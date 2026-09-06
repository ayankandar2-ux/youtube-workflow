#!/usr/bin/env python3
"""
Multi-Platform Video Automation Tool Entry Point.
Top-level selection:
  - YouTube
  - Instagram
  - Settings
  - Exit
Each platform provides its complete workflow menu with arrow-key navigation and full pipeline.
"""

import os
import shutil
import sys
import argparse
import re
import urllib.parse
from pathlib import Path
from typing import List, Dict, Optional, Any

from tui_menu import select_menu, ask_text, print_banner, clear_screen, BOLD, RESET, GREEN, YELLOW, CYAN, RED, BLUE
import youtube_workflow as yw


# =============================================================================
# HELPERS
# =============================================================================

def extract_ig_username(url_or_user: str) -> str:
    """Extract clean Instagram username from a URL, @handle, or plain string."""
    s = str(url_or_user).strip()
    if not s:
        return ""
    if s.startswith("@"):
        s = s[1:]
    if "instagram.com" in s or "instagr.am" in s:
        if not (s.startswith("http://") or s.startswith("https://")):
            s = f"https://{s}"
        try:
            parsed = urllib.parse.urlparse(s)
            path_parts = [p for p in parsed.path.strip("/").split("/") if p]
            if path_parts:
                if path_parts[0] in ("p", "reel", "reels", "stories", "tv"):
                    return ""
                return path_parts[0]
        except Exception:
            pass
    s = s.split("?")[0].split("/")[0].strip()
    if s and re.match(r"^[a-zA-Z0-9._]+$", s):
        return s
    return ""


def normalize_ig_profile(url_or_user: str) -> str:
    """Normalize Instagram username or handle to a valid clean profile URL without tracking params."""
    username = extract_ig_username(url_or_user)
    if username:
        return f"https://www.instagram.com/{username}/"
    u = str(url_or_user).strip()
    if not (u.startswith("http://") or u.startswith("https://")):
        u = f"https://{u}"
    try:
        parsed = urllib.parse.urlparse(u)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/") + "/"
    except Exception:
        return u


def get_dest_name(dest: str) -> str:
    """Return user-friendly display name for an upload destination."""
    if not dest:
        return ""
    d = dest.lower()
    if d in ["instagram", "instagram_facebook"]:
        return "INSTAGRAM + FACEBOOK"
    elif d == "instagram_only":
        return "INSTAGRAM ONLY"
    elif d == "facebook":
        return "FACEBOOK DIRECT"
    elif d in ["all", "both"]:
        return "ALL PLATFORMS (YOUTUBE + INSTAGRAM + FACEBOOK)"
    elif d == "youtube":
        return "YOUTUBE"
    return dest.upper()


def choose_upload_destination(item_count: int, default_platform: str = "youtube") -> Optional[str]:
    """
    Prompt user where to upload videos:
    YouTube, Instagram + Facebook, Facebook Only, Instagram Only, or All Platforms.
    """
    import facebook_uploader as fu
    if fu.is_facebook_graph_configured():
        fb_page_info = fu.check_facebook_graph_api()
        pname = fb_page_info.get("name", "Page") if fb_page_info.get("valid") else "Page"
        fb_tag = f" {GREEN}[FB Graph API: {pname}]{RESET}"
    elif fu.is_facebook_cookies_configured():
        fb_tag = f" {GREEN}[FB Headless Ready]{RESET}"
    else:
        fb_tag = f" {YELLOW}[No FB Configured]{RESET}"

    dest_options = [
        "1. YouTube Only (Public Video / Short)",
        f"2. Instagram + Facebook{fb_tag} (Upload to Both)",
        f"3. Facebook Only{fb_tag} (Official Graph API Reel / Headless)",
        "4. Instagram Only (No Facebook)",
        "5. All Platforms (YouTube + Instagram + Facebook)",
        "6. Cancel and return to menu"
    ]
    idx = select_menu(f"Where would you like to upload the {item_count} video(s)?", dest_options)
    if idx in [5, -1]:
        return None
    dest_map = ["youtube", "instagram", "facebook", "instagram_only", "all"]
    chosen = dest_map[idx]

    # If chosen includes Facebook but neither Graph API nor cookies are configured yet, offer quick setup
    if chosen in ["instagram", "facebook", "all"] and not fu.is_facebook_configured():
        clear_screen()
        print_banner("Facebook Cookies Setup", platform="facebook")
        print(f"{YELLOW}{BOLD}Notice:{RESET} Direct Facebook upload requires your Facebook session cookies.")
        print("Without cookies, the headless browser cannot log in to your account to upload Reels.\n")

        cookie_options = [
            "1. Setup / Paste Facebook Cookies now (takes 10 seconds)",
            "2. Enter c_user and xs cookies manually"
        ]
        if chosen == "instagram":
            cookie_options.append("3. Continue with Instagram Only (without Direct FB)")
        cookie_options.append(f"{len(cookie_options) + 1}. Cancel upload")

        c_idx = select_menu("How would you like to proceed?", cookie_options)
        if c_idx == 0:
            clear_screen()
            print_banner("Paste Facebook Cookies", platform="facebook")
            print(f"{BOLD}Paste your Facebook cookies below:{RESET}")
            print("  • Accepts JSON array (from 'Cookie-Editor' extension)")
            print("  • Accepts cookies.txt Netscape format")
            print("  • Accepts raw cookie string (e.g. c_user=...; xs=...)\n")
            raw_cookies = ask_text("Paste cookie text (or blank to cancel):", allow_empty=True).strip()
            if raw_cookies and raw_cookies.lower() not in ["cancel", "back"]:
                if fu.parse_and_save_facebook_cookies(raw_cookies):
                    print(f"\n{GREEN}✔ Cookies saved successfully! Verifying session...{RESET}")
                    res = fu.check_facebook_session(timeout_sec=20)
                    if res.get("logged_in"):
                        print(f"{GREEN}✔ Connected to Facebook as: {res.get('name')}{RESET}")
                    else:
                        print(f"{YELLOW}[Notice] Cookies saved: {res.get('error')}{RESET}")
                    time.sleep(1.5)
                    return chosen
                else:
                    print(f"\n{RED}Failed to parse cookies.{RESET}")
                    time.sleep(2)
                    return "instagram_only" if chosen == "instagram" else None
            return "instagram_only" if chosen == "instagram" else None
        elif c_idx == 1:
            clear_screen()
            print_banner("Manual c_user & xs Entry", platform="facebook")
            c_user = ask_text("Enter c_user (Facebook User ID):", allow_empty=True).strip()
            xs = ask_text("Enter xs (Facebook session token):", allow_empty=True).strip()
            if c_user and xs:
                if fu.parse_and_save_facebook_cookies(f"c_user={c_user}; xs={xs};"):
                    print(f"\n{GREEN}✔ Cookies saved successfully!{RESET}")
                    time.sleep(1)
                    return chosen
                else:
                    print(f"\n{RED}Failed to save cookies.{RESET}")
                    time.sleep(2)
            return "instagram_only" if chosen == "instagram" else None
        elif chosen == "instagram" and c_idx == 2:
            return "instagram_only"
        else:
            return None

    return chosen


def register_local_video_file(file_path: str) -> Optional[Dict]:
    """Register a local video file from disk into the database so it can be uploaded."""
    p = Path(file_path).resolve()
    if not p.exists() or not p.is_file():
        return None

    clean_stem = re.sub(r'[^a-zA-Z0-9_-]', '_', p.stem)
    vid = f"local_{clean_stem[:30]}_{p.stat().st_size}"

    # Check or generate thumbnail
    clean_id = vid.replace("ig_", "").replace("local_", "")
    cand = (
        list(yw.THUMBNAILS_DIR.glob(f"*{clean_id}*.jpg")) or
        list(yw.DOWNLOADS_DIR.glob(f"*{clean_id}*.jpg")) or
        list(yw.THUMBNAILS_DIR.glob(f"*{vid}*.jpg"))
    )
    thumb_path = str(cand[0]) if cand else None
    if not thumb_path or not os.path.exists(thumb_path):
        gen_thumb = yw.THUMBNAILS_DIR / f"{clean_id}.jpg"
        try:
            import subprocess
            subprocess.run([
                "ffmpeg", "-y", "-ss", "00:00:01", "-i", str(p),
                "-vframes", "1", "-q:v", "2", str(gen_thumb)
            ], capture_output=True, check=True)
            if gen_thumb.exists() and gen_thumb.stat().st_size > 0:
                thumb_path = str(gen_thumb)
        except Exception:
            pass

    dur = 0
    try:
        import subprocess
        res = subprocess.run([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(p)
        ], capture_output=True, text=True)
        if res.returncode == 0 and res.stdout.strip():
            dur = float(res.stdout.strip())
    except Exception:
        pass

    data = {
        "video_id": vid,
        "source_url": f"file://{p}",
        "platform": "local",
        "title": p.stem,
        "description": p.stem,
        "tags": [],
        "duration": dur,
        "file_path": str(p),
        "file_size": p.stat().st_size,
        "thumbnail_path": thumb_path,
        "upload_status": "pending"
    }
    yw.save_video_metadata(vid, data)
    return data


def run_upload_local_video(platform: str = "youtube"):
    """Prompt user to select or enter path to a local video file and upload to YouTube, Instagram, or Both."""
    clear_screen()
    print_banner("Upload Local Video File", platform=platform)

    recent_vids = sorted(list(yw.DOWNLOADS_DIR.glob("*.mp4")), key=lambda x: x.stat().st_mtime, reverse=True)[:10]
    sub_options = []
    for f in recent_vids:
        sub_options.append(f"Recent: {f.name[:55]} ({f.stat().st_size/(1024*1024):.1f} MB)")
    sub_options.append("Enter custom file path manually")
    sub_options.append("Back to Menu")

    chosen_file = None
    if recent_vids:
        c_idx = select_menu("Select a downloaded file or enter path:", sub_options)
        if c_idx == -1 or c_idx == len(sub_options) - 1:
            return
        elif c_idx < len(recent_vids):
            chosen_file = str(recent_vids[c_idx])

    if not chosen_file:
        raw_path = ask_text("Enter path to video file (.mp4, .mkv, etc., or blank to cancel):", allow_empty=True).strip()
        if not raw_path or raw_path.lower() in ["cancel", "back"]:
            return
        p = Path(raw_path).expanduser().resolve()
        if not p.exists() or not p.is_file():
            print(f"{RED}File not found: {raw_path}{RESET}")
            input(f"\n{BOLD}Press Enter to return...{RESET}")
            return
        chosen_file = str(p)

    video_data = register_local_video_file(chosen_file)
    if not video_data:
        print(f"{RED}Failed to prepare video file for upload.{RESET}")
        input(f"\n{BOLD}Press Enter to return...{RESET}")
        return

    dest = choose_upload_destination(1, default_platform=platform)
    if not dest:
        return

    yt_service = yw.get_youtube_service() if dest in ["youtube", "both", "all"] else None
    ig_client = yw.get_instagram_client(interactive=True) if dest in ["instagram", "instagram_only", "both", "all"] else None

    print(f"\n{GREEN}{BOLD}Uploading '{video_data['title']}' to {get_dest_name(dest)}...{RESET}")
    yw.update_video_status(video_data["video_id"], "uploading")
    try:
        yw.upload_item(video_data, destination=dest, yt_service=yt_service, ig_client=ig_client, privacy_status="public")
        print(f"\n{GREEN}{BOLD}Upload complete!{RESET}")
    except Exception as e:
        print(f"\n{RED}Upload failed: {e}{RESET}")
        yw.update_video_status(video_data["video_id"], "failed", error=str(e))

    input(f"\n{BOLD}Press Enter to return to menu...{RESET}")


def show_queue_status(platform_filter: str = None):
    """Display queue status and upload history."""
    clear_screen()
    plat_title = f"{platform_filter.capitalize()} " if platform_filter else ""
    print_banner(f"{plat_title}Queue Status", platform=platform_filter)
    yw.cmd_status(argparse.Namespace(verbose=True, all=False))
    input(f"\n{BOLD}Press Enter to return to menu...{RESET}")


# =============================================================================
# YOUTUBE WORKFLOWS
# =============================================================================

def run_youtube_option_1():
    """
    YOUTUBE OPTION 1 — MULTI-DOWNLOAD + UPLOAD
    - Let user add YouTube video URLs one by one (up to 10 URLs).
    - Show added URLs in interface.
    - Ask for quality: 720p, 480p, or 360p.
    - Long videos (2h–20h+) supported with confirmation prompt.
    - Download and upload to YouTube as public with sanitized descriptions.
    """
    urls = []
    status_msg = ""

    while True:
        clear_screen()
        print_banner("Option 1: Multi-Download + Upload", platform="youtube")
        if status_msg:
            print(f"{status_msg}\n")
            status_msg = ""

        print(f"{BOLD}Currently Added YouTube Video URLs ({len(urls)}/10):{RESET}")
        if not urls:
            print(f"  {YELLOW}(No URLs added yet){RESET}\n")
        else:
            for idx, u in enumerate(urls, 1):
                val = u["value"]
                prefix = "[YOUTUBE]" if u["type"] == "url" else "[LOCAL FILE]"
                print(f"  {CYAN}{idx}.{RESET} {prefix} {val}")
            print()

        sub_options = []
        if len(urls) < 10:
            sub_options.append("Add Video URL")
            sub_options.append("Add Local Video File")
        if urls:
            sub_options.append(f"Done / Continue ({len(urls)} item(s))")
            sub_options.append("Remove Last Item")
            sub_options.append("Clear All Items")
        sub_options.append("Back to YouTube Menu")

        choice_idx = select_menu("Choose an action:", sub_options)
        if choice_idx == -1:
            return
        choice = sub_options[choice_idx]

        if choice == "Add Video URL":
            url = ask_text("Enter YouTube Video URL (or blank to cancel):", allow_empty=True).strip()
            if not url or url.lower() in ["cancel", "back"]:
                continue
            vid = yw.extract_video_id(url)
            plat = yw.detect_platform(url)
            if vid and plat == "youtube":
                urls.append({"type": "url", "value": url})
                status_msg = f"{GREEN}✔ [YOUTUBE] URL added!{RESET}"
            elif Path(url).exists() and Path(url).is_file():
                urls.append({"type": "local", "value": url})
                status_msg = f"{GREEN}✔ [LOCAL FILE] Video added: {Path(url).name}!{RESET}"
            else:
                status_msg = f"{RED}Invalid YouTube URL format.{RESET}"
        elif choice == "Add Local Video File":
            recent_vids = sorted(list(yw.DOWNLOADS_DIR.glob("*.mp4")), key=lambda x: x.stat().st_mtime, reverse=True)[:10]
            f_opts = [f"Recent: {f.name[:45]}" for f in recent_vids] + ["Enter custom file path manually", "Cancel"]
            f_idx = select_menu("Select a video file to add:", f_opts)
            if f_idx in [-1, len(f_opts) - 1]:
                continue
            elif f_idx < len(recent_vids):
                urls.append({"type": "local", "value": str(recent_vids[f_idx])})
                status_msg = f"{GREEN}✔ [LOCAL FILE] Video added: {recent_vids[f_idx].name}!{RESET}"
            else:
                raw_p = ask_text("Enter full path to video file (.mp4, .mkv, etc.):", allow_empty=True).strip()
                if raw_p and Path(raw_p).exists() and Path(raw_p).is_file():
                    urls.append({"type": "local", "value": str(Path(raw_p).resolve())})
                    status_msg = f"{GREEN}✔ [LOCAL FILE] Video added: {Path(raw_p).name}!{RESET}"
                elif raw_p:
                    status_msg = f"{RED}File not found: {raw_p}{RESET}"
        elif choice.startswith("Done / Continue"):
            break
        elif choice == "Remove Last Item":
            if urls:
                removed = urls.pop()
                status_msg = f"{YELLOW}Removed: {removed['value']}{RESET}"
        elif choice == "Clear All Items":
            urls.clear()
            status_msg = f"{YELLOW}Cleared all items.{RESET}"
        elif choice == "Back to YouTube Menu":
            return

    if not urls:
        print(f"{YELLOW}No videos to process.{RESET}")
        return

    quality_options = ["720p (High Quality)", "480p (Standard)", "360p (Low / Fast)"]
    q_idx = select_menu("Select Download Quality:", quality_options)
    if q_idx == -1:
        return
    height_map = [720, 480, 360]
    chosen_height = height_map[q_idx]

    dest = choose_upload_destination(len(urls), default_platform="youtube")
    if not dest:
        return

    print(f"\n{CYAN}{BOLD}Starting Multi-Download & Upload for {len(urls)} item(s) at {chosen_height}p to {dest.upper()}...{RESET}")
    downloaded = []
    for item_entry in urls:
        if item_entry["type"] == "local":
            v_data = register_local_video_file(item_entry["value"])
            if v_data:
                downloaded.append(v_data)
        else:
            u = item_entry["value"]
            try:
                res = yw.download_video(u, max_height=chosen_height)
                if res:
                    downloaded.append(res)
            except Exception as e:
                print(f"{RED}Error downloading {u}: {e}{RESET}")

    if downloaded:
        print(f"\n{GREEN}{BOLD}Uploading {len(downloaded)} video(s) to {get_dest_name(dest)}...{RESET}")
        yt_service = yw.get_youtube_service() if dest in ["youtube", "both", "all"] else None
        ig_client = yw.get_instagram_client(interactive=True) if dest in ["instagram", "instagram_only", "both", "all"] else None
        success = 0
        failed = 0
        for i, item in enumerate(downloaded, 1):
            vid = item.get("video_id")
            title = item.get("title") or vid
            print(f"\n[{i}/{len(downloaded)}] Starting upload for: {title}")
            yw.update_video_status(vid, "uploading")
            try:
                yw.upload_item(item, destination=dest, yt_service=yt_service, ig_client=ig_client, privacy_status="public")
                success += 1
            except Exception as e:
                err_msg = str(e)
                print(f"  {RED}Upload error for {vid}: {err_msg}{RESET}")
                yw.update_video_status(vid, "failed", error=err_msg)
                failed += 1
                if "Daily Upload Limit Exceeded" in err_msg or "uploadLimitExceeded" in err_msg or "quotaExceeded" in err_msg:
                    print(f"\n{YELLOW}{BOLD}[!] YouTube daily upload limit reached on this channel.{RESET}")
                    print(f"{YELLOW}Stopping further YouTube uploads in this batch. All downloaded files remain saved in 'downloads/'.{RESET}")
                    break
        print("\n" + "=" * 50)
        print(f"Upload Batch Complete: {success} Successful, {failed} Failed")
        print("=" * 50)
    else:
        print(f"\n{YELLOW}No videos were eligible for upload (skipped or failed).{RESET}")

    input(f"\n{BOLD}Press Enter to return to YouTube menu...{RESET}")


def run_youtube_option_2():
    """
    YOUTUBE OPTION 2 — DOWNLOAD FULL CHANNEL + UPLOAD
    - Ask for YouTube channel URL.
    - Fetch channel and count all videos and shorts.
    - Present 3 choices:
        1. Download all videos
        2. Download only shorts
        3. Download long videos
    - Select quality: 720p, 480p, 360p, or 180p.
    - Long videos (2h–20h+) supported with user confirmation.
    - Destination: YouTube, Instagram (+ Facebook Auto-Share), or Both.
    - Download and upload as public.
    """
    clear_screen()
    print_banner("Option 2: Download Full Channel + Upload", platform="youtube")

    channel_url = ask_text("Enter YouTube Channel URL (e.g. https://www.youtube.com/@Channel, or blank to cancel):", allow_empty=True).strip()
    if not channel_url or channel_url.lower() in ["cancel", "back"]:
        return

    print(f"\n{CYAN}Scanning full YouTube channel contents from {channel_url}...{RESET}")
    print(f"{CYAN}Fetching videos and shorts list...{RESET}")
    try:
        data = yw.get_channel_videos_categorized(channel_url)
        all_videos = data.get("all", [])
        shorts_videos = data.get("shorts", [])
        long_videos = data.get("long", [])
    except Exception as e:
        print(f"{RED}Failed to fetch channel: {e}{RESET}")
        input(f"\n{BOLD}Press Enter to return to YouTube menu...{RESET}")
        return

    if not all_videos:
        print(f"{YELLOW}No videos found on this channel.{RESET}")
        input(f"\n{BOLD}Press Enter to return to YouTube menu...{RESET}")
        return

    dur_2h_plus = [v for v in long_videos if v.get("duration") and v.get("duration") >= 7200]

    print(f"\n{GREEN}{BOLD}YouTube Channel Scan Complete:{RESET}")
    print(f"  • Total items found: {len(all_videos)}")
    print(f"  • YouTube Shorts: {len(shorts_videos)}")
    print(f"  • Long / Standard videos: {len(long_videos)}")
    if dur_2h_plus:
        print(f"    (including {len(dur_2h_plus)} long video(s) ≥ 2h, e.g. 11h–20h+)")

    filter_options = [
        f"1. Download all videos ({len(all_videos)} total)",
        f"2. Download only shorts ({len(shorts_videos)} shorts)",
        f"3. Download long videos ({len(long_videos)} long/standard videos)",
        "4. Cancel and return to menu"
    ]
    f_idx = select_menu("What would you like to download?", filter_options)
    if f_idx in [3, -1]:
        return
    elif f_idx == 0:
        eligible_videos = all_videos
        content_label = "all videos & shorts"
    elif f_idx == 1:
        eligible_videos = shorts_videos
        content_label = "shorts"
    elif f_idx == 2:
        eligible_videos = long_videos
        content_label = "long videos"

    if not eligible_videos:
        print(f"\n{YELLOW}No videos found matching your selection ({content_label}).{RESET}")
        input(f"\n{BOLD}Press Enter to return to YouTube menu...{RESET}")
        return

    # Long video check (>= 2 hours, e.g. 11h–20h+)
    long_dur_selected = [v for v in eligible_videos if v.get("duration") and v.get("duration") >= 7200]
    std_dur_selected = [v for v in eligible_videos if not v.get("duration") or v.get("duration") < 7200]
    confirm_each_long = False

    if long_dur_selected:
        handle_options = [
            f"1. Include all videos (both standard and {len(long_dur_selected)} long video(s))",
            f"2. Ask confirmation for each long video before downloading",
            f"3. Skip long videos (process only {len(std_dur_selected)} standard video(s))",
            "4. Cancel and return to menu"
        ]
        h_idx = select_menu(f"Found {len(long_dur_selected)} video(s) 2 hours or longer (up to 20h+). How would you like to proceed?", handle_options)
        if h_idx in [3, -1]:
            return
        elif h_idx == 0:
            eligible_videos = eligible_videos
            confirm_each_long = False
        elif h_idx == 1:
            eligible_videos = eligible_videos
            confirm_each_long = True
        elif h_idx == 2:
            eligible_videos = std_dur_selected
            confirm_each_long = False

    if not eligible_videos:
        print(f"{YELLOW}No videos selected to process.{RESET}")
        input(f"\n{BOLD}Press Enter to return to YouTube menu...{RESET}")
        return

    quality_options = ["720p (High Quality)", "480p (Standard)", "360p (Low)", "180p (Fastest)"]
    q_idx = select_menu("Select Download Quality:", quality_options)
    if q_idx == -1:
        return
    height_map = [720, 480, 360, 180]
    chosen_height = height_map[q_idx]

    dest = choose_upload_destination(len(eligible_videos), default_platform="youtube")
    if not dest:
        return

    confirm_options = [f"Yes, download and upload {len(eligible_videos)} {content_label} to {get_dest_name(dest)}", "Cancel and return to menu"]
    c_idx = select_menu(f"Proceed with downloading & uploading {len(eligible_videos)} video(s)?", confirm_options)
    if c_idx != 0:
        return

    downloaded = []
    yt_service = yw.get_youtube_service() if dest in ["youtube", "both", "all"] else None
    ig_client = yw.get_instagram_client(interactive=True) if dest in ["instagram", "instagram_only", "both", "all"] else None

    for i, v in enumerate(eligible_videos, 1):
        vid = v.get("id")
        title = v.get("title", vid)
        url = v.get("webpage_url") or v.get("url")
        if not url or not str(url).startswith("http"):
            is_short = v.get("is_short", False)
            url = f"https://www.youtube.com/shorts/{vid}" if is_short else f"https://www.youtube.com/watch?v={vid}"
        print(f"\n{CYAN}[{i}/{len(eligible_videos)}] Processing: {title}{RESET}")
        try:
            res = yw.download_video(url, max_height=chosen_height, confirm_long=confirm_each_long)
            if res:
                downloaded.append(res)
                yw.upload_item(res, destination=dest, yt_service=yt_service, ig_client=ig_client, privacy_status="public")
        except Exception as e:
            err_msg = str(e)
            print(f"{RED}Error processing {vid}: {err_msg}{RESET}")
            if "Daily Upload Limit Exceeded" in err_msg or "uploadLimitExceeded" in err_msg or "quotaExceeded" in err_msg:
                print(f"\n{YELLOW}{BOLD}[!] YouTube daily upload limit reached on this channel.{RESET}")
                print(f"{YELLOW}Stopping further YouTube uploads in this batch. All downloaded files remain saved in 'downloads/'.{RESET}")
                break

    print(f"\n{GREEN}{BOLD}Channel processing complete! Uploaded {len(downloaded)} video(s) to {get_dest_name(dest)}.{RESET}")
    input(f"\n{BOLD}Press Enter to return to YouTube menu...{RESET}")


def run_youtube_option_3():
    """
    YOUTUBE OPTION 3 — ADD CHANNEL / MULTI-CHANNEL DOWNLOAD + UPLOAD
    - Add up to 10 YouTube channel URLs one by one.
    - Choose top most-viewed videos per channel (1-10).
    - Select quality: 720p, 480p, 360p, or 180p.
    - Long videos (2h–20h+) supported with confirmation.
    - Download and upload to YouTube as public.
    """
    channels = []
    status_msg = ""

    while True:
        clear_screen()
        print_banner("Option 3: Multi-Channel Download + Upload", platform="youtube")
        if status_msg:
            print(f"{status_msg}\n")
            status_msg = ""

        print(f"{BOLD}Currently Added YouTube Channels ({len(channels)}/10):{RESET}")
        if not channels:
            print(f"  {YELLOW}(No channels added yet){RESET}\n")
        else:
            for idx, c in enumerate(channels, 1):
                print(f"  {CYAN}{idx}.{RESET} [YOUTUBE] {c}")
            print()

        sub_options = []
        if len(channels) < 10:
            sub_options.append("Add Channel URL")
        if channels:
            sub_options.append(f"Done / Continue ({len(channels)} channel(s))")
            sub_options.append("Remove Last Channel")
            sub_options.append("Clear All Channels")
        sub_options.append("Back to YouTube Menu")

        choice_idx = select_menu("Choose an action:", sub_options)
        if choice_idx == -1:
            return
        choice = sub_options[choice_idx]

        if choice == "Add Channel URL":
            url = ask_text("Enter YouTube Channel URL (or blank to cancel):", allow_empty=True).strip()
            if not url or url.lower() in ["cancel", "back"]:
                continue
            channels.append(url)
            status_msg = f"{GREEN}✔ [YOUTUBE] Channel URL added!{RESET}"
        elif choice.startswith("Done / Continue"):
            break
        elif choice == "Remove Last Channel":
            if channels:
                removed = channels.pop()
                status_msg = f"{YELLOW}Removed: {removed}{RESET}"
        elif choice == "Clear All Channels":
            channels.clear()
            status_msg = f"{YELLOW}Cleared all channels.{RESET}"
        elif choice == "Back to YouTube Menu":
            return

    if not channels:
        print(f"{YELLOW}No channels to process.{RESET}")
        return

    count_options = [f"{i} video{'s' if i > 1 else ''} per channel" for i in range(1, 11)]
    cnt_idx = select_menu("How many top most-viewed videos to process from EACH channel?", count_options, default_index=1)
    if cnt_idx == -1:
        return
    videos_per_channel = cnt_idx + 1

    quality_options = ["720p (High Quality)", "480p (Standard)", "360p (Low)", "180p (Fastest)"]
    q_idx = select_menu("Select Download Quality:", quality_options)
    if q_idx == -1:
        return
    height_map = [720, 480, 360, 180]
    chosen_height = height_map[q_idx]

    dest = choose_upload_destination(len(channels) * videos_per_channel, default_platform="youtube")
    if not dest:
        return

    yt_service = yw.get_youtube_service() if dest in ["youtube", "both", "all"] else None
    ig_client = yw.get_instagram_client(interactive=True) if dest in ["instagram", "instagram_only", "both", "all"] else None
    total_uploaded = 0

    for ch_idx, ch_url in enumerate(channels, 1):
        print(f"\n{BLUE}{BOLD}══════════════════════════════════════════════════════════════{RESET}")
        print(f"{CYAN}{BOLD}[YouTube Channel {ch_idx}/{len(channels)}] Scanning: {ch_url}{RESET}")
        print(f"{BLUE}{BOLD}══════════════════════════════════════════════════════════════{RESET}")

        try:
            all_vids = yw.get_channel_videos(ch_url, max_videos=50)
            top_vids = all_vids[:videos_per_channel]

            print(f"Found {len(all_vids)} videos total. Selecting top {len(top_vids)} most-viewed:")
            for rank, v in enumerate(top_vids, 1):
                dur = v.get("duration") or 0
                views = v.get("view_count") or 0
                title = v.get("title", "Unknown")[:50]
                dur_str = yw.format_duration(dur)
                print(f"  #{rank}: {views:,} views | {dur_str} | {title}")

            for v in top_vids:
                vid = v.get("id")
                v_url = v.get("webpage_url") or v.get("url")
                if not v_url or not str(v_url).startswith("http"):
                    v_url = f"https://www.youtube.com/watch?v={vid}"
                try:
                    res = yw.download_video(v_url, max_height=chosen_height, confirm_long=True)
                    if res:
                        yw.upload_item(res, destination=dest, yt_service=yt_service, ig_client=ig_client, privacy_status="public")
                        total_uploaded += 1
                except Exception as e:
                    err_msg = str(e)
                    print(f"{RED}Error processing {vid}: {err_msg}{RESET}")
                    if "Daily Upload Limit Exceeded" in err_msg or "uploadLimitExceeded" in err_msg or "quotaExceeded" in err_msg:
                        print(f"\n{YELLOW}{BOLD}[!] YouTube daily upload limit reached on this channel.{RESET}")
                        print(f"{YELLOW}Stopping further YouTube uploads in this batch. All downloaded files remain saved in 'downloads/'.{RESET}")
                        break

        except Exception as e:
            print(f"{RED}Failed to process channel {ch_url}: {e}{RESET}")

    print(f"\n{GREEN}{BOLD}Multi-Channel Processing Complete! Total uploaded: {total_uploaded} video(s) to {get_dest_name(dest)}.{RESET}")
    input(f"\n{BOLD}Press Enter to return to YouTube menu...{RESET}")


def run_youtube_option_4():
    """
    YOUTUBE OPTION 4 — PLAYLIST DOWNLOAD + UPLOAD
    - Ask for YouTube playlist URL.
    - Fetch playlist and show count.
    - Long videos (2h–20h+) supported with confirmation.
    - Select quality: 720p, 480p, 360p, or 180p.
    - Download and upload to YouTube as public.
    """
    clear_screen()
    print_banner("Option 4: Playlist Download + Upload", platform="youtube")

    playlist_url = ask_text("Enter YouTube Playlist URL (or blank to cancel):", allow_empty=True).strip()
    if not playlist_url or playlist_url.lower() in ["cancel", "back"]:
        return

    print(f"\n{CYAN}Fetching playlist contents from {playlist_url}...{RESET}")
    try:
        videos = yw.get_playlist_videos(playlist_url)
    except Exception as e:
        print(f"{RED}Failed to fetch playlist: {e}{RESET}")
        input(f"\n{BOLD}Press Enter to return to YouTube menu...{RESET}")
        return

    if not videos:
        print(f"{YELLOW}No videos found in this playlist.{RESET}")
        input(f"\n{BOLD}Press Enter to return to YouTube menu...{RESET}")
        return

    long_videos = [v for v in videos if v.get("duration") and v.get("duration") >= 7200]
    std_videos = [v for v in videos if not v.get("duration") or v.get("duration") < 7200]

    print(f"\n{GREEN}{BOLD}Playlist Scan Complete:{RESET}")
    print(f"  • Total videos found: {len(videos)}")
    print(f"  • Standard videos (< 2h): {len(std_videos)}")
    if long_videos:
        print(f"  • Long videos (≥ 2h, e.g. 11h–20h+): {len(long_videos)}")

    eligible_videos = videos
    confirm_each_long = False

    if long_videos:
        handle_options = [
            f"1. Include all videos (both standard and {len(long_videos)} long video(s))",
            f"2. Ask confirmation for each long video before downloading",
            f"3. Skip long videos (process only {len(std_videos)} standard video(s))",
            "4. Cancel and return to menu"
        ]
        h_idx = select_menu(f"Found {len(long_videos)} video(s) 2 hours or longer (up to 20h+). How would you like to proceed?", handle_options)
        if h_idx in [3, -1]:
            return
        elif h_idx == 0:
            eligible_videos = videos
            confirm_each_long = False
        elif h_idx == 1:
            eligible_videos = videos
            confirm_each_long = True
        elif h_idx == 2:
            eligible_videos = std_videos
            confirm_each_long = False

    if not eligible_videos:
        print(f"{YELLOW}No videos selected to process.{RESET}")
        input(f"\n{BOLD}Press Enter to return to YouTube menu...{RESET}")
        return

    quality_options = ["720p (High Quality)", "480p (Standard)", "360p (Low)", "180p (Fastest)"]
    q_idx = select_menu("Select Download Quality:", quality_options)
    if q_idx == -1:
        return
    height_map = [720, 480, 360, 180]
    chosen_height = height_map[q_idx]

    dest = choose_upload_destination(len(eligible_videos), default_platform="youtube")
    if not dest:
        return

    confirm_options = [f"Yes, download and upload {len(eligible_videos)} video(s) to {get_dest_name(dest)}", "Cancel and return to menu"]
    c_idx = select_menu(f"Proceed with downloading & uploading {len(eligible_videos)} video(s)?", confirm_options)
    if c_idx != 0:
        return

    downloaded = []
    yt_service = yw.get_youtube_service() if dest in ["youtube", "both", "all"] else None
    ig_client = yw.get_instagram_client(interactive=True) if dest in ["instagram", "instagram_only", "both", "all"] else None

    for i, v in enumerate(eligible_videos, 1):
        vid = v.get("id")
        title = v.get("title", vid)
        url = v.get("webpage_url") or v.get("url")
        if not url or not str(url).startswith("http"):
            url = f"https://www.youtube.com/watch?v={vid}"
        print(f"\n{CYAN}[{i}/{len(eligible_videos)}] Processing: {title}{RESET}")
        try:
            res = yw.download_video(url, max_height=chosen_height, confirm_long=confirm_each_long)
            if res:
                downloaded.append(res)
                yw.upload_item(res, destination=dest, yt_service=yt_service, ig_client=ig_client, privacy_status="public")
        except Exception as e:
            err_msg = str(e)
            print(f"{RED}Error processing {vid}: {err_msg}{RESET}")
            if "Daily Upload Limit Exceeded" in err_msg or "uploadLimitExceeded" in err_msg or "quotaExceeded" in err_msg:
                print(f"\n{YELLOW}{BOLD}[!] YouTube daily upload limit reached on this channel.{RESET}")
                print(f"{YELLOW}Stopping further YouTube uploads in this batch. All downloaded files remain saved in 'downloads/'.{RESET}")
                break

    print(f"\n{GREEN}{BOLD}Playlist processing complete! Uploaded {len(downloaded)} video(s) to {get_dest_name(dest)}.{RESET}")
    input(f"\n{BOLD}Press Enter to return to YouTube menu...{RESET}")


def youtube_menu():
    """Complete YouTube Workflow Menu."""
    options = [
        "1. Multi-Download + Upload",
        "2. Download Full Channel + Upload",
        "3. Add Channel / Multi-Channel Download + Upload",
        "4. Playlist Download + Upload",
        "5. Upload Local Video File (to YouTube, Instagram + Facebook, or All)",
        "6. Queue Status & Database Summary",
        "7. Back to Main Menu"
    ]

    while True:
        clear_screen()
        print_banner("YouTube Automation Workflow", platform="youtube")
        idx = select_menu("YouTube Workflow Menu:", options)

        if idx == 0:
            run_youtube_option_1()
        elif idx == 1:
            run_youtube_option_2()
        elif idx == 2:
            run_youtube_option_3()
        elif idx == 3:
            run_youtube_option_4()
        elif idx == 4:
            run_upload_local_video(platform="youtube")
        elif idx == 5:
            show_queue_status("youtube")
        elif idx in [6, -1]:
            break


# =============================================================================
# INSTAGRAM WORKFLOWS
# =============================================================================




def run_instagram_option_1():
    """
    INSTAGRAM OPTION 1 — MULTI-DOWNLOAD + UPLOAD
    - Add up to 10 Instagram Reel / Post URLs one by one.
    - Show added URLs in interface.
    - Ask for quality: 720p, 480p, or 360p.
    - Long videos (2h–20h+) supported with confirmation prompt.
    - Ask destination: YouTube, Instagram, or Both.
    - Download and upload to selected destination.
    """
    urls = []
    status_msg = ""

    while True:
        clear_screen()
        print_banner("Option 1: Multi-Download + Upload", platform="instagram")
        if status_msg:
            print(f"{status_msg}\n")
            status_msg = ""

        print(f"{BOLD}Currently Added Instagram URLs ({len(urls)}/10):{RESET}")
        if not urls:
            print(f"  {YELLOW}(No URLs added yet){RESET}\n")
        else:
            for idx, u in enumerate(urls, 1):
                print(f"  {CYAN}{idx}.{RESET} [INSTAGRAM] {u}")
            print()

        sub_options = []
        if len(urls) < 10:
            sub_options.append("Add Reel / Post URL")
        if urls:
            sub_options.append(f"Done / Continue ({len(urls)} item(s))")
            sub_options.append("Remove Last URL")
            sub_options.append("Clear All URLs")
        sub_options.append("Back to Instagram Menu")

        choice_idx = select_menu("Choose an action:", sub_options)
        if choice_idx == -1:
            return
        choice = sub_options[choice_idx]

        if choice == "Add Reel / Post URL":
            url = ask_text("Enter Instagram Reel or Post URL (e.g. https://www.instagram.com/reel/SHORTCODE/, or blank to cancel):", allow_empty=True).strip()
            if not url or url.lower() in ["cancel", "back"]:
                continue
            vid = yw.extract_video_id(url)
            if vid:
                urls.append(url)
                status_msg = f"{GREEN}✔ [INSTAGRAM] Reel URL added!{RESET}"
            else:
                uname = extract_ig_username(url)
                if uname:
                    print(f"\n{YELLOW}Notice: '{url}' is an account profile URL for @{uname}, not a single reel.{RESET}")
                    sub = select_menu(f"Would you like to fetch the latest reel from @{uname} to add to the download queue?", ["Yes, fetch latest reel", "No, cancel"])
                    if sub == 0:
                        vids = yw.get_channel_videos(url, max_videos=1)
                        if vids:
                            urls.append(vids[0]["url"])
                            status_msg = f"{GREEN}✔ [INSTAGRAM] Added latest reel: {vids[0]['title']} ({vids[0]['url']}){RESET}"
                        else:
                            status_msg = f"{RED}Could not find any reels on @{uname}.{RESET}"
                else:
                    status_msg = f"{RED}Invalid Instagram URL format. Expected: https://www.instagram.com/reel/SHORTCODE/{RESET}"
        elif choice.startswith("Done / Continue"):
            break
        elif choice == "Remove Last URL":
            if urls:
                removed = urls.pop()
                status_msg = f"{YELLOW}Removed: {removed}{RESET}"
        elif choice == "Clear All URLs":
            urls.clear()
            status_msg = f"{YELLOW}Cleared all URLs.{RESET}"
        elif choice == "Back to Instagram Menu":
            return

    if not urls:
        print(f"{YELLOW}No URLs to process.{RESET}")
        return

    quality_options = ["720p (High Quality)", "480p (Standard)", "360p (Low / Fast)"]
    q_idx = select_menu("Select Download Quality:", quality_options)
    if q_idx == -1:
        return
    height_map = [720, 480, 360]
    chosen_height = height_map[q_idx]

    dest = choose_upload_destination(len(urls), default_platform="instagram")
    if not dest:
        return

    print(f"\n{CYAN}{BOLD}Starting Multi-Download for {len(urls)} Instagram item(s) at {chosen_height}p...{RESET}")
    downloaded = []
    for u in urls:
        try:
            res = yw.download_video(u, max_height=chosen_height)
            if res:
                downloaded.append(res)
        except Exception as e:
            print(f"{RED}Error downloading {u}: {e}{RESET}")

    if downloaded:
        print(f"\n{GREEN}{BOLD}Uploading {len(downloaded)} video(s) to {get_dest_name(dest)}...{RESET}")
        yt_service = yw.get_youtube_service() if dest in ["youtube", "both", "all"] else None
        ig_client = yw.get_instagram_client(interactive=True) if dest in ["instagram", "instagram_only", "both", "all"] else None

        success = 0
        failed = 0
        for i, item in enumerate(downloaded, 1):
            vid = item.get("video_id")
            title = item.get("title") or vid
            print(f"\n[{i}/{len(downloaded)}] Starting upload for: {title}")
            yw.update_video_status(vid, "uploading")
            try:
                yw.upload_item(item, destination=dest, yt_service=yt_service, ig_client=ig_client, privacy_status="public")
                success += 1
            except Exception as e:
                print(f"  {RED}Upload error for {vid}: {e}{RESET}")
                yw.update_video_status(vid, "failed", error=str(e))
                failed += 1
        print("\n" + "=" * 50)
        print(f"Upload Batch Complete: {success} Successful, {failed} Failed")
        print("=" * 50)
    else:
        print(f"\n{YELLOW}No videos were eligible for upload (skipped or failed).{RESET}")

    input(f"\n{BOLD}Press Enter to return to Instagram menu...{RESET}")


def run_instagram_option_2():
    """
    INSTAGRAM OPTION 2 — DOWNLOAD FULL PROFILE + UPLOAD
    - Ask for Instagram profile URL or username.
    - Fetch profile reels and determine count.
    - Select quality: 720p, 480p, 360p, or 180p.
    - Long videos (2h–20h+) supported with user confirmation.
    - Download and upload to YouTube as public.
    """
    clear_screen()
    print_banner("Option 2: Download Full Profile + Upload", platform="instagram")

    raw_input = ask_text("Enter Instagram Profile URL or Username (e.g. https://www.instagram.com/username/ or @username, or blank to cancel):", allow_empty=True).strip()
    if not raw_input or raw_input.lower() in ["cancel", "back"]:
        return
    profile_url = normalize_ig_profile(raw_input)

    print(f"\n{CYAN}Fetching full Instagram profile contents from {profile_url}...{RESET}")
    try:
        videos = yw.get_all_channel_videos(profile_url)
    except Exception as e:
        print(f"{RED}Failed to fetch Instagram profile: {e}{RESET}")
        input(f"\n{BOLD}Press Enter to return to Instagram menu...{RESET}")
        return

    if not videos:
        print(f"{YELLOW}No reels/videos found on this profile.{RESET}")
        input(f"\n{BOLD}Press Enter to return to Instagram menu...{RESET}")
        return

    long_videos = [v for v in videos if v.get("duration") and v.get("duration") >= 7200]
    std_videos = [v for v in videos if not v.get("duration") or v.get("duration") < 7200]

    print(f"\n{GREEN}{BOLD}Instagram Profile Scan Complete:{RESET}")
    print(f"  • Total items found: {len(videos)}")
    print(f"  • Standard items (< 2h): {len(std_videos)}")
    if long_videos:
        print(f"  • Long items (≥ 2h, e.g. 11h–20h+): {len(long_videos)}")

    eligible_videos = videos
    confirm_each_long = False

    if long_videos:
        handle_options = [
            f"1. Include all items (both standard and {len(long_videos)} long item(s))",
            f"2. Ask confirmation for each long item before downloading",
            f"3. Skip long items (process only {len(std_videos)} standard item(s))",
            "4. Cancel and return to menu"
        ]
        h_idx = select_menu(f"Found {len(long_videos)} item(s) 2 hours or longer (up to 20h+). How would you like to proceed?", handle_options)
        if h_idx in [3, -1]:
            return
        elif h_idx == 0:
            eligible_videos = videos
            confirm_each_long = False
        elif h_idx == 1:
            eligible_videos = videos
            confirm_each_long = True
        elif h_idx == 2:
            eligible_videos = std_videos
            confirm_each_long = False

    if not eligible_videos:
        print(f"{YELLOW}No items selected to process.{RESET}")
        input(f"\n{BOLD}Press Enter to return to Instagram menu...{RESET}")
        return

    quality_options = ["720p (High Quality)", "480p (Standard)", "360p (Low)", "180p (Fastest)"]
    q_idx = select_menu("Select Download Quality:", quality_options)
    if q_idx == -1:
        return
    height_map = [720, 480, 360, 180]
    chosen_height = height_map[q_idx]

    dest = choose_upload_destination(len(eligible_videos), default_platform="instagram")
    if not dest:
        return

    confirm_options = [f"Yes, download and upload {len(eligible_videos)} item(s) to {get_dest_name(dest)}", "Cancel and return to menu"]
    c_idx = select_menu(f"Proceed with downloading & uploading {len(eligible_videos)} item(s)?", confirm_options)
    if c_idx != 0:
        return

    yt_service = yw.get_youtube_service() if dest in ["youtube", "both", "all"] else None
    ig_client = yw.get_instagram_client(interactive=True) if dest in ["instagram", "instagram_only", "both", "all"] else None
    if dest in ["instagram", "instagram_only", "both", "all"] and not ig_client:
        print(f"{RED}Instagram login cancelled or failed. Returning to menu.{RESET}")
        input(f"\n{BOLD}Press Enter to return to Instagram menu...{RESET}")
        return

    downloaded = []
    for i, v in enumerate(eligible_videos, 1):
        vid = v.get("id")
        title = v.get("title", vid)
        url = v.get("webpage_url") or v.get("url")
        if not url or not str(url).startswith("http"):
            url = f"https://www.instagram.com/reel/{vid}/"
        print(f"\n{CYAN}[{i}/{len(eligible_videos)}] Processing: {title}{RESET}")
        try:
            res = yw.download_video(url, max_height=chosen_height, confirm_long=confirm_each_long)
            if res:
                downloaded.append(res)
                yw.upload_item(res, destination=dest, yt_service=yt_service, ig_client=ig_client, privacy_status="public")
        except Exception as e:
            print(f"{RED}Error processing {vid}: {e}{RESET}")

    print(f"\n{GREEN}{BOLD}Profile processing complete! Uploaded {len(downloaded)} reel(s) to {get_dest_name(dest)}.{RESET}")
    input(f"\n{BOLD}Press Enter to return to Instagram menu...{RESET}")


def run_instagram_option_3():
    """
    INSTAGRAM OPTION 3 — ADD PROFILE / MULTI-PROFILE DOWNLOAD + UPLOAD
    - Add up to 10 Instagram Profile URLs or usernames one by one.
    - Choose top most-viewed/recent reels per profile (1-10).
    - Select quality: 720p, 480p, 360p, or 180p.
    - Long videos (2h–20h+) supported with user confirmation.
    - Download and upload to YouTube as public.
    """
    profiles = []
    status_msg = ""

    while True:
        clear_screen()
        print_banner("Option 3: Multi-Profile Download + Upload", platform="instagram")
        if status_msg:
            print(f"{status_msg}\n")
            status_msg = ""

        print(f"{BOLD}Currently Added Instagram Profiles ({len(profiles)}/10):{RESET}")
        if not profiles:
            print(f"  {YELLOW}(No profiles added yet){RESET}\n")
        else:
            for idx, p in enumerate(profiles, 1):
                print(f"  {CYAN}{idx}.{RESET} [INSTAGRAM] {p}")
            print()

        sub_options = []
        if len(profiles) < 10:
            sub_options.append("Add Profile URL or Username")
        if profiles:
            sub_options.append(f"Done / Continue ({len(profiles)} profile(s))")
            sub_options.append("Remove Last Profile")
            sub_options.append("Clear All Profiles")
        sub_options.append("Back to Instagram Menu")

        choice_idx = select_menu("Choose an action:", sub_options)
        if choice_idx == -1:
            return
        choice = sub_options[choice_idx]

        if choice == "Add Profile URL or Username":
            raw_url = ask_text("Enter Instagram Profile URL or Username (or blank to cancel):", allow_empty=True).strip()
            if not raw_url or raw_url.lower() in ["cancel", "back"]:
                continue
            norm = normalize_ig_profile(raw_url)
            profiles.append(norm)
            status_msg = f"{GREEN}✔ [INSTAGRAM] Profile added!{RESET}"
        elif choice.startswith("Done / Continue"):
            break
        elif choice == "Remove Last Profile":
            if profiles:
                removed = profiles.pop()
                status_msg = f"{YELLOW}Removed: {removed}{RESET}"
        elif choice == "Clear All Profiles":
            profiles.clear()
            status_msg = f"{YELLOW}Cleared all profiles.{RESET}"
        elif choice == "Back to Instagram Menu":
            return

    if not profiles:
        print(f"{YELLOW}No profiles to process.{RESET}")
        return

    count_options = [f"{i} reel{'s' if i > 1 else ''} per profile" for i in range(1, 11)]
    cnt_idx = select_menu("How many top most-viewed reels to process from EACH profile?", count_options, default_index=1)
    if cnt_idx == -1:
        return
    reels_per_profile = cnt_idx + 1

    quality_options = ["720p (High Quality)", "480p (Standard)", "360p (Low)", "180p (Fastest)"]
    q_idx = select_menu("Select Download Quality:", quality_options)
    if q_idx == -1:
        return
    height_map = [720, 480, 360, 180]
    chosen_height = height_map[q_idx]

    dest = choose_upload_destination(len(profiles) * reels_per_profile, default_platform="instagram")
    if not dest:
        return

    yt_service = yw.get_youtube_service() if dest in ["youtube", "both", "all"] else None
    ig_client = yw.get_instagram_client(interactive=True) if dest in ["instagram", "instagram_only", "both", "all"] else None
    if dest in ["instagram", "instagram_only", "both", "all"] and not ig_client:
        print(f"{RED}Instagram login cancelled or failed. Returning to menu.{RESET}")
        input(f"\n{BOLD}Press Enter to return to Instagram menu...{RESET}")
        return

    total_uploaded = 0

    for pr_idx, pr_url in enumerate(profiles, 1):
        print(f"\n{BLUE}{BOLD}══════════════════════════════════════════════════════════════{RESET}")
        print(f"{CYAN}{BOLD}[Instagram Profile {pr_idx}/{len(profiles)}] Scanning: {pr_url}{RESET}")
        print(f"{BLUE}{BOLD}══════════════════════════════════════════════════════════════{RESET}")

        try:
            all_vids = yw.get_channel_videos(pr_url, max_videos=50)
            top_vids = all_vids[:reels_per_profile]

            print(f"Found {len(all_vids)} reels total. Selecting top {len(top_vids)} most-viewed:")
            for rank, v in enumerate(top_vids, 1):
                dur = v.get("duration") or 0
                views = v.get("view_count") or 0
                title = v.get("title", "Unknown")[:50]
                dur_str = yw.format_duration(dur)
                print(f"  #{rank}: {views:,} views | {dur_str} | {title}")

            for v in top_vids:
                vid = v.get("id")
                v_url = v.get("webpage_url") or v.get("url")
                if not v_url or not str(v_url).startswith("http"):
                    v_url = f"https://www.instagram.com/reel/{vid}/"
                try:
                    res = yw.download_video(v_url, max_height=chosen_height, confirm_long=True)
                    if res:
                        yw.upload_item(res, destination=dest, yt_service=yt_service, ig_client=ig_client, privacy_status="public")
                        total_uploaded += 1
                except Exception as e:
                    print(f"{RED}Error processing {vid}: {e}{RESET}")

        except Exception as e:
            print(f"{RED}Failed to process {pr_url}: {e}{RESET}")

    print(f"\n{GREEN}{BOLD}Multi-Profile Processing Complete! Total uploaded: {total_uploaded} reel(s) to {get_dest_name(dest)}.{RESET}")
    input(f"\n{BOLD}Press Enter to return to Instagram menu...{RESET}")


def run_instagram_option_4():
    """
    INSTAGRAM OPTION 4 — PLAYLIST / COLLECTION DOWNLOAD + UPLOAD
    - Ask for Instagram collection, audio, or tag URL.
    - Fetch collection and show count.
    - Select quality: 720p, 480p, 360p, or 180p.
    - Long videos (2h–20h+) supported with user confirmation.
    - Download and upload to YouTube as public.
    """
    clear_screen()
    print_banner("Option 4: Playlist / Collection Download + Upload", platform="instagram")

    collection_url = ask_text("Enter Instagram Collection, Audio, or Tag URL (e.g. https://www.instagram.com/reels/audio/..., or blank to cancel):", allow_empty=True).strip()
    if not collection_url or collection_url.lower() in ["cancel", "back"]:
        return

    print(f"\n{CYAN}Fetching collection contents from {collection_url}...{RESET}")
    try:
        videos = yw.get_playlist_videos(collection_url)
    except Exception as e:
        print(f"{RED}Failed to fetch collection: {e}{RESET}")
        input(f"\n{BOLD}Press Enter to return to Instagram menu...{RESET}")
        return

    if not videos:
        print(f"{YELLOW}No reels/videos found in this collection.{RESET}")
        input(f"\n{BOLD}Press Enter to return to Instagram menu...{RESET}")
        return

    long_videos = [v for v in videos if v.get("duration") and v.get("duration") >= 7200]
    std_videos = [v for v in videos if not v.get("duration") or v.get("duration") < 7200]

    print(f"\n{GREEN}{BOLD}Collection Scan Complete:{RESET}")
    print(f"  • Total items found: {len(videos)}")
    print(f"  • Standard items (< 2h): {len(std_videos)}")
    if long_videos:
        print(f"  • Long items (≥ 2h, e.g. 11h–20h+): {len(long_videos)}")

    eligible_videos = videos
    confirm_each_long = False

    if long_videos:
        handle_options = [
            f"1. Include all items (both standard and {len(long_videos)} long item(s))",
            f"2. Ask confirmation for each long item before downloading",
            f"3. Skip long items (process only {len(std_videos)} standard item(s))",
            "4. Cancel and return to menu"
        ]
        h_idx = select_menu(f"Found {len(long_videos)} item(s) 2 hours or longer (up to 20h+). How would you like to proceed?", handle_options)
        if h_idx in [3, -1]:
            return
        elif h_idx == 0:
            eligible_videos = videos
            confirm_each_long = False
        elif h_idx == 1:
            eligible_videos = videos
            confirm_each_long = True
        elif h_idx == 2:
            eligible_videos = std_videos
            confirm_each_long = False

    if not eligible_videos:
        print(f"{YELLOW}No items selected to process.{RESET}")
        input(f"\n{BOLD}Press Enter to return to Instagram menu...{RESET}")
        return

    quality_options = ["720p (High Quality)", "480p (Standard)", "360p (Low)", "180p (Fastest)"]
    q_idx = select_menu("Select Download Quality:", quality_options)
    if q_idx == -1:
        return
    height_map = [720, 480, 360, 180]
    chosen_height = height_map[q_idx]

    dest = choose_upload_destination(len(eligible_videos), default_platform="instagram")
    if not dest:
        return

    confirm_options = [f"Yes, download and upload {len(eligible_videos)} item(s) to {get_dest_name(dest)}", "Cancel and return to menu"]
    c_idx = select_menu(f"Proceed with downloading & uploading {len(eligible_videos)} item(s)?", confirm_options)
    if c_idx != 0:
        return

    yt_service = yw.get_youtube_service() if dest in ["youtube", "both", "all"] else None
    ig_client = yw.get_instagram_client(interactive=True) if dest in ["instagram", "instagram_only", "both", "all"] else None
    if dest in ["instagram", "instagram_only", "both", "all"] and not ig_client:
        print(f"{RED}Instagram login cancelled or failed. Returning to menu.{RESET}")
        input(f"\n{BOLD}Press Enter to return to Instagram menu...{RESET}")
        return

    downloaded = []
    for i, v in enumerate(eligible_videos, 1):
        vid = v.get("id")
        title = v.get("title", vid)
        url = v.get("webpage_url") or v.get("url")
        if not url or not str(url).startswith("http"):
            url = f"https://www.instagram.com/reel/{vid}/"
        print(f"\n{CYAN}[{i}/{len(eligible_videos)}] Processing: {title}{RESET}")
        try:
            res = yw.download_video(url, max_height=chosen_height, confirm_long=confirm_each_long)
            if res:
                downloaded.append(res)
                yw.upload_item(res, destination=dest, yt_service=yt_service, ig_client=ig_client, privacy_status="public")
        except Exception as e:
            print(f"{RED}Error processing {vid}: {e}{RESET}")

    print(f"\n{GREEN}{BOLD}Collection processing complete! Uploaded {len(downloaded)} video(s) to {get_dest_name(dest)}.{RESET}")
    input(f"\n{BOLD}Press Enter to return to Instagram menu...{RESET}")


def instagram_menu():
    """Complete Instagram Workflow Menu (same structure as YouTube)."""
    options = [
        "1. Multi-Download + Upload",
        "2. Download Full Profile + Upload",
        "3. Add Profile / Multi-Profile Download + Upload",
        "4. Playlist / Collection Download + Upload",
        "5. Upload Local Video File (to Instagram + Facebook, YouTube, or All)",
        "6. Queue Status & Database Summary",
        "7. Back to Main Menu"
    ]

    while True:
        clear_screen()
        print_banner("Instagram Automation Workflow", platform="instagram")
        idx = select_menu("Instagram Workflow Menu:", options)

        if idx == 0:
            run_instagram_option_1()
        elif idx == 1:
            run_instagram_option_2()
        elif idx == 2:
            run_instagram_option_3()
        elif idx == 3:
            run_instagram_option_4()
        elif idx == 4:
            run_upload_local_video(platform="instagram")
        elif idx == 5:
            show_queue_status("instagram")
        elif idx in [6, -1]:
            break


# =============================================================================
# SETTINGS
# =============================================================================

def facebook_settings_menu():
    """Facebook Direct (Headless/Cookies) & Meta Accounts Center Cross-Posting configuration."""
    import facebook_uploader as fu

    while True:
        clear_screen()
        print_banner("Facebook Automation & Cross-Posting Settings", platform="settings")
        fb_cfg = yw.get_facebook_config()
        auto_share = "ENABLED (ON)" if fb_cfg.get("auto_share_to_facebook", True) else "DISABLED (OFF)"
        
        # Graph API Status
        g_ready = fu.is_facebook_graph_configured()
        g_info = fu.check_facebook_graph_api() if g_ready else {}
        if g_info.get("valid"):
            g_status = f"{GREEN}CONNECTED (Page: {g_info.get('name')} [ID: {g_info.get('page_id')}]{RESET}"
        elif g_ready:
            g_status = f"{YELLOW}TOKEN ERROR: {g_info.get('error')}{RESET}"
        else:
            g_status = f"{YELLOW}NOT CONFIGURED{RESET}"

        # Headless Cookies Status
        direct_ready = fu.is_facebook_cookies_configured()
        direct_status = f"{GREEN}COOKIES CONFIGURED{RESET}" if direct_ready else f"{YELLOW}NO COOKIES (NOT CONFIGURED){RESET}"

        print(f"{BOLD}Facebook Connection Methods Available:{RESET}")
        print(f"\n{CYAN}[Method 1] Official Meta Graph API (Fastest & Most Reliable for Pages):{RESET}")
        print(f"  • Status : {g_status}")
        print(f"  • Mode   : Direct REST API upload with Page Access Token")
        print(f"  • Target : Facebook Page (Reels & Videos)")

        print(f"\n{CYAN}[Method 2] Direct Facebook Headless Upload (For Personal Profiles / No Graph API):{RESET}")
        print(f"  • Status : {direct_status}")
        print(f"  • Mode   : Headless browser automation with saved session cookies")
        print(f"  • Target : Direct upload to your personal Facebook Profile as Reel")

        print(f"\n{CYAN}[Method 3] Meta Accounts Center Cross-Posting (via Instagram):{RESET}")
        print(f"  • Status : {GREEN if 'ENABLED' in auto_share else RED}{auto_share}{RESET}")
        print(f"  • Mode   : Instagram Reel API automatically mirrors Reel to personal Facebook")

        options = [
            "1. Verify Meta Graph API Connection (Check Page & Token)",
            "2. Setup / Paste Facebook Session Cookies (Headless)",
            "3. Test Facebook Session (Verify via Headless Browser)",
            "4. Test Live Video Upload (Upload test clip to Facebook)",
            "5. Enter c_user and xs cookies manually",
            "6. Log in with Password (Auto-extract cookies)",
            "7. Toggle Instagram -> Facebook Auto Cross-Post",
            "8. Guide: How to Export Facebook Cookies in 30 Seconds",
            "9. Back to Settings"
        ]
        idx = select_menu("Choose an action:", options)

        if idx == 0:
            clear_screen()
            print_banner("Verify Meta Graph API Connection", platform="settings")
            print(f"{CYAN}Querying Facebook Graph API for Page & Token details...{RESET}")
            res = fu.check_facebook_graph_api()
            if res.get("valid"):
                print(f"\n{GREEN}{BOLD}✔ Facebook Graph API Connected & Valid!{RESET}")
                print(f"  • Page Name : {res.get('name')}")
                print(f"  • Page ID   : {res.get('page_id')}")
                print(f"  • Page Link : {res.get('link')}")
                print(f"\n{GREEN}All Facebook uploads will automatically use official Graph API.{RESET}")
            else:
                print(f"\n{RED}{BOLD}✖ Graph API Validation Failed:{RESET} {res.get('error')}")
            input(f"\n{BOLD}Press Enter to continue...{RESET}")

        elif idx == 1:
            clear_screen()
            print_banner("Setup Facebook Cookies", platform="settings")
            print(f"{BOLD}Paste your Facebook cookies below:{RESET}")
            print("  • Accepts JSON array (from 'Cookie-Editor' or 'EditThisCookie' extensions)")
            print("  • Accepts cookies.txt Netscape format")
            print("  • Accepts raw cookie string (e.g. c_user=...; xs=...)\n")
            raw_cookies = ask_text("Paste cookie text (or blank to cancel):", allow_empty=True).strip()
            if raw_cookies and raw_cookies.lower() not in ["cancel", "back"]:
                ok = fu.parse_and_save_facebook_cookies(raw_cookies)
                if ok:
                    print(f"\n{GREEN}✔ Facebook cookies parsed and saved successfully!{RESET}")
                    print("Verifying session with headless browser...")
                    res = fu.check_facebook_session()
                    if res.get("logged_in"):
                        print(f"{GREEN}✔ Successfully connected to Facebook! Logged in as: {res.get('name')}{RESET}")
                    else:
                        print(f"{YELLOW}[Notice] Cookies saved, but verification note: {res.get('error')}{RESET}")
                else:
                    print(f"\n{RED}Failed to parse cookies. Please check format and try again.{RESET}")
                input(f"\n{BOLD}Press Enter to continue...{RESET}")

        elif idx == 2:
            clear_screen()
            print_banner("Test Facebook Session", platform="settings")
            print(f"{CYAN}Testing Facebook session with Playwright headless browser...{RESET}")
            res = fu.check_facebook_session(timeout_sec=30)
            if res.get("logged_in"):
                print(f"\n{GREEN}{BOLD}✔ Facebook Session Active!{RESET}")
                print(f"  • Account Name : {res.get('name')}")
                print(f"  • User ID      : {res.get('user_id')}")
                print(f"  • Home URL     : {res.get('url')}")
            else:
                print(f"\n{RED}{BOLD}✖ Facebook Session Inactive:{RESET} {res.get('error')}")
            input(f"\n{BOLD}Press Enter to continue...{RESET}")

        elif idx == 3:
            clear_screen()
            print_banner("Test Live Video Upload", platform="settings")
            print(f"{CYAN}Generating sample clip and testing live Facebook upload...{RESET}")
            import subprocess
            test_clip = Path("/root/youtube-workflow/test_reel.mp4")
            if not test_clip.exists():
                subprocess.run([
                    "ffmpeg", "-threads", "1", "-y", "-f", "lavfi", "-i", "testsrc=duration=3:size=360x640:rate=24",
                    "-f", "lavfi", "-i", "sine=frequency=1000:duration=3", "-c:v", "libx264", "-preset", "ultrafast",
                    "-threads", "1", "-c:a", "aac", "-pix_fmt", "yuv420p", str(test_clip)
                ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                upload_res = fu.upload_facebook(str(test_clip), caption="Automated Test Video from Settings Menu #workflow")
                print(f"\n{GREEN}{BOLD}✔ Facebook Video Upload Successful!{RESET}")
                print(f"  • Mode       : {upload_res.get('mode')}")
                print(f"  • URL        : {upload_res.get('url')}")
                if upload_res.get("screenshot"):
                    print(f"  • Screenshot : {upload_res.get('screenshot')}")
            except Exception as e:
                print(f"\n{RED}{BOLD}✖ Test upload failed:{RESET} {e}")
            input(f"\n{BOLD}Press Enter to continue...{RESET}")

        elif idx == 4:
            clear_screen()
            print_banner("Manual c_user & xs Entry", platform="settings")
            print("Enter the two core Facebook cookies:")
            c_user = ask_text("Enter c_user (Facebook User ID, e.g. 1000...):", allow_empty=True).strip()
            xs = ask_text("Enter xs (Facebook session token):", allow_empty=True).strip()
            if c_user and xs:
                raw_str = f"c_user={c_user}; xs={xs};"
                ok = fu.parse_and_save_facebook_cookies(raw_str)
                if ok:
                    print(f"\n{GREEN}✔ Saved c_user and xs cookies!{RESET}")
                    print("Testing session...")
                    res = fu.check_facebook_session()
                    if res.get("logged_in"):
                        print(f"{GREEN}✔ Successfully verified: {res.get('name')}{RESET}")
                    else:
                        print(f"{YELLOW}Note: {res.get('error')}{RESET}")
                else:
                    print(f"{RED}Failed to save cookies.{RESET}")
            input(f"\n{BOLD}Press Enter to continue...{RESET}")

        elif idx == 5:
            clear_screen()
            print_banner("Facebook Password Login", platform="settings")
            print(f"{CYAN}This unlocks your account (Kazuto Kirigaya) and extracts fresh session cookies automatically.{RESET}")
            import getpass
            fb_pass = getpass.getpass("Enter your Facebook password: ").strip()
            if fb_pass:
                print(f"\n{CYAN}Logging into Facebook and capturing session cookies...{RESET}")
                res = fu.login_with_password(fb_pass)
                if res.get("success"):
                    print(f"\n{GREEN}{BOLD}✔ Facebook login successful! Captured {res.get('cookies_count')} cookies.{RESET}")
                    check = fu.check_facebook_session()
                    if check.get("logged_in"):
                        print(f"{GREEN}✔ Verified session: {check.get('name')}{RESET}")
                else:
                    print(f"\n{RED}✖ Login failed: {res.get('error')}{RESET}")
            else:
                print(f"{YELLOW}Cancelled.{RESET}")
            input(f"\n{BOLD}Press Enter to continue...{RESET}")

        elif idx == 6:
            curr = fb_cfg.get("auto_share_to_facebook", True)
            new_val = not curr
            yw.save_facebook_config({"auto_share_to_facebook": new_val})
            print(f"\n{GREEN}✔ Auto-Share to Facebook Account is now {'ENABLED' if new_val else 'DISABLED'}!{RESET}")
            input(f"\n{BOLD}Press Enter to continue...{RESET}")

        elif idx == 7:
            clear_screen()
            print_banner("How to Export Facebook Cookies", platform="settings")
            print(f"""{BOLD}HOW TO EXPORT FACEBOOK COOKIES IN 30 SECONDS:{RESET}

{CYAN}Step 1: Install a Cookie Extension in your browser (Chrome/Edge/Brave/Firefox){RESET}
  • Recommended extension: {BOLD}Cookie-Editor{RESET} (available in Chrome Web Store & Firefox Add-ons)
  • URL: https://cookie-editor.com/

{CYAN}Step 2: Export Cookies from Facebook{RESET}
  • Open your browser and go to {BOLD}https://www.facebook.com{RESET} (ensure you are logged in).
  • Click the {BOLD}Cookie-Editor{RESET} icon in your browser toolbar.
  • Click the {BOLD}Export{RESET} button at the bottom right -> select {BOLD}Export as JSON{RESET} (or Header string).

{CYAN}Step 3: Paste into this tool{RESET}
  • Go to {BOLD}Settings -> Facebook Settings -> Setup / Paste Facebook Session Cookies{RESET}.
  • Paste the copied JSON or text and hit Enter!
  • The tool will immediately verify your login and enable direct uploads!
""")
            input(f"\n{BOLD}Press Enter to return...{RESET}")

        elif idx in [8, -1]:
            break


def settings_menu():
    """Credentials, cookies, and database settings."""
    options = [
        "1. YouTube OAuth Credentials & Channel Status",
        "2. Instagram Account Login & Status",
        "3. Facebook Direct & Cross-Posting Settings",
        "4. Instagram Cookies Configuration",
        "5. YouTube Cookies Configuration",
        "6. Queue Status & Database Summary",
        "7. Back to Main Menu"
    ]

    while True:
        clear_screen()
        print_banner("Workflow Settings", platform="settings")
        idx = select_menu("Settings Menu:", options)

        if idx == 0:
            clear_screen()
            print_banner("YouTube OAuth Status", platform="settings")
            yw.cmd_setup_oauth(argparse.Namespace())
            input(f"\n{BOLD}Press Enter to return to settings...{RESET}")
        elif idx == 1:
            clear_screen()
            print_banner("Instagram Account Login & Status", platform="settings")
            status = yw.get_instagram_account_status()
            if status["logged_in"]:
                print(f"{GREEN}{BOLD}Current Status: Logged in as @{status['username']}{RESET}")
                sub_opts = [
                    "Test connection / Re-login with account",
                    "Log out & clear Instagram session",
                    "Back to Settings"
                ]
                sub = select_menu("Choose an action:", sub_opts)
                if sub == 0:
                    yw.get_instagram_client(interactive=True)
                    input(f"\n{BOLD}Press Enter to return to settings...{RESET}")
                elif sub == 1:
                    yw.cmd_setup_instagram(argparse.Namespace(logout=True))
                    input(f"\n{BOLD}Press Enter to return to settings...{RESET}")
            else:
                print(f"{YELLOW}{BOLD}Current Status: Not logged in to Instagram{RESET}")
                sub_opts = [
                    "Log in to Instagram (Username & Password)",
                    "Back to Settings"
                ]
                sub = select_menu("Choose an action:", sub_opts)
                if sub == 0:
                    yw.get_instagram_client(interactive=True)
                    input(f"\n{BOLD}Press Enter to return to settings...{RESET}")
        elif idx == 2:
            facebook_settings_menu()
        elif idx == 3:
            clear_screen()
            print_banner("Instagram Cookies Setup", platform="settings")
            yw.cmd_setup_cookies(argparse.Namespace(instagram_file=None))
            sub = select_menu("Choose an action:", [
                "Import/Copy Instagram cookies from file path",
                "Back to Settings"
            ])
            if sub == 0:
                p = ask_text("Enter path to exported cookies.txt file (or blank to cancel):", allow_empty=True).strip()
                if p and p.lower() not in ["cancel", "back"]:
                    yw.cmd_setup_cookies(argparse.Namespace(instagram_file=p))
                    input(f"\n{BOLD}Press Enter to return to settings...{RESET}")
        elif idx == 4:
            clear_screen()
            print_banner("YouTube Cookies Setup", platform="settings")
            print(f"YouTube Cookies file : {yw.COOKIES_PATH} ({'EXISTS' if yw.COOKIES_PATH.exists() else 'MISSING'})")
            sub = select_menu("Choose an action:", [
                "Import/Copy YouTube cookies from file path",
                "Back to Settings"
            ])
            if sub == 0:
                p = ask_text("Enter path to exported cookies.txt file (or blank to cancel):", allow_empty=True).strip()
                if p and p.lower() not in ["cancel", "back"]:
                    if Path(p).exists():
                        shutil.copy(p, str(yw.COOKIES_PATH))
                        print(f"{GREEN}Copied YouTube cookies from {p} to {yw.COOKIES_PATH}{RESET}")
                    else:
                        print(f"{RED}File not found: {p}{RESET}")
                    input(f"\n{BOLD}Press Enter to return to settings...{RESET}")
        elif idx == 5:
            show_queue_status()
        elif idx in [6, -1]:
            break


# =============================================================================
# MAIN ENTRYPOINT
# =============================================================================

def main_menu():
    """
    Main platform selection menu:
      - YouTube
      - Instagram
      - Settings
      - Exit
    """
    yw.init_db()

    main_options = [
        "YouTube",
        "Instagram",
        "Settings",
        "Exit"
    ]

    while True:
        clear_screen()
        print_banner("Platform Selection")
        idx = select_menu("Select a platform or option:", main_options)

        if idx == 0:
            youtube_menu()
        elif idx == 1:
            instagram_menu()
        elif idx == 2:
            settings_menu()
        elif idx in [3, -1]:
            print(f"\n{CYAN}Exiting Multi-Platform Workflow Automation. Goodbye!{RESET}\n")
            break


if __name__ == "__main__":
    try:
        main_menu()
    except KeyboardInterrupt:
        print(f"\n\n{YELLOW}Program interrupted. Exiting cleanly.{RESET}\n")
        sys.exit(0)
