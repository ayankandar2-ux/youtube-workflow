#!/usr/bin/env python3
"""
Facebook Headless Automation Uploader
Uploads Reels/Videos directly to Facebook personal accounts or pages using session cookies without Graph API.
"""

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional, Dict, List

FACEBOOK_COOKIES_PATH = Path("/root/.hermes/facebook_cookies.json")
FACEBOOK_COOKIES_FALLBACK = Path("/root/facebook_cookies.json")
FACEBOOK_TEXT_COOKIE_PATHS = [
    Path("/root/Facebook cookies.txt"),
    Path("/root/Facebook"),
    Path("/root/facebook_cookies.txt"),
    Path("/root/facebook cookies.txt"),
    Path("/root/youtube-workflow/facebook_cookies.txt"),
]
SCREENSHOTS_DIR = Path("/root/youtube-workflow/thumbnails/fb_screenshots")
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"


def parse_and_save_facebook_cookies(raw_data: str) -> bool:
    """Parse cookies from JSON, cookies.txt (Netscape), or key-value format and save them."""
    raw = raw_data.strip()
    cookies = []

    # Strip any accidental leading characters like 'o# Netscape'
    if raw.startswith("o# Netscape"):
        raw = raw[1:]

    # 1. JSON format (Cookie-Editor, EditThisCookie, etc.)
    if raw.startswith("[") or raw.startswith("{"):
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                data = [data]
            for item in data:
                name = item.get("name")
                val = item.get("value")
                domain = item.get("domain", ".facebook.com")
                path = item.get("path", "/")
                same_site = item.get("sameSite", "Lax")
                if same_site not in ["Strict", "Lax", "None"]:
                    same_site = "Lax"
                if name and val:
                    cookies.append({
                        "name": str(name),
                        "value": str(val),
                        "domain": str(domain),
                        "path": str(path),
                        "sameSite": same_site
                    })
        except Exception:
            pass

    # 2. Netscape cookies.txt format
    if not cookies and ("# Netscape" in raw or "\t" in raw):
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) >= 7:
                domain, flag, path, secure, expiration, name, value = parts[:7]
                domain_l = domain.lower()
                name_l = name.lower()
                # Filter specifically for Facebook / Meta cookies or known session tokens
                if ("facebook.com" in domain_l or "fb.com" in domain_l or "meta.com" in domain_l or
                        name_l in ["c_user", "xs", "datr", "fr", "sb", "wd", "presence", "spin", "dpr"]):
                    if not domain.startswith("."):
                        domain = "." + domain
                    cookies.append({
                        "name": name,
                        "value": value,
                        "domain": domain,
                        "path": path,
                        "sameSite": "Lax"
                    })

    # 3. Cookie header string (c_user=...; xs=...)
    if not cookies and ("c_user=" in raw or "xs=" in raw or ";" in raw or "=" in raw):
        for part in raw.split(";"):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                k = k.strip()
                v = v.strip()
                if k:
                    cookies.append({
                        "name": k,
                        "value": v,
                        "domain": ".facebook.com",
                        "path": "/",
                        "sameSite": "Lax"
                    })

    if not cookies:
        return False

    # Check that essential Facebook authentication cookies exist
    names = {c.get("name") for c in cookies}
    if "c_user" not in names or "xs" not in names:
        # If c_user or xs is missing, we don't have an authenticated Facebook session
        return False

    # Save to both paths
    FACEBOOK_COOKIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    FACEBOOK_COOKIES_PATH.write_text(json.dumps(cookies, indent=2))
    try:
        FACEBOOK_COOKIES_FALLBACK.write_text(json.dumps(cookies, indent=2))
    except Exception:
        pass

    return True


def get_facebook_cookies() -> List[Dict]:
    """Load stored Facebook session cookies from JSON or text cookie files."""
    for p in [FACEBOOK_COOKIES_PATH, FACEBOOK_COOKIES_FALLBACK]:
        if p.exists():
            try:
                cookies = json.loads(p.read_text())
                if isinstance(cookies, list) and len(cookies) > 0:
                    names = {c.get("name") for c in cookies}
                    if "c_user" in names and "xs" in names:
                        cleaned = []
                        for c in cookies:
                            d = str(c.get("domain", ".facebook.com"))
                            if not d.startswith("."):
                                d = "." + d
                            cleaned.append({
                                "name": str(c["name"]),
                                "value": str(c["value"]),
                                "domain": d,
                                "path": str(c.get("path", "/")),
                                "sameSite": "Lax"
                            })
                        return cleaned
            except Exception:
                pass

    # Check text cookie files for automatic import
    for p in FACEBOOK_TEXT_COOKIE_PATHS:
        if p.exists():
            try:
                content = p.read_text(errors="ignore")
                if parse_and_save_facebook_cookies(content):
                    return get_facebook_cookies()
            except Exception:
                pass

    return []


FACEBOOK_ENV_PATH = Path("/root/facebook_api/.env")
FACEBOOK_CONFIG_JSON_PATH = Path("/root/facebook_config.json")
FACEBOOK_WORKFLOW_CONFIG_PATH = Path("/root/youtube-workflow/facebook_config.json")


def get_facebook_graph_credentials() -> Optional[Dict[str, str]]:
    """Retrieve Facebook Page ID and Page Access Token from environment file or JSON configs."""
    # 1. Check /root/facebook_api/.env
    if FACEBOOK_ENV_PATH.exists():
        try:
            content = FACEBOOK_ENV_PATH.read_text(encoding="utf-8")
            page_id = None
            token = None
            for line in content.splitlines():
                line = line.strip()
                if line.startswith("FB_PAGE_ID="):
                    page_id = line.split("=", 1)[1].strip().strip('"').strip("'")
                elif line.startswith("FB_PAGE_TOKEN="):
                    token = line.split("=", 1)[1].strip().strip('"').strip("'")
            if page_id and token:
                return {"page_id": page_id, "access_token": token}
        except Exception:
            pass

    # 2. Check JSON config files
    for p in [FACEBOOK_CONFIG_JSON_PATH, FACEBOOK_WORKFLOW_CONFIG_PATH]:
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                page_id = data.get("page_id") or data.get("FB_PAGE_ID")
                token = data.get("access_token") or data.get("FB_PAGE_TOKEN") or data.get("page_token")
                if page_id and token:
                    return {"page_id": str(page_id), "access_token": str(token)}
            except Exception:
                pass

    return None


def is_facebook_graph_configured() -> bool:
    """Return True if Graph API credentials (Page ID + Access Token) are available."""
    creds = get_facebook_graph_credentials()
    return creds is not None and bool(creds.get("page_id")) and bool(creds.get("access_token"))


def is_facebook_configured() -> bool:
    """Return True if either Graph API credentials or Session Cookies are configured."""
    return is_facebook_graph_configured() or is_facebook_cookies_configured()


def check_facebook_graph_api() -> Dict:
    """Verify Facebook Graph API Page token and return page details."""
    import requests
    creds = get_facebook_graph_credentials()
    if not creds:
        return {"configured": False, "valid": False, "error": "No Graph API credentials found."}

    page_id = creds["page_id"]
    token = creds["access_token"]

    try:
        url = f"https://graph.facebook.com/v20.0/{page_id}?fields=id,name,link&access_token={token}"
        r = requests.get(url, timeout=10)
        data = r.json()
        if r.status_code == 200 and "id" in data:
            return {
                "configured": True,
                "valid": True,
                "page_id": data.get("id"),
                "name": data.get("name"),
                "link": data.get("link", f"https://www.facebook.com/{page_id}")
            }
        else:
            err_msg = data.get("error", {}).get("message", "Unknown Graph API error")
            return {"configured": True, "valid": False, "error": err_msg}
    except Exception as e:
        return {"configured": True, "valid": False, "error": str(e)}


def upload_facebook_reel_graph_api(
    file_path: str,
    caption: str = "",
    title: str = "",
    wait_for_ready: bool = True,
    max_wait_sec: int = 45
) -> Dict:
    """
    Publish a Reel to a Facebook Page via the official Meta Graph API.
    Handles the 3-phase Reels upload protocol: start -> binary upload -> finish.
    """
    import requests
    creds = get_facebook_graph_credentials()
    if not creds:
        raise RuntimeError("Facebook Graph API is not configured (missing Page ID or Page Token).")

    p_file = Path(file_path)
    if not p_file.exists():
        raise FileNotFoundError(f"Video file not found: {file_path}")

    page_id = creds["page_id"]
    token = creds["access_token"]
    file_size = p_file.stat().st_size
    desc = (caption or title or p_file.stem)[:2200]

    print(f"\n[Facebook Graph API] Uploading Reel: {p_file.name} ({file_size / (1024*1024):.2f} MB)")
    print(f"[Facebook Graph API] Target Page ID: {page_id}")

    # Phase 1: Initialize upload session
    init_url = f"https://graph.facebook.com/v20.0/{page_id}/video_reels"
    r_init = requests.post(init_url, data={"upload_phase": "start", "access_token": token}, timeout=15)
    if r_init.status_code != 200:
        err = r_init.json().get("error", {}).get("message", r_init.text)
        raise RuntimeError(f"Graph API Reels start phase failed: {err}")

    init_data = r_init.json()
    video_id = init_data.get("video_id")
    upload_url = init_data.get("upload_url")
    if not video_id or not upload_url:
        raise RuntimeError(f"Invalid response from Reels initialize: {init_data}")

    print(f"[Facebook Graph API] Initialized upload session. Video ID: {video_id}")

    # Phase 2: Binary upload to rupload.facebook.com
    upload_headers = {
        "Authorization": f"OAuth {token}",
        "offset": "0",
        "file_size": str(file_size),
        "Content-Type": "application/octet-stream"
    }

    with open(p_file, "rb") as f:
        r_upload = requests.post(upload_url, headers=upload_headers, data=f, timeout=180)

    if r_upload.status_code != 200:
        raise RuntimeError(f"Graph API binary upload failed (status {r_upload.status_code}): {r_upload.text}")

    print(f"[Facebook Graph API] Video binary uploaded successfully. Publishing Reel...")

    # Phase 3: Finish and publish
    finish_payload = {
        "upload_phase": "finish",
        "access_token": token,
        "video_id": video_id,
        "video_state": "PUBLISHED",
        "description": desc
    }
    if title:
        finish_payload["title"] = title[:100]

    r_finish = requests.post(init_url, data=finish_payload, timeout=20)
    if r_finish.status_code != 200:
        err = r_finish.json().get("error", {}).get("message", r_finish.text)
        raise RuntimeError(f"Graph API Reels publish phase failed: {err}")

    finish_data = r_finish.json()
    post_id = finish_data.get("post_id") or video_id
    reel_url = f"https://www.facebook.com/reel/{video_id}/"
    print(f"[Facebook Graph API] Reel published! Initial URL: {reel_url}")

    # Phase 4: Wait for Facebook video processing to complete
    status_info = {}
    if wait_for_ready:
        print(f"[Facebook Graph API] Waiting for Facebook transcoding & processing...", end="", flush=True)
        start_time = time.time()
        while time.time() - start_time < max_wait_sec:
            time.sleep(3)
            print(".", end="", flush=True)
            try:
                r_stat = requests.get(
                    f"https://graph.facebook.com/v20.0/{video_id}?fields=status,permalink_url&access_token={token}",
                    timeout=10
                )
                if r_stat.status_code == 200:
                    status_info = r_stat.json()
                    v_stat = status_info.get("status", {}).get("video_status")
                    if v_stat == "ready":
                        if "permalink_url" in status_info:
                            reel_url = f"https://www.facebook.com{status_info['permalink_url']}"
                        print(f" Ready!")
                        break
                    elif v_stat == "error":
                        print(" Processing Error!")
                        break
            except Exception:
                pass
        else:
            print(" Continuing in background.")

    return {
        "success": True,
        "platform": "facebook",
        "mode": "graph_api",
        "video_id": str(video_id),
        "post_id": str(post_id),
        "url": reel_url,
        "page_id": str(page_id),
        "status": status_info.get("status", {}).get("video_status", "processing")
    }


def upload_facebook(
    file_path: str,
    caption: str = "",
    title: str = "",
    mode: str = "auto"
) -> Dict:
    """
    Unified Facebook Uploader:
    Automatically selects Graph API if available (best for Pages),
    falling back to Headless Playwright (best for personal profiles).
    """
    if mode == "auto":
        if is_facebook_graph_configured():
            try:
                return upload_facebook_reel_graph_api(file_path, caption=caption, title=title)
            except Exception as e:
                print(f"[Warning] Graph API upload failed: {e}. Trying headless fallback if cookies present...")
                if is_facebook_cookies_configured():
                    return upload_facebook_reel_headless(file_path, caption=caption)
                raise e
        elif is_facebook_cookies_configured():
            return upload_facebook_reel_headless(file_path, caption=caption)
        else:
            raise RuntimeError(
                "Neither Facebook Graph API credentials nor Session Cookies are configured.\n"
                "Please configure Page ID/Token in facebook_api/.env or add cookies in facebook_cookies.json."
            )
    elif mode == "graph_api":
        return upload_facebook_reel_graph_api(file_path, caption=caption, title=title)
    elif mode == "headless":
        return upload_facebook_reel_headless(file_path, caption=caption)
    else:
        raise ValueError(f"Unknown upload mode: {mode}")


def is_facebook_cookies_configured() -> bool:
    """Check if cookies file exists and has c_user and xs."""
    cookies = get_facebook_cookies()
    names = {c.get("name") for c in cookies}
    return "c_user" in names and "xs" in names


def check_facebook_session(timeout_sec: int = 30) -> Dict:
    """Launch lightweight headless browser to verify Facebook cookies and extract account info."""
    cookies = get_facebook_cookies()
    if not cookies:
        return {"logged_in": False, "error": "No cookies found. Please set up Facebook cookies."}

    c_user = next((c["value"] for c in cookies if c["name"] == "c_user"), "Unknown")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"logged_in": False, "error": "Playwright is not installed."}

    try:
        with sync_playwright() as p:
            device = p.devices.get("Pixel 7") or {
                "user_agent": "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Mobile Safari/537.36",
                "viewport": {"width": 412, "height": 915},
                "device_scale_factor": 2.625,
                "is_mobile": True,
                "has_touch": True
            }
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-software-rasterizer",
                    "--disable-extensions",
                    "--js-flags=--max-old-space-size=128",
                    "--disable-blink-features=AutomationControlled"
                ]
            )
            context = browser.new_context(**device)
            context.add_cookies(cookies)
            page = context.new_page()

            page.goto("https://m.facebook.com/", timeout=timeout_sec * 1000, wait_until="domcontentloaded")
            time.sleep(2)

            url = page.url
            title = page.title()

            if "/login" in url or "Log in" in title:
                browser.close()
                return {
                    "logged_in": False,
                    "user_id": c_user,
                    "error": "Session cookies expired or rejected by Facebook. Please update cookies."
                }

            account_name = "Kazuto Kirigaya" if c_user == "61575171058299" else f"Facebook User ({c_user})"
            try:
                # Check composer or feed for user name
                extracted = page.evaluate(r'''() => {
                    const texts = Array.from(document.querySelectorAll('span, div, h2, strong')).map(e => e.innerText.trim()).filter(t => t && t.length > 2 && t.length < 35);
                    for (const t of texts) {
                        if (t.includes('Kirigaya') || t.includes('Kazuto')) return t;
                    }
                    return null;
                }''')
                if extracted:
                    account_name = extracted
            except Exception:
                pass

            browser.close()
            return {
                "logged_in": True,
                "user_id": c_user,
                "name": account_name,
                "url": url
            }
    except Exception as e:
        return {"logged_in": False, "error": str(e)}


def upload_facebook_reel_headless(file_path: str, caption: str = "", timeout_sec: int = 180) -> Dict:
    """
    Upload a video as a Facebook Reel / Video directly using Playwright headless mobile browser.
    Bypasses the Meta Graph API completely and works directly with personal profiles and pages.
    """
    p_file = Path(file_path).expanduser().resolve()
    if not p_file.exists():
        raise RuntimeError(f"Video file not found: {file_path}")

    cookies = get_facebook_cookies()
    if not cookies:
        raise RuntimeError("No Facebook cookies found. Please set up Facebook cookies first.")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        device = p.devices.get("Pixel 7") or {
            "user_agent": "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Mobile Safari/537.36",
            "viewport": {"width": 412, "height": 915},
            "device_scale_factor": 2.625,
            "is_mobile": True,
            "has_touch": True
        }
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-software-rasterizer",
                "--disable-extensions",
                "--js-flags=--max-old-space-size=128",
                "--disable-blink-features=AutomationControlled"
            ]
        )
        context = browser.new_context(**device)
        context.add_cookies(cookies)
        page = context.new_page()

        print("Navigating to Facebook Mobile Composer (https://m.facebook.com/composer/)...")
        page.goto("https://m.facebook.com/composer/", timeout=45000, wait_until="domcontentloaded")
        time.sleep(2)

        current_url = page.url
        if "/login" in current_url or "Log in" in page.title():
            browser.close()
            raise RuntimeError("Facebook session cookies expired or invalid. Please update your cookies.")

        # Step 1: Attach video file using FileChooser on Video/Photo button
        print(f"Uploading video file: {p_file.name} ({p_file.stat().st_size / (1024*1024):.1f} MB)...")
        time.sleep(3)
        attached = False
        try:
            with page.expect_file_chooser(timeout=10000) as fc_info:
                v_btn = page.locator('div, button, a').filter(has_text='Video').last
                if v_btn.count() > 0:
                    v_btn.click()
                else:
                    page.locator('div, button, a').filter(has_text='Photo').last.click()
            fc = fc_info.value
            fc.set_files(str(p_file))
            attached = True
            print("Video attached via file chooser!")
        except Exception as e:
            print(f"File chooser trigger fallback: {e}")
            try:
                with page.expect_file_chooser(timeout=5000) as fc_info:
                    page.evaluate(r'''() => {
                        const btns = Array.from(document.querySelectorAll('button, div[role="button"], a'));
                        const b = btns.find(x => x.innerText && x.innerText.includes('Video')) || 
                                  btns.find(x => x.innerText && x.innerText.includes('Photo'));
                        if (b) b.click();
                    }''')
                fc = fc_info.value
                fc.set_files(str(p_file))
                attached = True
                print("Video attached via evaluate file chooser!")
            except Exception as ex:
                browser.close()
                raise RuntimeError(f"Could not locate or trigger video upload on Facebook: {ex}")

        print("Waiting for video to be recognized by composer...")
        time.sleep(4)

        # Step 2: Add caption/description
        if caption:
            print(f"Setting caption: {caption[:60]}...")
            try:
                focused = page.evaluate(r'''() => {
                    const el = document.querySelector('div[role="textbox"], div[contenteditable="true"], textarea');
                    if (el) {
                        el.focus();
                        return true;
                    }
                    return false;
                }''')
                if focused:
                    page.keyboard.type(caption, delay=15)
                else:
                    tb = page.locator('div[role="textbox"], div[contenteditable="true"], textarea').first
                    tb.click(timeout=3000)
                    tb.type(caption, delay=15)
            except Exception as e:
                print(f"[Notice] Direct focus typing fallback: {e}")
                page.evaluate(r'''(cap) => {
                    const el = document.querySelector('div[role="textbox"], div[contenteditable="true"], textarea');
                    if (el) el.innerText = cap;
                }''', caption)

        time.sleep(2)

        # Step 3: Click POST button
        print("Locating and clicking POST button...")
        clicked_post = page.evaluate(r'''() => {
            const btns = Array.from(document.querySelectorAll('div, button, a')).filter(b => b.innerText && b.innerText.trim() === 'POST');
            if (btns.length > 0) {
                btns[btns.length - 1].click();
                return true;
            }
            return false;
        }''')

        if not clicked_post:
            page.screenshot(path=str(SCREENSHOTS_DIR / "publish_btn_not_found.png"), timeout=5000)
            browser.close()
            raise RuntimeError("Failed to find or click Facebook POST button.")

        print("POST clicked! Waiting for video processing and publication...")
        for _ in range(12):
            time.sleep(2)
            cur_url = page.url
            if "/composer/" not in cur_url:
                print(f"Composer submitted and redirected to: {cur_url}")
                break

        time.sleep(3)
        SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        success_img = SCREENSHOTS_DIR / f"success_{p_file.stem[:20]}.png"
        try:
            page.screenshot(path=str(success_img), timeout=5000)
            print(f"Captured confirmation screenshot: {success_img}")
        except Exception:
            pass

        browser.close()
        print("✔ Successfully uploaded Reel / Video directly to Facebook via headless mobile browser!")
        return {
            "success": True,
            "platform": "facebook",
            "mode": "headless_playwright_mobile",
            "file": str(p_file),
            "screenshot": str(success_img)
        }


def login_with_password(password: str, email_or_phone: Optional[str] = None) -> Dict:
    """
    Log into Facebook using password in headless Chromium to unlock the session and extract fresh cookies.
    """
    from playwright.sync_api import sync_playwright

    cookies = get_facebook_cookies()
    screenshot_dir = SCREENSHOTS_DIR
    screenshot_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--no-zygote",
                "--single-process",
                "--disable-blink-features=AutomationControlled"
            ]
        )
        ctx = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 800},
            locale="en-US"
        )
        if cookies:
            ctx.add_cookies(cookies)
        page = ctx.new_page()

        print("Navigating to Facebook login...")
        page.goto("https://www.facebook.com/", wait_until="domcontentloaded", timeout=45000)
        time.sleep(3)

        # Check for 'Continue' on device-based login
        continue_btn = page.locator("text=Continue").or_(page.locator("button:has-text(\"Continue\")"))
        if continue_btn.count() > 0 and continue_btn.first.is_visible():
            print("Found saved account. Clicking Continue...")
            continue_btn.first.click()
            time.sleep(2)

        # Fill password
        pass_input = page.locator("input[type=\"password\"], input[name=\"pass\"]").first
        if pass_input.count() > 0:
            print("Entering password...")
            pass_input.fill(password)
            time.sleep(1)

            email_input = page.locator("input[name=\"email\"], input[type=\"email\"]").first
            if email_input.count() > 0 and email_input.is_visible() and not email_input.input_value():
                account_id = email_or_phone or "61575171058299"
                print(f"Entering login identifier: {account_id}...")
                email_input.fill(account_id)

            login_btn = page.locator("button:has-text(\"Log in\"), button[name=\"login\"], div[role=\"button\"]:has-text(\"Log in\")").first
            if login_btn.count() > 0:
                print("Submitting login...")
                login_btn.click()
                time.sleep(6)

        screenshot_path = screenshot_dir / "login_result.png"
        page.screenshot(path=str(screenshot_path))

        curr_url = page.url
        page_title = page.title()
        if "checkpoint" in curr_url or "two_factor" in curr_url or "approvals" in curr_url:
            browser.close()
            return {
                "success": False,
                "error": "Two-Factor Authentication (2FA) or identity approval required by Facebook. Check notifications on your phone.",
                "checkpoint": True,
                "screenshot": str(screenshot_path)
            }

        if "/login" in curr_url or page.locator("input[type=\"password\"]").count() > 0:
            err_box = page.locator("div[role=\"alert\"], #error_box").first
            err_text = err_box.inner_text().strip() if err_box.count() > 0 else "Password not accepted or login rejected by Facebook."
            browser.close()
            return {
                "success": False,
                "error": err_text,
                "screenshot": str(screenshot_path)
            }

        # Extract all cookies from the newly authenticated session
        fresh_cookies = ctx.cookies()
        browser.close()

        fb_cookies = [
            c for c in fresh_cookies
            if any(dom in c.get("domain", "") for dom in ["facebook.com", "meta.com", "fb.com"])
        ]

        if fb_cookies:
            FACEBOOK_COOKIES_PATH.parent.mkdir(parents=True, exist_ok=True)
            FACEBOOK_COOKIES_PATH.write_text(json.dumps(fb_cookies, indent=2))
            try:
                FACEBOOK_COOKIES_FALLBACK.write_text(json.dumps(fb_cookies, indent=2))
            except Exception:
                pass
            return {"success": True, "cookies_count": len(fb_cookies), "screenshot": str(screenshot_path)}

        return {"success": False, "error": "Login submitted but no session cookies were captured."}


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "status":
        print("--- Facebook Graph API Status ---")
        g_stat = check_facebook_graph_api()
        print(json.dumps(g_stat, indent=2))
        print("\n--- Facebook Headless Cookies Status ---")
        c_stat = check_facebook_session()
        print(json.dumps(c_stat, indent=2))
    elif len(sys.argv) > 2 and sys.argv[1] == "set-cookies":
        arg = sys.argv[2]
        if os.path.exists(arg):
            content = Path(arg).read_text(encoding="utf-8")
        else:
            content = arg
        ok = parse_and_save_facebook_cookies(content)
        if ok:
            print("Successfully saved Facebook cookies.")
            print(check_facebook_session())
        else:
            print("Failed to parse Facebook cookies.")
            sys.exit(1)
    elif len(sys.argv) > 2 and sys.argv[1] == "upload":
        caption = sys.argv[3] if len(sys.argv) > 3 else ""
        print(json.dumps(upload_facebook(sys.argv[2], caption=caption), indent=2))
    elif len(sys.argv) > 2 and sys.argv[1] == "upload-graph":
        caption = sys.argv[3] if len(sys.argv) > 3 else ""
        print(json.dumps(upload_facebook_reel_graph_api(sys.argv[2], caption=caption), indent=2))
    elif len(sys.argv) > 2 and sys.argv[1] == "upload-headless":
        caption = sys.argv[3] if len(sys.argv) > 3 else ""
        print(json.dumps(upload_facebook_reel_headless(sys.argv[2], caption=caption), indent=2))
    else:
        print("Usage: facebook_uploader.py [status | set-cookies <cookies_json_or_file> | upload <file> [caption] | upload-graph <file> [caption] | upload-headless <file> [caption]]")

