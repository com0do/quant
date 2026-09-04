"""
JoinQuant auto sign-in & credit claimer.

Uses Playwright headless Chromium to log in, read one community article and press the daily check-in, then reports
the credit balance before / after and the newest credit-history rows.
A login-page puzzle captcha is handled via template matching (scipy). The check-in button opens a slider captcha that
is not solved: the run prints "ACTION REQUIRED" and the check-in has to be done by hand.

Environment variables (repo .env, or CI secrets):
  JQ_USERNAME / JQ_PASSWORD

Exit codes:
  0 — login + claim ok (or claim partial but login ok)
  1 — hard failure (bad credentials, captcha, unexpected page)
  2 — region blocked / non-China IP (expected on GitHub-hosted runners)

Usage:
  python scripts/jq_auto_claim.py
"""

from __future__ import annotations

import io
import os
import re
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.signal import correlate2d

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


def solve_puzzle_captcha(page) -> bool:
    """Attempt to solve the JoinQuant sliding puzzle captcha."""
    try:
        captcha_visible = page.evaluate("""() => {
            const el = document.querySelector('.geetest_panel') ||
                       document.querySelector('#captcha') ||
                       document.querySelector('[class*="captcha"]') ||
                       document.querySelector('[class*="verify"]');
            return el ? el.offsetParent !== null : false;
        }""")
        if not captcha_visible:
            return True

        xy_data = page.evaluate("""() => {
            const xy = document.querySelector('#xy_img');
            if (!xy) return null;
            const canvas = document.createElement('canvas');
            canvas.width = xy.naturalWidth || xy.width;
            canvas.height = xy.naturalHeight || xy.height;
            const ctx = canvas.getContext('2d');
            ctx.drawImage(xy, 0, 0);
            return canvas.toDataURL('image/png');
        }""")

        bg_data = page.evaluate("""() => {
            const bg = document.querySelector('#bg_img');
            if (!bg) return null;
            const canvas = document.createElement('canvas');
            canvas.width = bg.naturalWidth || bg.width;
            canvas.height = bg.naturalHeight || bg.height;
            const ctx = canvas.getContext('2d');
            ctx.drawImage(bg, 0, 0);
            return canvas.toDataURL('image/png');
        }""")

        if xy_data and bg_data:
            gap = _find_gap(bg_data, xy_data)
            if gap > 0:
                return _perform_slide(page, gap)
    except Exception:
        pass

    try:
        close_btn = page.locator('[class*="close"], .geetest_panel_close, [aria-label="close"]').first
        if close_btn.is_visible(timeout=2000):
            close_btn.click()
            return True
    except Exception:
        pass

    return True


def _find_gap(bg_data_url: str, slider_data_url: str) -> int:
    try:
        bg_bytes = _data_url_to_bytes(bg_data_url)
        slider_bytes = _data_url_to_bytes(slider_data_url)

        bg = Image.open(io.BytesIO(bg_bytes)).convert("L")
        slider = Image.open(io.BytesIO(slider_bytes)).convert("L")

        bg_arr = np.array(bg, dtype=np.float64)
        slider_arr = np.array(slider, dtype=np.float64)

        bg_arr = (bg_arr - bg_arr.mean()) / (bg_arr.std() + 1e-9)
        slider_arr = (slider_arr - slider_arr.mean()) / (slider_arr.std() + 1e-9)

        corr = correlate2d(bg_arr, slider_arr, mode="valid")
        _, x = np.unravel_index(np.argmax(corr), corr.shape)
        return int(x)
    except Exception:
        return -1


def _data_url_to_bytes(data_url: str) -> bytes:
    import base64

    _, encoded = data_url.split(",", 1)
    return base64.b64decode(encoded)


def _perform_slide(page, gap_x: int) -> bool:
    try:
        slider = page.locator(".geetest_slider_button, [class*='slider'], [class*='slide']").first
        if not slider.is_visible(timeout=2000):
            return False

        box = slider.bounding_box()
        if not box:
            return False

        start_x = box["x"] + box["width"] / 2
        start_y = box["y"] + box["height"] / 2

        page.mouse.move(start_x, start_y)
        page.mouse.down()

        steps = 20
        for i in range(1, steps + 1):
            progress = i / steps
            eased = 1 - (1 - progress) ** 3
            x = start_x + gap_x * eased
            y = start_y + (np.sin(progress * 3) * 2)
            page.mouse.move(x, y)
            time.sleep(0.01 + 0.03 * (1 - progress))

        page.mouse.up()
        time.sleep(2)
        return True
    except Exception:
        return False


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
    """Click the daily check-in card's button: 'clicked', 'already' (button says done) or 'missing'."""
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
    # "jigsaw verification": the check-in is guarded by a slider captcha that is deliberately not solved; close it so
    # the modal does not block the rest of the page, and report that a manual check-in is needed.
    if any("拼图" in p for p in popups):
        close = page.locator(".el-dialog__headerbtn")
        for i in range(close.count()):
            if close.nth(i).is_visible():
                close.nth(i).click()
                time.sleep(1)
        print("[JQ] ACTION REQUIRED: the daily check-in shows a slider captcha; check in by hand on the credits page")
        return "captcha"
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
            solve_puzzle_captcha(page)
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
