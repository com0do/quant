"""
JoinQuant auto sign-in & credit claimer.

Uses Playwright headless Chromium to log in and claim daily credits.
Handles the sliding puzzle captcha via template matching (scipy).

Environment variables (set in GitHub Secrets):
  JQ_USERNAME  — JoinQuant phone number
  JQ_PASSWORD  — JoinQuant password

Usage:
  python scripts/jq_auto_claim.py
"""

from __future__ import annotations

import io
import os
import re
import time
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.signal import correlate2d


def solve_puzzle_captcha(page) -> bool:
    """
    Attempt to solve the JoinQuant sliding puzzle captcha.

    Extracts the background and slider images from the DOM, uses
    template matching to find the gap position, then slides.

    Returns True if captcha was solved (or not presented).
    """
    try:
        # Check if captcha is present
        captcha_visible = page.evaluate("""() => {
            const el = document.querySelector('.geetest_panel') || 
                       document.querySelector('#captcha') ||
                       document.querySelector('[class*="captcha"]') ||
                       document.querySelector('[class*="verify"]');
            return el ? el.offsetParent !== null : false;
        }""")
        if not captcha_visible:
            return True

        # Try image-based approach: look for xy_img and bg_img
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

    # Fallback: try to click through or skip
    try:
        close_btn = page.locator('[class*="close"], .geetest_panel_close, [aria-label="close"]').first
        if close_btn.is_visible(timeout=2000):
            close_btn.click()
            return True
    except Exception:
        pass

    return True  # Don't block on captcha failure


def _find_gap(bg_data_url: str, slider_data_url: str) -> int:
    """Use template matching to find the gap position."""
    try:
        bg_bytes = _data_url_to_bytes(bg_data_url)
        slider_bytes = _data_url_to_bytes(slider_data_url)

        bg = Image.open(io.BytesIO(bg_bytes)).convert("L")
        slider = Image.open(io.BytesIO(slider_bytes)).convert("L")

        bg_arr = np.array(bg, dtype=np.float64)
        slider_arr = np.array(slider, dtype=np.float64)

        # Normalize
        bg_arr = (bg_arr - bg_arr.mean()) / (bg_arr.std() + 1e-9)
        slider_arr = (slider_arr - slider_arr.mean()) / (slider_arr.std() + 1e-9)

        corr = correlate2d(bg_arr, slider_arr, mode="valid")
        y, x = np.unravel_index(np.argmax(corr), corr.shape)
        return int(x)
    except Exception:
        return -1


def _data_url_to_bytes(data_url: str) -> bytes:
    """Extract raw bytes from a data: URL."""
    import base64
    header, encoded = data_url.split(",", 1)
    return base64.b64decode(encoded)


def _perform_slide(page, gap_x: int) -> bool:
    """Perform the sliding action."""
    try:
        slider = page.locator(".geetest_slider_button, [class*='slider'], [class*='slide']").first
        if not slider.is_visible(timeout=2000):
            return False

        box = slider.bounding_box()
        if not box:
            return False

        start_x = box["x"] + box["width"] / 2
        start_y = box["y"] + box["height"] / 2

        # Human-like slide with easing
        page.mouse.move(start_x, start_y)
        page.mouse.down()

        # Move in steps with slight vertical wobble
        steps = 20
        for i in range(1, steps + 1):
            progress = i / steps
            # Ease-out cubic
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


def claim_credits(page) -> dict:
    """
    Navigate to the credits page and claim all available rewards.
    Returns a summary dict.
    """
    result = {"signed_in": False, "claimed": 0, "errors": []}

    try:
        page.goto("https://www.joinquant.com/view/user/floor?type=creditsdesc", timeout=30000)
        page.wait_for_load_state("networkidle", timeout=15000)
        time.sleep(3)

        # Click the main sign-in button
        sign_btns = page.locator('text=签到, button:has-text("签到"), [class*="sign"], [class*="checkin"]')
        count = sign_btns.count()
        for i in range(count):
            try:
                btn = sign_btns.nth(i)
                if btn.is_visible():
                    btn.click()
                    result["signed_in"] = True
                    time.sleep(2)
                    break
            except Exception:
                continue

        # Click "立即领取" buttons for each task
        claim_btns = page.locator('text=立即领取, button:has-text("领取"), [class*="claim"]')
        claim_count = claim_btns.count()
        for i in range(claim_count):
            try:
                btn = claim_btns.nth(i)
                if btn.is_visible():
                    btn_text = btn.inner_text()
                    btn.click()
                    result["claimed"] += 1
                    print(f"  Claimed: {btn_text}")
                    time.sleep(1.5)
            except Exception as e:
                result["errors"].append(str(e))

        # Handle "浏览社区文章" task: open article, wait 35s, go back
        try:
            article_links = page.locator('a[href*="post"], a[href*="article"]').first
            if article_links.is_visible(timeout=3000):
                article_links.click()
                time.sleep(5)
                page.evaluate("window.scrollBy(0, 800)")
                time.sleep(5)
                page.evaluate("window.scrollBy(0, -400)")
                time.sleep(25)
                page.go_back()
                time.sleep(2)
                # Try claiming again
                claim_again = page.locator('text=立即领取').first
                if claim_again.is_visible(timeout=3000):
                    claim_again.click()
                    result["claimed"] += 1
                    print("  Claimed: browse article reward")
                    time.sleep(1.5)
        except Exception as e:
            result["errors"].append(f"article: {e}")

    except Exception as e:
        result["errors"].append(f"credits page: {e}")

    return result


def run_claim() -> dict:
    """Main entry: login and claim credits."""
    username = os.getenv("JQ_USERNAME", "")
    password = os.getenv("JQ_PASSWORD", "")

    if not username or not password:
        return {"error": "JQ_USERNAME or JQ_PASSWORD not set"}

    from playwright.sync_api import sync_playwright

    result = {"signed_in": False, "claimed": 0, "login_ok": False, "errors": []}

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
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()

        try:
            # Step 1: Login
            print("[JQ] Navigating to login page...")
            page.goto("https://www.joinquant.com/user/login/index", timeout=30000)
            page.wait_for_load_state("networkidle", timeout=15000)
            time.sleep(2)

            # Fill credentials
            page.fill('input[name="username"]', username)
            page.fill('input[name="pwd"]', password)
            time.sleep(0.5)

            # Check agreement box
            try:
                agree = page.locator("#agreementBox")
                if agree.is_visible(timeout=2000) and not agree.is_checked():
                    agree.check()
            except Exception:
                pass

            time.sleep(0.5)

            # Submit
            page.click("button.btnPwdSubmit")
            time.sleep(3)

            # Handle captcha if present
            solve_puzzle_captcha(page)
            time.sleep(2)

            # Wait for login to complete (URL should change)
            page.wait_for_load_state("networkidle", timeout=15000)
            current_url = page.url
            print(f"[JQ] Post-login URL: {current_url}")
            result["login_ok"] = "/user/login" not in current_url

            if result["login_ok"]:
                # Step 2: Claim credits
                print("[JQ] Claiming credits...")
                claim_result = claim_credits(page)
                result["signed_in"] = claim_result["signed_in"]
                result["claimed"] = claim_result["claimed"]
                result["errors"].extend(claim_result["errors"])
            else:
                result["errors"].append("Login failed — still on login page")

        except Exception as e:
            result["errors"].append(str(e))
        finally:
            browser.close()

    return result


if __name__ == "__main__":
    import json
    summary = run_claim()
    print(f"\n[JQ] Result: {json.dumps(summary, ensure_ascii=False)}")
