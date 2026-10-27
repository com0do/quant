"""
JoinQuant auto sign-in & credit claimer.

Uses Playwright headless Chromium to log in, read one community article and press the daily check-in, then reports
the credit balance before / after and the newest credit-history rows.
JoinQuant guards login and the check-in with a jigsaw slider. The puzzle is reconstructed from the page and solved
with a masked image match when JQ_SOLVE_CAPTCHA=1; otherwise the run prints "ACTION REQUIRED" and stops at the slider.

Environment variables (repo .env, or CI secrets):
  JQ_USERNAME / JQ_PASSWORD
  JQ_SOLVE_CAPTCHA — "1" to solve the slider automatically (default 0: manual check-in)

Exit codes:
  0 — login + claim ok (or claim partial but login ok)
  1 — hard failure (bad credentials, captcha, unexpected page)
  2 — region blocked / non-China IP (expected on GitHub-hosted runners)

Usage:
  python scripts/jq_auto_claim.py
"""

from __future__ import annotations

import base64
import io
import json
import math
import os
import re
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

LOGIN_URLS = (
    "https://www.joinquant.com/user/login/index",
    "https://www.joinquant.com/user/login/",
)

# The Chinese strings in this file are literal text / selectors of the JoinQuant website (region-block page,
# phone-number placeholder, log-in / check-in / claim buttons) and must stay Chinese to match the page.
REGION_BLOCK_MARKERS = (
    "当前地区暂不支持访问",
    "Service Unavailable in Your Region",
    "不支持来自非中国大陆",
    "does not support access from IP addresses outside Mainland China",
)

USERNAME_SELECTORS = (
    'input[name="username"]',
    'input[name="phone"]',
    'input[type="tel"]',
    'input[placeholder*="手机"]',
    'input[id*="username"]',
    'input[id*="phone"]',
)

PASSWORD_SELECTORS = (
    'input[name="pwd"]',
    'input[name="password"]',
    'input[type="password"]',
)

SUBMIT_SELECTORS = (
    "button.btnPwdSubmit",
    'button:has-text("登录")',
    'button[type="submit"]',
)


def _credentials() -> tuple[str, str]:
    user = os.getenv("JQ_USERNAME") or ""
    pw = os.getenv("JQ_PASSWORD") or ""
    return user, pw


def _page_text(page) -> str:
    try:
        return page.content()
    except Exception:
        return ""


def _detect_region_block(page) -> bool:
    text = _page_text(page)
    return any(m in text for m in REGION_BLOCK_MARKERS)


def _save_debug(page, prefix: str = "jq_login") -> list[str]:
    """Save screenshot + HTML for CI artifact upload."""
    out_dir = Path(os.getenv("JQ_DEBUG_DIR", "output"))
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    try:
        png = out_dir / f"{prefix}.png"
        page.screenshot(path=str(png), full_page=True)
        paths.append(str(png))
        print(f"[JQ] debug screenshot: {png}")
    except Exception as exc:
        print(f"[JQ] screenshot failed: {exc}")
    try:
        html = out_dir / f"{prefix}.html"
        html.write_text(_page_text(page), encoding="utf-8")
        paths.append(str(html))
        print(f"[JQ] debug html: {html}")
    except Exception as exc:
        print(f"[JQ] html dump failed: {exc}")
    return paths


def _first_visible(page, selectors: tuple[str, ...], timeout_ms: int = 8000):
    from playwright.sync_api import TimeoutError as PwTimeout

    for sel in selectors:
        loc = page.locator(sel).first
        try:
            loc.wait_for(state="visible", timeout=timeout_ms)
            return loc, sel
        except PwTimeout:
            continue
    return None, ""


CAPTCHA_ROOT = "#yth_captchar"
CAPTCHA_DRAG = "#slideVerifyDragControl"
CAPTCHA_HANDLE = ".valid-code__drag-handle"


def _captcha_enabled() -> bool:
    """The slider is solved only when the operator opts in (JQ_SOLVE_CAPTCHA=1).

    JoinQuant guards both login and the daily check-in with a jigsaw slider. Leaving this off keeps the previous
    behaviour (stop, log ACTION REQUIRED) and keeps the account out of anything the site may treat as automation;
    the Gitee Go pipeline and the home-node unit set it to 1 explicitly.
    """
    return os.getenv("JQ_SOLVE_CAPTCHA", "0").strip().lower() not in ("", "0", "false", "no", "off")


def _captcha_visible(page) -> bool:
    for sel in (CAPTCHA_ROOT, CAPTCHA_DRAG):
        try:
            if page.locator(sel).first.is_visible(timeout=1000):
                return True
        except Exception:
            continue
    return False


def _data_url_bytes(value: str) -> bytes | None:
    m = re.search(r"url\(['\"]?(.*?)['\"]?\)", value or "", re.S)
    if not m or not m.group(1).startswith("data:"):
        return None
    try:
        return base64.b64decode(m.group(1).split(",", 1)[1])
    except Exception:
        return None


def _parse_px(value: str) -> tuple[int, int] | None:
    nums = re.findall(r"(-?\d+(?:\.\d+)?)px", value or "")
    if len(nums) < 2:
        return None
    return int(float(nums[0])), int(float(nums[1]))


def _read_captcha(page) -> dict | None:
    """Read the live captcha from the DOM: tile background offsets, background image, piece image and its top.

    The login widget tiles the background into 11x71 strips; the check-in widget is the same template but may not
    tile it. Both shapes are read here, and same-origin iframes are searched too.
    """
    try:
        return page.evaluate(
            """() => {
                const frames = [];
                const docs = [document];
                for (const f of document.querySelectorAll('iframe')) {
                    frames.push(f.src || '');
                    try { if (f.contentDocument) docs.push(f.contentDocument); } catch (e) {}
                }
                const find = (sel) => {
                    for (const d of docs) { const el = d.querySelector(sel); if (el) return el; }
                    return null;
                };
                const root = find('#yth_captchar') || find('.valid-code');
                const drag = find('#slideVerifyDragControl') || find('.valid-code__drag');
                const piece = find('#xy_img') || find('.valid-code__hq');
                if (!root || !drag || !piece) {
                    return {error: 'missing', root: !!root, drag: !!drag, piece: !!piece, frames};
                }
                const css = (el, p) => getComputedStyle(el)[p] || '';
                let bgCandidates = [];
                for (const el of [root, ...root.querySelectorAll('*')]) {
                    if (el === piece) continue;
                    const s = el.style.backgroundImage || css(el, 'backgroundImage');
                    if (s && s !== 'none') {
                        bgCandidates.push({tag: el.tagName, id: el.id,
                                           cls: String(el.className).slice(0, 60), bg: s,
                                           pos: el.style.backgroundPosition || css(el, 'backgroundPosition'),
                                           w: el.offsetWidth, h: el.offsetHeight});
                    }
                    if (el.tagName === 'IMG' && el.src) {
                        bgCandidates.push({tag: 'IMG', id: el.id,
                                           cls: String(el.className).slice(0, 60),
                                           bg: 'url("' + el.src + '")',
                                           pos: '', w: el.offsetWidth, h: el.offsetHeight});
                    }
                    if (el.tagName === 'CANVAS') {
                        try {
                            bgCandidates.push({tag: 'CANVAS', id: el.id,
                                               cls: String(el.className).slice(0, 60),
                                               bg: el.toDataURL('image/png'),
                                               pos: '', w: el.offsetWidth, h: el.offsetHeight});
                        } catch (e) {}
                    }
                }
                let tiles = [...root.querySelectorAll('.valid-code__div-item')].map(el => ({
                    bg: el.style.backgroundImage || css(el, 'backgroundImage'),
                    pos: el.style.backgroundPosition || css(el, 'backgroundPosition'),
                    w: el.offsetWidth, h: el.offsetHeight,
                }));
                if (!tiles.length) {
                    tiles = bgCandidates.filter(c => c.pos && /-[0-9]/.test(c.pos) && c.w > 0 && c.h > 0);
                }
                let bg = '';
                if (tiles.length) bg = tiles[0].bg;
                if (!bg || bg === 'none') {
                    const pick = bgCandidates.find(c => c.bg.indexOf('data:image') >= 0
                                                     || c.bg.indexOf('http') >= 0);
                    if (pick) bg = pick.bg;
                }
                const pieceBg = piece.style.backgroundImage || css(piece, 'backgroundImage')
                    || (piece.tagName === 'IMG' ? 'url("' + piece.src + '")' : '');
                return {
                    error: null, frames, rootTag: root.tagName, rootId: root.id,
                    rootCls: String(root.className).slice(0, 80), tileCount: tiles.length,
                    bgW: root.clientWidth, bgH: root.clientHeight,
                    blockW: tiles.length ? tiles[0].w : 0,
                    blockH: tiles.length ? tiles[0].h : 0,
                    positions: tiles.map(t => t.pos),
                    bg, pieceBg, bgCandidates: bgCandidates.slice(0, 12),
                    pieceTop: parseInt(piece.style.top || css(piece, 'top'), 10) || 0,
                };
            }"""
        )
    except Exception as exc:
        return {"error": f"evaluate failed: {exc}"}


def _captcha_dom_summary(page) -> dict:
    try:
        return page.evaluate(
            """() => {
                const out = {url: location.href, frames: [], nodes: []};
                for (const f of document.querySelectorAll('iframe')) out.frames.push(f.src || '');
                const docs = [document];
                for (const f of document.querySelectorAll('iframe')) {
                    try { if (f.contentDocument) docs.push(f.contentDocument); } catch (e) {}
                }
                const sels = ['#yth_captchar', '#xy_img', '#slideVerifyDragControl', '#bg_img', '.valid-code',
                              '.valid-code__hq', '.valid-code__drag', '.valid-code__drag-handle'];
                for (const doc of docs) {
                    for (const sel of sels) {
                        const el = doc.querySelector(sel);
                        if (el) out.nodes.push({sel, tag: el.tagName, id: el.id,
                                                cls: String(el.className).slice(0, 80),
                                                vis: el.offsetParent !== null});
                    }
                    for (const el of doc.querySelectorAll('[id], [class]')) {
                        const s = (el.id + ' ' + String(el.className)).toLowerCase();
                        if (!/valid|captcha|slide|verify|drag|puzzle|jigsaw/.test(s)) continue;
                        out.nodes.push({tag: el.tagName, id: el.id, cls: String(el.className).slice(0, 80),
                                        text: (el.innerText || '').slice(0, 60), vis: el.offsetParent !== null});
                        if (out.nodes.length > 50) break;
                    }
                }
                return out;
            }"""
        )
    except Exception as exc:
        return {"error": str(exc)}


def captcha_gap(data: dict) -> int | None:
    """Distance the piece has to travel, from the tile permutation and the two images.

    The server sends the background as 11x71 strips in shuffled order together with the source offset of each
    strip (point). Reassembling the strips gives the picture the user sees; the piece is the matching cut-out.
    A masked sum of squared differences at the piece's fixed vertical offset is exact (the gap is a real cut-out),
    so no template heuristics or browser plugins are needed.
    """
    bg_bytes = _data_url_bytes(data.get("bg", ""))
    piece_bytes = _data_url_bytes(data.get("pieceBg", ""))
    positions = [_parse_px(p) for p in data.get("positions") or []]
    if not bg_bytes or not piece_bytes or (positions and any(p is None for p in positions)):
        print(f"[JQ] captcha: images missing bg={bool(bg_bytes)} piece={bool(piece_bytes)} tiles={len(positions)}")
        return None
    try:
        bg = Image.open(io.BytesIO(bg_bytes)).convert("RGB")
        piece = Image.open(io.BytesIO(piece_bytes)).convert("RGBA")
        bg_w, bg_h = bg.size
        block_w, block_h = int(data.get("blockW") or 0), int(data.get("blockH") or 0)
        display = bg
        if positions and block_w > 0 and block_h > 0:
            cols = max(1, bg_w // block_w)
            display = Image.new("RGB", (bg_w, bg_h))
            for i, (sx, sy) in enumerate(positions):
                row, col = divmod(i, cols)
                left, top = -sx, -sy
                tile = bg.crop((left, top, left + block_w, top + block_h))
                display.paste(tile, (col * block_w, row * block_h))

        piece_rgb = np.asarray(piece.convert("RGB"), dtype=np.int16)
        mask = np.asarray(piece.getchannel("A")) > 200
        if not mask.any():
            mask = np.ones(piece_rgb.shape[:2], dtype=bool)
        height, width = piece_rgb.shape[:2]
        if height > bg_h or width > bg_w:
            print(f"[JQ] captcha: piece {width}x{height} larger than background {bg_w}x{bg_h}")
            return None
        display_rgb = np.asarray(display, dtype=np.int16)
        n = max(1, int(mask.sum()))

        def best_at(top: int) -> tuple[int, int]:
            best_s, best_x = float("inf"), -1
            for x in range(0, bg_w - width + 1):
                patch = display_rgb[top:top + height, x:x + width]
                diff = (patch - piece_rgb)[mask].astype(np.int32)
                score = int((diff * diff).sum())
                if score < best_s:
                    best_s, best_x = score, x
            return best_s, best_x

        top = max(0, min(int(data.get("pieceTop") or 0), bg_h - height))
        score, x = best_at(top)
        if x < 0 or score / n > 50:
            for yy in range(0, bg_h - height + 1, 2):
                if abs(yy - top) < 2:
                    continue
                s, xx = best_at(yy)
                if xx >= 0 and s < score:
                    score, x, top = s, xx, yy
        if x >= 0:
            print(f"[JQ] captcha: bg={bg.size}, piece={piece.size}, gap={x}, top={top}, mse={score / n:.1f}")
            return x
        return None
    except Exception as exc:
        print(f"[JQ] captcha image analysis failed: {exc}")
        return None


def _drag_piece(page, gap: int) -> bool:
    handle = page.locator(CAPTCHA_HANDLE).first
    try:
        handle.wait_for(state="visible", timeout=5000)
        box = handle.bounding_box()
    except Exception:
        return False
    if not box:
        return False
    start_x = box["x"] + box["width"] / 2
    start_y = box["y"] + box["height"] / 2
    page.mouse.move(start_x, start_y)
    page.mouse.down()
    steps = max(24, min(60, abs(gap) // 4 + 24))
    for i in range(1, steps + 1):
        progress = i / steps
        eased = 1 - (1 - progress) ** 3
        page.mouse.move(start_x + gap * eased, start_y + math.sin(progress * math.pi * 3) * 1.5)
        time.sleep(0.008 + 0.012 * (1 - progress))
    page.mouse.move(start_x + gap, start_y)
    page.mouse.up()
    return True


def _captcha_result(page, timeout: float = 8.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            cls = page.locator(CAPTCHA_DRAG).first.get_attribute("class") or ""
        except Exception:
            cls = ""
        if "success" in cls:
            return True
        if "error" in cls:
            return False
        if not _captcha_visible(page):
            return True
        time.sleep(0.25)
    return False


def solve_slider_captcha(page, attempts: int = 3) -> bool:
    """Solve the JoinQuant jigsaw slider (caller must have checked _captcha_enabled)."""
    if not _captcha_visible(page):
        return False
    first_fail = True
    for attempt in range(1, attempts + 1):
        data = _read_captcha(page)
        if not data or data.get("error"):
            print(f"[JQ] captcha attempt {attempt}: {json.dumps(data, ensure_ascii=False)[:1200]}")
            if first_fail:
                print(f"[JQ] captcha DOM: {json.dumps(_captcha_dom_summary(page), ensure_ascii=False)[:3000]}")
                _save_debug(page, "jq_captcha")
                first_fail = False
            time.sleep(1.0)
            continue
        gap = captcha_gap(data)
        if gap is None:
            print(f"[JQ] captcha attempt {attempt}: could not locate the gap "
                  f"(tiles={data.get('tileCount')}, bg={len(data.get('bg') or '')}, piece={len(data.get('pieceBg') or '')})")
            if first_fail:
                print(f"[JQ] captcha DOM: {json.dumps(_captcha_dom_summary(page), ensure_ascii=False)[:3000]}")
                _save_debug(page, "jq_captcha")
                first_fail = False
            time.sleep(1.0)
            continue
        delta = (0, -6, 6, -11, 11)[(attempt - 1) % 5]
        drag = max(0, gap + delta)
        print(f"[JQ] captcha attempt {attempt}: gap={gap} delta={delta} drag={drag}px")
        print(f"[JQ] captcha: root={data.get('rootTag')}#{data.get('rootId')} tiles={data.get('tileCount')} "
              f"bg_len={len(data.get('bg') or '')} piece_len={len(data.get('pieceBg') or '')}")
        for c in (data.get("bgCandidates") or []):
            print(f"[JQ] captcha candidate: {c.get('tag')} id={c.get('id')} cls={c.get('cls')} "
                  f"len={len(c.get('bg') or '')} pos={c.get('pos')} w={c.get('w')} h={c.get('h')}")
        if not _drag_piece(page, drag):
            print(f"[JQ] captcha attempt {attempt}: drag handle not found")
            time.sleep(1.0)
            continue
        if _captcha_result(page):
            print(f"[JQ] captcha solved on attempt {attempt}")
            return True
        print(f"[JQ] captcha attempt {attempt}: rejected, waiting for a new puzzle")
        time.sleep(1.5)
        if not _captcha_visible(page):
            return True
    return False


def _close_captcha_dialog(page) -> None:
    for sel in (".bootstrap-dialog .close", ".el-dialog__headerbtn", "#slideVerifyDragControl .close"):
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=1000):
                loc.click()
                time.sleep(0.5)
                return
        except Exception:
            continue
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass



# Visible short elements whose text mentions check-in / claim / credits / tasks (JoinQuant UI words, so Chinese).
_DESCRIBE_JS = """() => {
  const re = /签到|领取|积分|任务|完成/;
  const out = [];
  for (const el of document.querySelectorAll('body *')) {
    if (el.children.length > 2) continue;
    const t = (el.innerText || '').trim().replace(/\\s+/g, ' ');
    if (!t || t.length > 40 || !re.test(t)) continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    out.push(el.tagName.toLowerCase() + '.' + String(el.className || '').slice(0, 40) + ' | ' + t);
    if (out.length >= 40) break;
  }
  return out;
}"""


def _describe_credits_page(page) -> None:
    print(f"[JQ] Credits page: {page.url} title={page.title()!r}")
    try:
        lines = page.evaluate(_DESCRIBE_JS)
    except Exception as exc:
        lines = [f"describe failed: {exc}"]
    for line in lines:
        print(f"[JQ]   {line}")


CREDITS_URL = "https://www.joinquant.com/view/user/floor?type=creditsdesc"

# Texts on JoinQuant's credits page: "available credits: N", the daily check-in card ("check in daily for 1 credit"),
# the browse-articles card and its "go and see" button, the "already ..." prefix of done buttons, and the
# "view records" link to the credit history.
_AVAILABLE_RE = re.compile(r"可用积分[:：]\s*(\d+)")
_CHECKIN_CARD = "每天签到"
_ARTICLE_CARD = "浏览社区文章"
_ARTICLE_GO = "去看看"
_DONE_PREFIX = "已"


def _open_credits(page) -> None:
    page.goto(CREDITS_URL, timeout=30000)
    page.wait_for_load_state("domcontentloaded", timeout=20000)
    time.sleep(3)


def _available_credits(page) -> int | None:
    m = _AVAILABLE_RE.search(page.inner_text("body"))
    return int(m.group(1)) if m else None


def _task_card(page, text: str):
    return page.locator(".floor-credit-card").filter(has_text=text)


def _popup_texts(page) -> list[str]:
    texts = []
    for sel in (".el-message", ".el-message-box", ".el-dialog", ".el-notification", "[role='dialog']", "[role='alert']"):
        loc = page.locator(sel)
        for i in range(min(loc.count(), 3)):
            try:
                if loc.nth(i).is_visible():
                    texts.append(" ".join(loc.nth(i).inner_text().split())[:120])
            except Exception:
                continue
    return texts


def _check_in(page) -> str:
    """Click the daily check-in card's button: 'clicked', 'already' (button says done), 'captcha' or 'missing'."""
    card = _task_card(page, _CHECKIN_CARD)
    btn = card.locator("button").first
    if btn.count() == 0 or not btn.is_visible():
        return "missing"
    print(f"[JQ] Check-in card: {' '.join(card.inner_text().split())!r}, disabled={btn.is_disabled()}")
    if btn.inner_text().strip().startswith(_DONE_PREFIX) or btn.is_disabled():
        return "already"
    btn.click()
    time.sleep(2)
    popups = _popup_texts(page)
    print(f"[JQ] After check-in click: card={' '.join(card.inner_text().split())!r} popups={popups}")
    if any("拼图" in p for p in popups) or _captcha_visible(page):
        if not _captcha_enabled():
            _close_captcha_dialog(page)
            print("[JQ] ACTION REQUIRED: the daily check-in shows a slider captcha; set JQ_SOLVE_CAPTCHA=1 "
                  "to solve it automatically, or check in by hand on the credits page")
            return "captcha"
        if not solve_slider_captcha(page):
            _close_captcha_dialog(page)
            print("[JQ] ACTION REQUIRED: the slider could not be solved; check in by hand on the credits page")
            return "captcha"
        time.sleep(2)
        card = _task_card(page, _CHECKIN_CARD)
        btn = card.locator("button").first
        if btn.count() and (btn.inner_text().strip().startswith(_DONE_PREFIX) or btn.is_disabled()):
            print(f"[JQ] Check-in card after captcha: {' '.join(card.inner_text().split())!r}")
        return "clicked"
    return "clicked"


def _credit_history(page, rows: int = 4) -> None:
    """Print the newest rows of the credit history ("view records" link on the credits page)."""
    link = page.locator("a").filter(has_text="查看记录").first
    if link.count() == 0:
        print("[JQ] Credit history link not found")
        return
    href = link.get_attribute("href") or ""
    if href and not href.startswith("javascript"):
        page.goto(href if href.startswith("http") else "https://www.joinquant.com" + href, timeout=30000)
    else:
        link.click()
    time.sleep(3)
    print(f"[JQ] Credit history: {page.url}")
    lines = [ln for ln in page.inner_text("body").splitlines() if re.search(r"20\d\d[-/.]\d\d[-/.]\d\d", ln)]
    for ln in lines[:rows]:
        print(f"[JQ]   {' '.join(ln.split())[:120]}")


def _click_and_follow(page, locator):
    """Click and return the page that shows the result (a new tab, or the same tab after it navigates)."""
    before = page.url
    try:
        with page.context.expect_page(timeout=8000) as new_tab:
            locator.click()
        target = new_tab.value
    except Exception:
        target = page
        try:
            page.wait_for_url(lambda u: u != before, timeout=10000)
        except Exception:
            pass
    target.wait_for_load_state("domcontentloaded", timeout=20000)
    time.sleep(3)
    return target


# Article titles on the community list are click handlers, not links: pick clickable short texts below the header
# that are not author links.
_TITLE_CANDIDATES_JS = """() => {
  const out = [];
  for (const el of document.querySelectorAll('body *')) {
    if (el.children.length > 1 || el.closest('a[href*="/user/"]')) continue;
    const t = (el.innerText || '').trim();
    if (t.length < 10 || t.length > 80) continue;
    if (getComputedStyle(el).cursor !== 'pointer') continue;
    const r = el.getBoundingClientRect();
    if (r.width < 120 || r.height === 0 || r.y < 150 || r.y > window.innerHeight) continue;
    out.push([Math.round(r.x + Math.min(r.width / 2, 60)), Math.round(r.y + r.height / 2), t.slice(0, 30)]);
    if (out.length >= 5) break;
  }
  return out;
}"""


def _open_first_article(listing):
    """Click title candidates until one opens an article (new tab or same-tab navigation); None if none does."""
    try:
        listing.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    for x, y, text in listing.evaluate(_TITLE_CANDIDATES_JS):
        before = listing.url
        try:
            with listing.context.expect_page(timeout=6000) as new_tab:
                listing.mouse.click(x, y)
            target = new_tab.value
        except Exception:
            target = listing
            try:
                listing.wait_for_url(lambda u: u != before, timeout=6000)
            except Exception:
                pass
        target.wait_for_load_state("domcontentloaded", timeout=20000)
        print(f"[JQ] Clicked title {text!r} -> {target.url}")
        if "/detail" in target.url or "/post/" in target.url:
            time.sleep(3)
            return target
        if target is not listing:
            target.close()
    return None


def _browse_article(page) -> str:
    """Follow the browse-articles card to the community list, open one article and read it for about a minute."""
    go = _task_card(page, _ARTICLE_CARD).locator("button, a").filter(has_text=_ARTICLE_GO).first
    if go.count() == 0 or not go.is_visible():
        return "missing"
    listing = _click_and_follow(page, go)
    print(f"[JQ] After '{_ARTICLE_GO}': {listing.url}")
    article = listing if "/detail" in listing.url else _open_first_article(listing)
    if article is None:
        return f"no article opened from {listing.url}"
    print(f"[JQ] Reading article: {article.url} popups={_popup_texts(article)}")
    for _ in range(12):
        article.mouse.wheel(0, 500)
        time.sleep(5)
    print(f"[JQ] Done reading, popups={_popup_texts(article)}")
    for p in {id(x): x for x in (article, listing)}.values():
        if p is not page:
            p.close()
    return "read"


def claim_credits(page) -> dict:
    """Read one article, then press the daily check-in; the outcome is judged by the available-credits counter."""
    result = {"signed_in": False, "claimed": 0, "errors": []}
    try:
        _open_credits(page)
        if _detect_region_block(page):
            result["errors"].append("credits page region blocked")
            return result
        before = _available_credits(page)
        try:
            result["article"] = _browse_article(page)
        except Exception as exc:
            result["article"] = "error"
            result["errors"].append(f"article: {exc}")
        _open_credits(page)
        result["check_in"] = _check_in(page)
        if result["check_in"] == "missing":
            _describe_credits_page(page)
        _open_credits(page)
        after = _available_credits(page)
        _save_debug(page, "jq_credits")
        result.update(credits_before=before, credits_after=after)
        result["signed_in"] = result["check_in"] == "already" or (result["check_in"] == "clicked" and after != before)
        if before is not None and after is not None:
            result["claimed"] = after - before
        print(f"[JQ] Check-in: {result['check_in']}, article: {result['article']}, credits {before} -> {after}")
        try:
            _credit_history(page)
        except Exception as exc:
            print(f"[JQ] Credit history failed: {exc}")
    except Exception as exc:
        result["errors"].append(f"credits page: {exc}")
    return result


def _login(page, username: str, password: str) -> dict:
    """Open login page, fill form, submit. Returns partial result dict."""
    out: dict = {"login_ok": False, "errors": [], "region_blocked": False, "debug_files": []}

    last_url = ""
    for url in LOGIN_URLS:
        print(f"[JQ] Navigating to login: {url}")
        try:
            page.goto(url, timeout=30000, wait_until="domcontentloaded")
            time.sleep(2)
            last_url = page.url
            print(f"[JQ] Landed on: {last_url}")

            if _detect_region_block(page):
                out["region_blocked"] = True
                out["errors"].append(
                    "JoinQuant blocks non-Mainland-China IPs. "
                    "Use home data node (deploy/home_data_node) or a China self-hosted runner."
                )
                out["debug_files"] = _save_debug(page, "jq_region_block")
                return out

            # fund.joinquant.com is the private-fund website, not the login page
            if "fund.joinquant.com" in last_url and "login" not in last_url:
                out["errors"].append(f"unexpected redirect to fund portal: {last_url}")
                continue

            user_loc, user_sel = _first_visible(page, USERNAME_SELECTORS)
            pwd_loc, pwd_sel = _first_visible(page, PASSWORD_SELECTORS)
            if not user_loc or not pwd_loc:
                out["errors"].append(f"login form not found at {last_url}")
                continue

            print(f"[JQ] Using selectors user={user_sel} pwd={pwd_sel}")
            user_loc.fill(username)
            pwd_loc.fill(password)
            time.sleep(0.5)

            try:
                agree = page.locator("#agreementBox")
                if agree.is_visible(timeout=2000) and not agree.is_checked():
                    agree.check()
            except Exception:
                pass

            submitted = False
            for sel in SUBMIT_SELECTORS:
                btn = page.locator(sel).first
                try:
                    if btn.is_visible(timeout=2000):
                        btn.click()
                        submitted = True
                        print(f"[JQ] Clicked submit: {sel}")
                        break
                except Exception:
                    continue
            if not submitted:
                out["errors"].append("submit button not found")
                continue

            time.sleep(3)
            if _captcha_visible(page):
                if _captcha_enabled():
                    solve_slider_captcha(page)
                else:
                    print("[JQ] login captcha visible; set JQ_SOLVE_CAPTCHA=1 to solve it automatically")
            time.sleep(2)

            try:
                page.wait_for_load_state("domcontentloaded", timeout=15000)
            except Exception:
                pass

            current_url = page.url
            print(f"[JQ] Post-login URL: {current_url}")
            out["login_ok"] = "/user/login" not in current_url and "login" not in current_url.lower()
            if out["login_ok"]:
                return out

            out["errors"].append(f"still on login page: {current_url}")
        except Exception as exc:
            out["errors"].append(f"{url}: {exc}")

    if not out["debug_files"]:
        out["debug_files"] = _save_debug(page, "jq_login_fail")
    return out


def run_claim() -> dict:
    """Main entry: login and claim credits."""
    username, password = _credentials()
    if not username or not password:
        return {
            "error": "JQ_USERNAME/JQ_PASSWORD not set",
            "login_ok": False,
            "signed_in": False,
            "claimed": 0,
            "errors": [],
        }

    from playwright.sync_api import sync_playwright

    result: dict = {
        "signed_in": False,
        "claimed": 0,
        "login_ok": False,
        "region_blocked": False,
        "errors": [],
        "debug_files": [],
    }

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--window-size=1280,800",
            ],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="zh-CN",
        )
        page = context.new_page()

        def _log_post(request):
            url = request.url.lower()
            if request.method == "POST" and ("verifycode" in url or "credit" in url or "checkin" in url):
                print(f"[JQ] POST {request.url} data={request.post_data}")

        page.on("request", _log_post)

        try:
            login_result = _login(page, username, password)
            result["login_ok"] = bool(login_result.get("login_ok"))
            result["region_blocked"] = bool(login_result.get("region_blocked"))
            result["errors"].extend(login_result.get("errors", []))
            result["debug_files"].extend(login_result.get("debug_files", []))

            if result["login_ok"]:
                print("[JQ] Claiming credits...")
                claim_result = claim_credits(page)
                result["errors"].extend(claim_result.pop("errors"))
                result.update(claim_result)
            elif not result["region_blocked"]:
                result["errors"].append("Login failed — could not reach authenticated session")

        except Exception as exc:
            result["errors"].append(str(exc))
            try:
                result["debug_files"].extend(_save_debug(page, "jq_exception"))
            except Exception:
                pass
        finally:
            browser.close()

    return result


if __name__ == "__main__":
    import json

    summary = run_claim()
    print(f"\n[JQ] Result: {json.dumps(summary, ensure_ascii=False)}")
    if summary.get("region_blocked"):
        sys.exit(2)
    if summary.get("error") or not summary.get("login_ok"):
        sys.exit(1)
    sys.exit(0)
