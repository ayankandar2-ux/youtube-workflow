#!/usr/bin/env python3
"""
YouTube Video Automation Workflow
Downloads videos from YouTube channels/URLs and uploads them to your YouTube channel.
Supports channel ranking by view count, deduplication, batch queue, retry logic, and metadata preservation.
"""

import argparse
import glob
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import warnings
warnings.filterwarnings("ignore", category=FutureWarning)
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
from uuid import uuid4

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    from googleapiclient.errors import HttpError
except ImportError as e:
    print(f"Missing dependency: {e}")
    print("Run: pip3 install google-auth google-auth-oauthlib google-api-python-client yt-dlp tqdm")
    sys.exit(1)


# =============================================================================
# CONFIGURATION & PATHS
# =============================================================================

WORKFLOW_DIR = Path.home() / "youtube-workflow"
DOWNLOADS_DIR = WORKFLOW_DIR / "downloads"
METADATA_DIR = WORKFLOW_DIR / "metadata"
QUEUE_DIR = WORKFLOW_DIR / "queue"
THUMBNAILS_DIR = WORKFLOW_DIR / "thumbnails"
LOGS_DIR = WORKFLOW_DIR / "logs"

for d in [DOWNLOADS_DIR, METADATA_DIR, QUEUE_DIR, THUMBNAILS_DIR, LOGS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

DB_PATH = WORKFLOW_DIR / "workflow.db"
COOKIES_PATH = WORKFLOW_DIR / "cookies.txt"
INSTAGRAM_COOKIES_PATH = WORKFLOW_DIR / "instagram_cookies.txt"
INSTAGRAM_SESSION_PATH = Path.home() / ".hermes" / "instagram_session.json"
INSTAGRAM_CREDS_PATH = Path.home() / ".hermes" / "instagram_creds.json"
INSTAGRAM_CONFIG_PATH = Path("/root/instagram_config.json")
INSTAGRAM_CONFIG_LOCAL = WORKFLOW_DIR / "instagram_config.json"
FACEBOOK_CONFIG_PATH = Path("/root/facebook_config.json")
FACEBOOK_CONFIG_LOCAL = WORKFLOW_DIR / "facebook_config.json"
OAUTH_CREDENTIALS_PATH = Path.home() / ".hermes" / "youtube_client_secret.json"
OAUTH_TOKEN_PATH = Path.home() / ".hermes" / "youtube_token.json"

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly"
]
API_SERVICE_NAME = "youtube"
API_VERSION = "v3"


# =============================================================================
# DATABASE
# =============================================================================

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS videos (
            video_id TEXT PRIMARY KEY,
            source_url TEXT,
            platform TEXT DEFAULT 'youtube',
            title TEXT,
            description TEXT,
            tags TEXT,
            thumbnail_path TEXT,
            file_path TEXT,
            file_size INTEGER,
            duration INTEGER,
            upload_status TEXT DEFAULT 'pending',
            upload_error TEXT,
            upload_retry_count INTEGER DEFAULT 0,
            youtube_video_id TEXT,
            uploaded_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    try:
        c.execute("ALTER TABLE videos ADD COLUMN platform TEXT DEFAULT 'youtube'")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE videos ADD COLUMN instagram_media_id TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE videos ADD COLUMN upload_destination TEXT DEFAULT 'youtube'")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE videos ADD COLUMN facebook_post_id TEXT")
    except sqlite3.OperationalError:
        pass
    c.execute("""
        CREATE TABLE IF NOT EXISTS channel_cache (
            channel_id TEXT PRIMARY KEY,
            channel_name TEXT,
            video_data TEXT,
            fetched_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


def get_db():
    return sqlite3.connect(DB_PATH)


def video_exists_in_db(video_id: str) -> Optional[Dict]:
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT video_id, file_path, upload_status, youtube_video_id, title
        FROM videos WHERE video_id = ?
    """, (video_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return {
            "video_id": row[0],
            "file_path": row[1],
            "upload_status": row[2],
            "youtube_video_id": row[3],
            "title": row[4]
        }
    return None


def save_video_metadata(video_id: str, data: Dict):
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()
    tags = data.get("tags", [])
    tags_json = json.dumps(tags) if isinstance(tags, list) else json.dumps([])
    platform = data.get("platform") or detect_platform(data.get("source_url", "") or video_id)

    c.execute("""
        INSERT INTO videos
        (video_id, source_url, platform, title, description, tags, thumbnail_path, file_path, file_size, duration, upload_status, upload_error, upload_retry_count, youtube_video_id, uploaded_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(video_id) DO UPDATE SET
            platform = excluded.platform,
            title = excluded.title,
            description = excluded.description,
            tags = excluded.tags,
            thumbnail_path = COALESCE(excluded.thumbnail_path, thumbnail_path),
            file_path = COALESCE(excluded.file_path, file_path),
            file_size = COALESCE(excluded.file_size, file_size),
            duration = COALESCE(excluded.duration, duration),
            upload_status = excluded.upload_status,
            updated_at = excluded.updated_at
    """, (
        video_id,
        data.get("source_url"),
        platform,
        data.get("title"),
        data.get("description"),
        tags_json,
        data.get("thumbnail_path"),
        data.get("file_path"),
        data.get("file_size"),
        data.get("duration"),
        data.get("upload_status", "pending"),
        data.get("upload_error"),
        data.get("upload_retry_count", 0),
        data.get("youtube_video_id"),
        data.get("uploaded_at"),
        now
    ))
    conn.commit()
    conn.close()


def get_pending_videos(limit: int = 10, include_failed: bool = False) -> List[Dict]:
    conn = get_db()
    c = conn.cursor()
    statuses = ('pending', 'failed') if include_failed else ('pending',)
    placeholders = ",".join("?" for _ in statuses)
    c.execute(f"""
        SELECT video_id, source_url, title, description, tags, thumbnail_path, file_path, file_size, duration, upload_retry_count, upload_status
        FROM videos
        WHERE upload_status IN ({placeholders})
        ORDER BY created_at ASC
        LIMIT ?
    """, (*statuses, limit))
    rows = c.fetchall()
    conn.close()
    return [
        {
            "video_id": r[0],
            "source_url": r[1],
            "title": r[2],
            "description": r[3],
            "tags": json.loads(r[4]) if r[4] else [],
            "thumbnail_path": r[5],
            "file_path": r[6],
            "file_size": r[7],
            "duration": r[8],
            "upload_retry_count": r[9],
            "upload_status": r[10]
        }
        for r in rows
    ]


def update_video_status(video_id: str, status: str, error: str = None, youtube_video_id: str = None, instagram_media_id: str = None, facebook_post_id: str = None, upload_destination: str = None):
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()
    if status == "uploaded":
        c.execute("""
            UPDATE videos SET 
                upload_status = ?, 
                youtube_video_id = COALESCE(?, youtube_video_id), 
                instagram_media_id = COALESCE(?, instagram_media_id),
                facebook_post_id = COALESCE(?, facebook_post_id),
                upload_destination = COALESCE(?, upload_destination),
                upload_error = NULL, 
                uploaded_at = ?, 
                updated_at = ?
            WHERE video_id = ?
        """, (status, youtube_video_id, instagram_media_id, facebook_post_id, upload_destination, now, now, video_id))
    elif status == "failed":
        c.execute("""
            UPDATE videos SET upload_status = ?, upload_error = ?, upload_retry_count = upload_retry_count + 1, updated_at = ?
            WHERE video_id = ?
        """, (status, error, now, video_id))
    else:
        c.execute("""
            UPDATE videos SET upload_status = ?, updated_at = ? WHERE video_id = ?
        """, (status, now, video_id))
    conn.commit()
    conn.close()


# =============================================================================
# YOUTUBE DATA API - OAUTH
# =============================================================================

def get_youtube_service():
    creds = None
    if OAUTH_TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(OAUTH_TOKEN_PATH), SCOPES)
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("Refreshing expired YouTube OAuth access token...")
            creds.refresh(Request())
            OAUTH_TOKEN_PATH.write_text(creds.to_json())
        else:
            print("OAuth credentials not found or expired.")
            print(f"Please run: yw setup-oauth")
            sys.exit(1)
            
    return build(API_SERVICE_NAME, API_VERSION, credentials=creds)


# =============================================================================
# YT-DLP HELPERS & CHANNEL EXTRACTION
# =============================================================================

def detect_platform(url_or_id: str) -> str:
    """Detect whether a URL or ID belongs to YouTube or Instagram."""
    u = str(url_or_id).lower()
    if "instagram.com" in u or "instagr.am" in u or u.startswith("ig_"):
        return "instagram"
    return "youtube"


def extract_video_id(url_or_id: str) -> Optional[str]:
    """Extract YouTube or Instagram video / post ID."""
    url_str = str(url_or_id).strip()

    # Instagram shortcode (reel, reels, p, tv)
    ig_match = re.search(r"instagram\.com/(?:reel|reels|p|tv)/([a-zA-Z0-9_-]+)", url_str)
    if ig_match:
        return f"ig_{ig_match.group(1)}"
    if url_str.startswith("ig_"):
        return url_str

    # YouTube ID patterns
    patterns = [
        r'(?:v=|youtu\.be/|shorts/|embed/|live/)([a-zA-Z0-9_-]{11})',
        r'^([a-zA-Z0-9_-]{11})$',
    ]
    for pattern in patterns:
        match = re.search(pattern, url_str)
        if match:
            return match.group(1)
    return None


def extract_instagram_username(url_or_user: str) -> Optional[str]:
    """Extract clean Instagram username from a URL, handle, or string."""
    s = str(url_or_user).strip()
    if not s:
        return None
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
                    return None
                return path_parts[0]
        except Exception:
            pass
    s = s.split("?")[0].split("/")[0].strip()
    if s and re.match(r"^[a-zA-Z0-9._]+$", s):
        return s
    return None


def get_instagram_profile_videos(profile_url_or_user: str, max_videos: Optional[int] = None) -> List[Dict]:
    """
    Fetch reels/videos from an Instagram profile using authenticated instagrapi client,
    falling back to yt-dlp if instagrapi fails.
    """
    username = extract_instagram_username(profile_url_or_user)
    videos = []

    # 1. Try authenticated instagrapi client
    cl = get_instagram_client(interactive=False)
    if cl and username:
        try:
            print(f"Connecting to Instagram as @{cl.username or 'authenticated user'} to inspect @{username}...")
            user_info = cl.user_info_by_username(username)
            total_on_acc = getattr(user_info, 'media_count', 0)
            print(f"Profile: @{username} (Total media on account: {total_on_acc})")

            medias = []
            next_max_id = ""
            page = 1
            limit = max_videos if max_videos and max_videos > 0 else 500

            while True:
                sys.stdout.write(f"\r  • Fetching reels batch {page}... ({len(medias)} items collected so far)")
                sys.stdout.flush()
                try:
                    page_items, next_max_id = cl.user_clips_paginated_v1(user_info.pk, end_cursor=next_max_id)
                except Exception as page_err:
                    print(f"\n[Notice] Pagination completed or stopped: {page_err}")
                    break

                if not page_items:
                    break
                medias.extend(page_items)
                if not next_max_id or len(medias) >= limit:
                    break
                page += 1
                time.sleep(0.3)

            print(f"\r  ✔ Collected {len(medias)} reel(s) from @{username}. Processing metadata...              ")

            seen_codes = set()
            for c in medias:
                code = getattr(c, "code", "") or str(getattr(c, "pk", ""))
                if not code or code in seen_codes:
                    continue
                seen_codes.add(code)

                caption = getattr(c, "caption_text", "") or ""
                title = caption.strip().split("\n")[0][:100] if caption else f"Instagram Reel by @{username} [{code}]"
                duration = int(getattr(c, "video_duration", 0) or 0)
                view_count = int(getattr(c, "play_count", 0) or getattr(c, "view_count", 0) or 0)
                thumb_url = str(getattr(c, "thumbnail_url", "") or "")

                entry = {
                    "id": f"ig_{code}",
                    "code": code,
                    "title": title,
                    "description": caption,
                    "url": f"https://www.instagram.com/reel/{code}/",
                    "webpage_url": f"https://www.instagram.com/reel/{code}/",
                    "duration": duration,
                    "view_count": view_count,
                    "uploader": username,
                    "uploader_id": str(getattr(user_info, "pk", "")),
                    "platform": "instagram",
                    "thumbnail": thumb_url,
                    "media_type": "reel"
                }
                videos.append(entry)

            if max_videos and max_videos > 0:
                videos.sort(key=lambda v: (v.get("view_count") or 0), reverse=True)
                videos = videos[:max_videos]

            if videos:
                print(f"✔ Ready: {len(videos)} reel(s) loaded from Instagram profile @{username}.")
                return videos
        except Exception as e:
            print(f"\n[INSTAGRAM] instagrapi error for @{username}: {e}. Trying yt-dlp fallback...")

    # 2. Fallback to yt-dlp
    clean_url = f"https://www.instagram.com/{username}/" if username else str(profile_url_or_user).split("?")[0].rstrip("/") + "/"
    target_url = clean_url
    if not any(target_url.endswith(sub) for sub in ["/reels", "/reels/"]):
        target_url = target_url.rstrip("/") + "/reels"

    cmd = [
        "yt-dlp",
        "--no-warnings",
        "--flat-playlist",
        "--dump-json"
    ]
    if max_videos and max_videos > 0:
        cmd.extend(["--playlist-end", str(max_videos)])

    if INSTAGRAM_COOKIES_PATH.exists():
        cmd.extend(["--cookies", str(INSTAGRAM_COOKIES_PATH)])
    elif COOKIES_PATH.exists():
        cmd.extend(["--cookies", str(COOKIES_PATH)])
    cmd.extend(["--add-header", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"])
    cmd.append(target_url)

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 and target_url != clean_url:
        cmd[-1] = clean_url
        result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        err = result.stderr.strip()
        if "login" in err.lower() or "empty media" in err.lower():
            raise RuntimeError(f"Instagram requires authentication: {err}. Please configure Instagram login in Settings.")
        raise RuntimeError(f"Failed to fetch Instagram profile: {err}")

    seen_ids = set()
    for line in result.stdout.strip().split("\n"):
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            entry = json.loads(line)
            vid = entry.get("id")
            if vid and vid not in seen_ids:
                seen_ids.add(vid)
                entry["platform"] = "instagram"
                videos.append(entry)
        except json.JSONDecodeError:
            continue

    if max_videos and max_videos > 0:
        videos.sort(key=lambda v: (v.get("view_count") or 0), reverse=True)
    return videos


def get_video_info(url: str) -> Dict:
    """Get video metadata using yt-dlp without downloading."""
    platform = detect_platform(url)
    cmd = [
        "yt-dlp", "--no-warnings", "--skip-download",
        "--dump-json"
    ]
    if platform == "instagram":
        if INSTAGRAM_COOKIES_PATH.exists():
            cmd.extend(["--cookies", str(INSTAGRAM_COOKIES_PATH)])
        elif COOKIES_PATH.exists():
            cmd.extend(["--cookies", str(COOKIES_PATH)])
        cmd.extend(["--add-header", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"])

    cmd.append(url)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        err = result.stderr.strip()
        if platform == "instagram" and ("login" in err.lower() or "empty media" in err.lower()):
            raise RuntimeError(f"Instagram requires authentication: {err}. Place Instagram cookies in {INSTAGRAM_COOKIES_PATH}")
        raise RuntimeError(f"{platform.capitalize()} metadata fetch failed: {err}")
    
    for line in result.stdout.strip().split("\n"):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                info = json.loads(line)
                info["platform"] = platform
                return info
            except json.JSONDecodeError:
                continue
    raise RuntimeError(f"Failed to parse metadata JSON from yt-dlp")


def clean_youtube_channel_url(url_or_handle: str) -> str:
    """Clean and normalize YouTube channel URL, stripping query params, fragments, and sub-tabs."""
    s = str(url_or_handle).strip()
    if not (s.startswith("http://") or s.startswith("https://")):
        s = f"https://{s}"
    parsed = urllib.parse.urlparse(s)
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc or "www.youtube.com"
    path = parsed.path.rstrip("/")
    for tab in ["/videos", "/shorts", "/streams", "/featured", "/playlists", "/community", "/about"]:
        if path.endswith(tab):
            path = path[:-len(tab)]
            break
    return f"{scheme}://{netloc}{path}"


def get_channel_videos(channel_url: str, max_videos: int = 50) -> List[Dict]:
    """Get videos/reels from a YouTube channel or Instagram profile, sorted by view count descending."""
    platform = detect_platform(channel_url)
    if platform == "instagram":
        return get_instagram_profile_videos(channel_url, max_videos=max_videos)

    base_url = clean_youtube_channel_url(channel_url)
    target_url = f"{base_url}/videos"

    cmd = [
        "yt-dlp",
        "--no-warnings",
        "--flat-playlist",
        "--dump-json",
        "--playlist-end", str(max_videos)
    ]
    cmd.append(target_url)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        if target_url != channel_url:
            cmd[-1] = channel_url
            result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            err = result.stderr.strip()
            raise RuntimeError(f"Failed to fetch YouTube channel: {err}")

    videos = []
    seen_ids = set()
    for line in result.stdout.strip().split("\n"):
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            entry = json.loads(line)
            vid = entry.get("id")
            if vid and vid not in seen_ids:
                seen_ids.add(vid)
                entry["platform"] = "youtube"
                videos.append(entry)
        except json.JSONDecodeError:
            continue

    videos.sort(key=lambda v: (v.get("view_count") or 0), reverse=True)
    return videos


def get_playlist_videos(playlist_url: str) -> List[Dict]:
    """Fetch all video entries from a YouTube playlist or Instagram collection/audio."""
    platform = detect_platform(playlist_url)
    cmd = [
        "yt-dlp",
        "--no-warnings",
        "--flat-playlist",
        "--dump-json",
    ]
    if platform == "instagram":
        if INSTAGRAM_COOKIES_PATH.exists():
            cmd.extend(["--cookies", str(INSTAGRAM_COOKIES_PATH)])
        elif COOKIES_PATH.exists():
            cmd.extend(["--cookies", str(COOKIES_PATH)])
        cmd.extend(["--add-header", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"])

    cmd.append(playlist_url)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to fetch {platform} playlist/collection: {result.stderr.strip()}")

    videos = []
    seen_ids = set()
    for line in result.stdout.strip().split("\n"):
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            entry = json.loads(line)
            vid = entry.get("id")
            if vid and vid not in seen_ids:
                seen_ids.add(vid)
                entry["platform"] = platform
                videos.append(entry)
        except json.JSONDecodeError:
            continue
    return videos


def get_channel_videos_categorized(channel_url: str) -> Dict[str, List[Dict]]:
    """Fetch all videos from a YouTube channel or Instagram profile categorized into all, shorts, and long."""
    platform = detect_platform(channel_url)
    if platform == "instagram":
        all_vids = get_instagram_profile_videos(channel_url, max_videos=None)
        shorts = [v for v in all_vids if (v.get("duration") and v.get("duration") <= 60) or v.get("media_type") == "reel"]
        long_vids = [v for v in all_vids if v not in shorts]
        return {"all": all_vids, "shorts": shorts, "long": long_vids}

    base_url = clean_youtube_channel_url(channel_url)

    def _fetch_tab(target_url: str) -> List[Dict]:
        cmd = [
            "yt-dlp",
            "--no-warnings",
            "--flat-playlist",
            "--dump-json",
            target_url
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        items = []
        if result.stdout.strip():
            for line in result.stdout.strip().split("\n"):
                line = line.strip()
                if not line or not line.startswith("{"):
                    continue
                try:
                    entry = json.loads(line)
                    items.append(entry)
                except json.JSONDecodeError:
                    continue
        return items

    with ThreadPoolExecutor(max_workers=2) as executor:
        f_videos = executor.submit(_fetch_tab, f"{base_url}/videos")
        f_shorts = executor.submit(_fetch_tab, f"{base_url}/shorts")
        raw_videos = f_videos.result()
        raw_shorts = f_shorts.result()

    if not raw_videos and not raw_shorts:
        raw_fallback = _fetch_tab(channel_url)
        for entry in raw_fallback:
            u = str(entry.get("url") or entry.get("webpage_url") or entry.get("original_url") or "")
            dur = entry.get("duration")
            if "/shorts/" in u or (dur and dur <= 60 and "/watch" not in u):
                raw_shorts.append(entry)
            else:
                raw_videos.append(entry)

    seen_ids = set()
    long_videos = []
    short_videos = []

    # Process shorts first
    for entry in raw_shorts:
        vid = entry.get("id")
        if vid and vid not in seen_ids:
            seen_ids.add(vid)
            entry["platform"] = "youtube"
            entry["is_short"] = True
            if not entry.get("url") or not str(entry.get("url")).startswith("http"):
                entry["url"] = f"https://www.youtube.com/shorts/{vid}"
            if not entry.get("webpage_url"):
                entry["webpage_url"] = entry["url"]
            short_videos.append(entry)

    # Process videos; detect if any item is actually a short
    for entry in raw_videos:
        vid = entry.get("id")
        if vid and vid not in seen_ids:
            seen_ids.add(vid)
            entry["platform"] = "youtube"
            u = str(entry.get("url") or entry.get("webpage_url") or entry.get("original_url") or "")
            if "/shorts/" in u:
                entry["is_short"] = True
                if not entry.get("url") or not str(entry.get("url")).startswith("http"):
                    entry["url"] = f"https://www.youtube.com/shorts/{vid}"
                if not entry.get("webpage_url"):
                    entry["webpage_url"] = entry["url"]
                short_videos.append(entry)
            else:
                entry["is_short"] = False
                if not entry.get("url") or not str(entry.get("url")).startswith("http"):
                    entry["url"] = f"https://www.youtube.com/watch?v={vid}"
                if not entry.get("webpage_url"):
                    entry["webpage_url"] = entry["url"]
                long_videos.append(entry)

    all_videos = long_videos + short_videos
    return {
        "all": all_videos,
        "shorts": short_videos,
        "long": long_videos
    }


def get_all_channel_videos(channel_url: str) -> List[Dict]:
    """Fetch all videos from a YouTube channel or Instagram profile without limit."""
    data = get_channel_videos_categorized(channel_url)
    return data.get("all", [])


def format_duration(seconds: int) -> str:
    """Format duration in seconds into a human-readable string (e.g. 11h 24m, 20h 00m, 45m 10s)."""
    if not seconds or seconds < 0:
        return "Unknown"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h}h {m:02d}m"
    return f"{m}m {s:02d}s"


def find_downloaded_mp4(video_id: str, clean_id: Optional[str] = None) -> Optional[Path]:
    """
    Find a finished, valid .mp4 file for the given video_id, ignoring temp/part/intermediate files.
    If separate completed video (*.f*.mp4) and audio (*.f*.webm / *.f*.m4a) streams exist,
    automatically merges them using memory-safe ffmpeg without +faststart to prevent OOM.
    """
    clean_id = clean_id or video_id.replace("ig_", "")
    patterns = [f"*{video_id}*.mp4"]
    if clean_id and clean_id != video_id:
        patterns.append(f"*{clean_id}*.mp4")

    # 1. Look for valid, finished .mp4 files
    valid_candidates = []
    for pat in patterns:
        for p in DOWNLOADS_DIR.glob(pat):
            fname = p.name
            if any(fname.endswith(ext) for ext in [".temp.mp4", ".part", ".ytdl"]):
                continue
            if re.search(r'\.f\d+\.mp4$', fname):
                continue
            if p.is_file() and p.stat().st_size > 1024:
                try:
                    probe = subprocess.run(
                        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(p)],
                        capture_output=True, text=True, timeout=5
                    )
                    if probe.returncode == 0 and probe.stdout.strip():
                        valid_candidates.append(p)
                except Exception:
                    pass

    if valid_candidates:
        valid_candidates.sort(key=lambda x: x.stat().st_size, reverse=True)
        return valid_candidates[0]

    # 2. Check for auto-recovery: separate video (.f*.mp4) and audio (.f*.webm or .f*.m4a)
    video_parts = []
    audio_parts = []
    for pat in [f"*{video_id}*", f"*{clean_id}*"]:
        for f in DOWNLOADS_DIR.glob(pat):
            if f.name.endswith(".part"):
                continue
            if re.search(r'\.f\d+\.mp4$', f.name) and f.stat().st_size > 1024:
                video_parts.append(f)
            elif (re.search(r'\.f\d+\.webm$', f.name) or re.search(r'\.f\d+\.m4a$', f.name)) and f.stat().st_size > 1024:
                audio_parts.append(f)

    if video_parts and audio_parts:
        v_file = max(video_parts, key=lambda x: x.stat().st_size)
        audio_parts_valid = [a for a in audio_parts if not a.name.endswith(".part")]
        if audio_parts_valid:
            a_file = max(audio_parts_valid, key=lambda x: x.stat().st_size)
            base_name = re.sub(r'\.f\d+\.mp4$', '', v_file.name)
            merged_out = DOWNLOADS_DIR / f"{base_name}.mp4"
            print(f"\n  [Auto-Recovery] Found completed video & audio streams. Merging to {merged_out.name}...")
            try:
                # Memory-safe merge without +faststart to avoid OOM on containers
                merge_cmd = [
                    "ffmpeg", "-y", "-i", str(v_file), "-i", str(a_file),
                    "-c", "copy", "-map", "0:v:0", "-map", "1:a:0",
                    str(merged_out)
                ]
                m_res = subprocess.run(merge_cmd, capture_output=True, text=True)
                if m_res.returncode == 0 and merged_out.exists() and merged_out.stat().st_size > 1024:
                    print("  [Auto-Recovery] Merge completed successfully.")
                    v_file.unlink(missing_ok=True)
                    a_file.unlink(missing_ok=True)
                    temp_file = DOWNLOADS_DIR / f"{base_name}.temp.mp4"
                    if temp_file.exists():
                        temp_file.unlink(missing_ok=True)
                    return merged_out
            except Exception as me:
                print(f"  [Auto-Recovery] Merge failed: {me}")

    return None


def download_video(url: str, max_height: int = 720, confirm_long: bool = True) -> Optional[Dict]:
    """Download video with yt-dlp capped at max_height (default 720p), preserving metadata & thumbnail, avoiding duplicate downloads.
    Long videos (>= 2 hours, e.g. 11h-20h+) are supported and will prompt the user to proceed.
    """
    platform = detect_platform(url)
    vid = extract_video_id(url)
    if vid:
        existing = video_exists_in_db(vid)
        if existing and existing.get("file_path") and os.path.exists(existing["file_path"]):
            print(f"Video {vid} already downloaded at: {existing['file_path']}")
            return existing

    print(f"Fetching video info for: {url}")
    info = get_video_info(url)
    raw_id = str(info.get("id") or "")
    if platform == "instagram":
        video_id = vid or (raw_id if raw_id.startswith("ig_") else f"ig_{raw_id}")
    else:
        video_id = vid or raw_id
    title = (info.get("title") or "").strip() or (f"Reel_{video_id}" if platform == "instagram" else f"Video_{video_id}")
    description = info.get("description", "")
    tags = info.get("tags", [])
    duration = info.get("duration", 0)

    # Long video check: if >= 2 hours (7200s), ask user if they want to proceed (supports 11h-20h+ videos)
    if duration and duration >= 7200:
        dur_str = format_duration(duration)
        print(f"\n[LONG VIDEO DETECTED] '{title}' is {dur_str} long (exceeds 2 hours, can be 11h-20h+).")
        if confirm_long:
            if sys.stdin.isatty():
                try:
                    ans = input(f"Do you want to proceed with downloading and processing this {dur_str} video? [y/N]: ").strip().lower()
                    if ans not in ['y', 'yes']:
                        print(f"[SKIPPED] User chose not to proceed with long video: {title}")
                        return None
                except (KeyboardInterrupt, EOFError):
                    print(f"\n[SKIPPED] Cancelled long video: {title}")
                    return None
            else:
                print(f"[PROCEED] Non-interactive mode: proceeding with {dur_str} video.")
        else:
            print(f"[PROCEED] Proceeding with {dur_str} video as requested.")

    # Check disk for existing video file
    clean_id = video_id.replace("ig_", "")
    existing_file = find_downloaded_mp4(video_id, clean_id)
    if existing_file:
        file_path = str(existing_file)
        print(f"Found existing download on disk: {file_path}")
        thumb_candidates = (
            list(THUMBNAILS_DIR.glob(f"*{video_id}*.jpg")) or
            list(THUMBNAILS_DIR.glob(f"*{clean_id}*.jpg")) or
            list(DOWNLOADS_DIR.glob(f"*{clean_id}*.jpg")) or
            list(DOWNLOADS_DIR.glob(f"*{video_id}*.jpg"))
        )
        thumb_path = str(thumb_candidates[0]) if thumb_candidates else None
        if thumb_path and thumb_path.startswith(str(DOWNLOADS_DIR)):
            dst = THUMBNAILS_DIR / Path(thumb_path).name
            shutil.move(thumb_path, str(dst))
            thumb_path = str(dst)
        if (not thumb_path or not os.path.exists(thumb_path)) and file_path and os.path.exists(file_path):
            gen_thumb = THUMBNAILS_DIR / f"{clean_id}.jpg"
            try:
                subprocess.run([
                    "ffmpeg", "-y", "-ss", "00:00:01", "-i", file_path,
                    "-vframes", "1", "-q:v", "2", str(gen_thumb)
                ], capture_output=True, check=True)
                if gen_thumb.exists() and gen_thumb.stat().st_size > 0:
                    thumb_path = str(gen_thumb)
            except Exception:
                pass
        data = {
            "video_id": video_id,
            "source_url": url,
            "platform": platform,
            "title": title,
            "description": description,
            "tags": tags,
            "duration": duration,
            "file_path": file_path,
            "file_size": os.path.getsize(file_path),
            "thumbnail_path": thumb_path,
            "upload_status": "pending"
        }
        save_video_metadata(video_id, data)
        return data

    # Clean title for filename template
    output_template = str(DOWNLOADS_DIR / "%(title).80s [%(id)s].%(ext)s")
    cmd = [
        "yt-dlp",
        "--no-warnings",
        "-f", f"bestvideo[height<={max_height}]+bestaudio/best[height<={max_height}]/best",
        "--merge-output-format", "mp4",
        "--postprocessor-args", "Merger:-movflags -faststart",
        "--write-thumbnail",
        "--convert-thumbnails", "jpg",
        "--write-info-json",
        "-o", output_template
    ]
    if platform == "instagram":
        if INSTAGRAM_COOKIES_PATH.exists():
            cmd.extend(["--cookies", str(INSTAGRAM_COOKIES_PATH)])
        elif COOKIES_PATH.exists():
            cmd.extend(["--cookies", str(COOKIES_PATH)])
        cmd.extend(["--add-header", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"])
    elif COOKIES_PATH.exists():
        cmd.extend(["--cookies", str(COOKIES_PATH)])

    cmd.append(url)

    print(f"Downloading [{platform.upper()}]: {title} ({duration//60}:{duration%60:02d})")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True
    )
    last_lines = []
    try:
        for raw_line in iter(proc.stdout.readline, ''):
            line = raw_line.strip()
            if not line:
                continue
            last_lines.append(line)
            if len(last_lines) > 20:
                last_lines.pop(0)

            # Display real-time progress for download and merging
            if "[download]" in line or "[Merger]" in line or "Destination:" in line:
                sys.stdout.write(f"\r\033[K  {line[:90]}")
                sys.stdout.flush()
            elif "[info]" in line or "Deleting original" in line:
                print(f"\n  {line}")
    except KeyboardInterrupt:
        proc.kill()
        print(f"\n[ABORTED] Download cancelled by user: {title}")
        raise

    proc.stdout.close()
    ret = proc.wait()
    sys.stdout.write("\n")
    sys.stdout.flush()
    if ret != 0:
        err = "\n".join(last_lines)
        if platform == "instagram" and ("login" in err.lower() or "empty media" in err.lower()):
            raise RuntimeError(f"Instagram requires authentication: {err}. Place Instagram cookies in {INSTAGRAM_COOKIES_PATH}")
        raise RuntimeError(f"Download failed: {err}")

    # Locate and organize files
    finished_file = find_downloaded_mp4(video_id, clean_id)
    file_path = str(finished_file) if finished_file else None

    # Move thumbnail to THUMBNAILS_DIR
    thumb_path = None
    for pattern in [
        f"*{video_id}*.jpg", f"*{video_id}*.png", f"*{video_id}*.webp",
        f"*{clean_id}*.jpg", f"*{clean_id}*.png", f"*{clean_id}*.webp"
    ]:
        for f in DOWNLOADS_DIR.glob(pattern):
            dst = THUMBNAILS_DIR / f.name
            shutil.move(str(f), str(dst))
            thumb_path = str(dst)
            break
        if thumb_path:
            break

    if (not thumb_path or not os.path.exists(thumb_path)) and file_path and os.path.exists(file_path):
        gen_thumb = THUMBNAILS_DIR / f"{clean_id}.jpg"
        try:
            subprocess.run([
                "ffmpeg", "-y", "-ss", "00:00:01", "-i", file_path,
                "-vframes", "1", "-q:v", "2", str(gen_thumb)
            ], capture_output=True, check=True)
            if gen_thumb.exists() and gen_thumb.stat().st_size > 0:
                thumb_path = str(gen_thumb)
        except Exception:
            pass

    # Move metadata .info.json to METADATA_DIR
    for pattern in [f"*{video_id}*.info.json", f"*{clean_id}*.info.json"]:
        for f in DOWNLOADS_DIR.glob(pattern):
            dst = METADATA_DIR / f.name
            shutil.move(str(f), str(dst))
            break

    file_size = os.path.getsize(file_path) if file_path and os.path.exists(file_path) else 0

    data = {
        "video_id": video_id,
        "source_url": url,
        "platform": platform,
        "title": title,
        "description": description,
        "tags": tags,
        "duration": duration,
        "file_path": file_path,
        "file_size": file_size,
        "thumbnail_path": thumb_path,
        "upload_status": "pending"
    }
    save_video_metadata(video_id, data)
    return data


# =============================================================================
# YOUTUBE UPLOAD (RESUMABLE & RETRIES)
# =============================================================================

def clean_and_rewrite_description(original: str, title: str) -> str:
    """
    Generate a clean rewritten video description.
    The uploaded description must NOT contain:
      - Any URL (http, https, www, domain.tld, etc.)
      - Any username/channel handle (@...)
      - Creator/channel information
      - Original creator attribution or promotional links
    Do not copy original description if it contains any of the above.
    Keep description relevant to the video content and clean.
    """
    if not original:
        return f"{title}\n\nFull story breakdown and recap. Enjoy the full video!"

    url_pattern = re.compile(
        r"(https?://\S+|www\.\S+|\b[a-zA-Z0-9.-]+\.(?:com|org|net|io|me|ly|be|app|tv|gg|co|xyz|info|cc|to|link|site|top|club)\b)",
        re.IGNORECASE
    )
    handle_pattern = re.compile(r"@[a-zA-Z0-9_.-]+", re.IGNORECASE)

    banned_phrases = [
        "subscribe", "sub to", "like and share", "like & share", "hit the bell",
        "notification bell", "turn on notifications", "follow me", "follow us",
        "instagram", "twitter", "x.com", "facebook", "tiktok", "discord", "telegram",
        "patreon", "social media", "socials", "merch", "merchandise", "shop now",
        "store:", "buy now", "sponsor", "sponsored", "promo", "affiliate",
        "credit:", "credits:", "credit to", "music by", "soundtrack", "license",
        "licensed under", "copyright disclaimer", "fair use disclaimer", "all rights belong",
        "original video", "watch original", "channel:", "produced by", "edited by"
    ]

    cleaned_lines = []
    for line in original.splitlines():
        line_clean = line.strip()
        if not line_clean:
            continue
        # Drop lines with URLs or @ handles
        if url_pattern.search(line_clean) or handle_pattern.search(line_clean):
            continue
        line_lower = line_clean.lower()
        # Drop lines with promotional or attribution phrases
        if any(phrase in line_lower for phrase in banned_phrases):
            continue
        cleaned_lines.append(line_clean)

    cleaned_text = "\n\n".join(cleaned_lines)
    # Scrub any leftover URLs or handles
    cleaned_text = url_pattern.sub("", cleaned_text)
    cleaned_text = handle_pattern.sub("", cleaned_text).strip()

    # If description was wiped out or is too short (< 25 chars), generate clean relevant content
    if len(cleaned_text) < 25:
        return f"{title}\n\nFull story breakdown and recap. Enjoy the full video!"

    return cleaned_text


def upload_video_to_youtube(service, video_data: Dict, privacy_status: str = "public") -> str:
    """Upload video to YouTube with resumable chunking, retry handling, and clean rewritten metadata."""
    file_path = video_data.get("file_path")
    if not file_path or not os.path.exists(file_path):
        raise RuntimeError(f"Video file not found: {file_path}")

    dur = video_data.get("duration") or 0
    if dur > 43200:
        raise ValueError(
            f"Video duration ({dur // 3600}h {(dur % 3600) // 60}m) exceeds YouTube's maximum limit of 12 hours (43,200s). "
            f"YouTube does not allow video uploads exceeding 12 hours."
        )

    title = (video_data.get("title") or "Uploaded Video")[:100]
    raw_desc = video_data.get("description") or ""
    description = clean_and_rewrite_description(raw_desc, title)[:5000]

    raw_tags = video_data.get("tags") or []
    if isinstance(raw_tags, str):
        try:
            raw_tags = json.loads(raw_tags)
        except Exception:
            raw_tags = []
    tags = []
    for t in raw_tags[:50]:
        if isinstance(t, str):
            t_s = t.strip()
            if t_s and not t_s.startswith("@") and not any(ext in t_s.lower() for ext in [".com", "http", "www."]):
                tags.append(t_s)

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags[:50],
            "categoryId": "22"  # People & Blogs
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False
        }
    }

    # 5MB chunks for resumable upload
    chunk_size = 5 * 1024 * 1024
    media = MediaFileUpload(file_path, chunksize=chunk_size, resumable=True, mimetype="video/mp4")

    request = service.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media
    )

    print(f"\nUploading: {title}")
    print(f"Privacy: {privacy_status} | Size: {os.path.getsize(file_path)/(1024*1024):.1f} MB")

    response = None
    retry_count = 0
    max_retries = 3

    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                progress = int(status.progress() * 100)
                sys.stdout.write(f"\r  Upload progress: {progress}%")
                sys.stdout.flush()
        except HttpError as e:
            err_str = str(e)
            if "uploadLimitExceeded" in err_str or "exceeded the number of videos" in err_str:
                raise RuntimeError(
                    "YouTube Daily Upload Limit Exceeded: Your YouTube channel has reached YouTube's 24-hour daily upload limit (uploadLimitExceeded). "
                    "YouTube enforces this per-channel limit across a rolling 24-hour window. Please wait for the 24-hour window to reset, or upload to Instagram/Facebook."
                )
            if "quotaExceeded" in err_str:
                raise RuntimeError(
                    "YouTube API Quota Exceeded: Your Google Cloud YouTube Data API project quota has been exhausted for today (10,000 units/day). "
                    "The quota resets daily at midnight Pacific Time (PT). You can upload to Instagram/Facebook in the meantime."
                )
            retry_count += 1
            if retry_count > max_retries:
                raise RuntimeError(f"Upload failed after {max_retries} retries: {e}")
            wait_sec = 2 ** retry_count
            print(f"\n  Temporary HTTP error (retry {retry_count}/{max_retries}): {e}. Waiting {wait_sec}s...")
            time.sleep(wait_sec)
        except Exception as e:
            raise RuntimeError(f"Fatal upload error: {e}")

    print()
    if response and "id" in response:
        youtube_id = response["id"]
        watch_url = f"https://www.youtube.com/watch?v={youtube_id}"
        print(f"  SUCCESS! Video uploaded.")
        print(f"  YouTube Video ID: {youtube_id}")
        print(f"  Watch URL: {watch_url}")

        # Attempt thumbnail upload if available
        thumb_path = video_data.get("thumbnail_path")
        if thumb_path and os.path.exists(thumb_path):
            try:
                service.thumbnails().set(
                    videoId=youtube_id,
                    media_body=MediaFileUpload(thumb_path, mimetype="image/jpeg")
                ).execute()
                print("  Thumbnail uploaded successfully.")
            except Exception as te:
                print(f"  Note: Thumbnail upload skipped ({te}). Video is uploaded.")

        return youtube_id
    else:
        raise RuntimeError("Upload finished without returning a YouTube video ID.")


# =============================================================================
# INSTAGRAM UPLOAD & CLIENT (INSTAGRAPI)
# =============================================================================

def extract_instagram_sessionid() -> Optional[str]:
    """Try to find sessionid cookie in known cookie files."""
    paths = [INSTAGRAM_COOKIES_PATH, COOKIES_PATH, Path("/root/cookies.txt"), Path.home() / "cookies.txt"]
    for p in paths:
        if p.exists():
            try:
                for line in p.read_text(errors="replace").splitlines():
                    if "instagram.com" in line and "sessionid" in line:
                        parts = line.strip().split("\t")
                        if len(parts) >= 7 and parts[5] == "sessionid":
                            return parts[6].strip()
            except Exception:
                pass
    return None


def get_instagram_account_status() -> Dict:
    """Return dict with login status and username."""
    if INSTAGRAM_SESSION_PATH.exists():
        try:
            from instagrapi import Client
            cl = Client()
            cl.load_settings(INSTAGRAM_SESSION_PATH)
            username = getattr(cl, "username", None)
            if not username and INSTAGRAM_CREDS_PATH.exists():
                creds = json.loads(INSTAGRAM_CREDS_PATH.read_text())
                username = creds.get("username")
            return {"logged_in": True, "username": username or "Connected User"}
        except Exception:
            pass

    for cfg_file in [INSTAGRAM_CONFIG_PATH, INSTAGRAM_CONFIG_LOCAL, INSTAGRAM_CREDS_PATH]:
        if cfg_file.exists():
            try:
                creds = json.loads(cfg_file.read_text())
                u = creds.get("username", "").strip()
                s = creds.get("sessionid", "").strip()
                if u and u != "your_instagram_username":
                    return {"logged_in": True, "username": u}
                elif s:
                    return {"logged_in": True, "username": "SessionID Configured"}
            except Exception:
                pass

    return {"logged_in": False, "username": None}


def get_instagram_client(interactive: bool = True) -> Optional[Any]:
    """
    Get authenticated instagrapi Client.
    1. Loads from saved session settings if valid.
    2. Tries config files (/root/instagram_config.json, creds.json).
    3. Tries sessionid cookie if found.
    4. If interactive, prompts user for username/password & 2FA.
    """
    try:
        from instagrapi import Client
        from instagrapi.exceptions import TwoFactorRequired, BadPassword, ChallengeRequired
    except ImportError:
        print("[ERROR] instagrapi is not installed. Please run: pip install instagrapi")
        return None

    cl = Client()

    # 1. Saved session file
    if INSTAGRAM_SESSION_PATH.exists():
        try:
            cl.load_settings(INSTAGRAM_SESSION_PATH)
            if cl.user_id:
                return cl
        except Exception:
            pass

    # 2. Config files (/root/instagram_config.json or ~/.hermes/instagram_creds.json)
    for cfg_file in [INSTAGRAM_CONFIG_PATH, INSTAGRAM_CONFIG_LOCAL, INSTAGRAM_CREDS_PATH]:
        if cfg_file.exists():
            try:
                cfg = json.loads(cfg_file.read_text())
                if cfg.get("sessionid"):
                    cl.login_by_sessionid(cfg["sessionid"].strip())
                    Path.home().joinpath(".hermes").mkdir(parents=True, exist_ok=True)
                    cl.dump_settings(INSTAGRAM_SESSION_PATH)
                    print(f"✔ Authenticated via sessionid from {cfg_file.name}!")
                    return cl
                elif cfg.get("username") and cfg.get("password") and cfg.get("password") != "your_instagram_password":
                    u = cfg["username"].strip()
                    p = cfg["password"].strip()
                    print(f"Logging in to Instagram using credentials from {cfg_file.name} as @{u}...")
                    cl.login(u, p)
                    Path.home().joinpath(".hermes").mkdir(parents=True, exist_ok=True)
                    cl.dump_settings(INSTAGRAM_SESSION_PATH)
                    INSTAGRAM_CREDS_PATH.write_text(json.dumps({"username": u, "password": p}))
                    print(f"✔ Successfully logged in to Instagram as @{u}!")
                    return cl
            except Exception as e:
                print(f"[INSTAGRAM] Login with {cfg_file.name} failed: {e}")

    # 3. Session ID from cookies.txt
    sessionid = extract_instagram_sessionid()
    if sessionid:
        try:
            cl.login_by_sessionid(sessionid)
            Path.home().joinpath(".hermes").mkdir(parents=True, exist_ok=True)
            cl.dump_settings(INSTAGRAM_SESSION_PATH)
            print("✔ Authenticated to Instagram using sessionid cookie!")
            return cl
        except Exception:
            pass

    if not interactive:
        return None

    # 4. Interactive login prompt
    print("\n" + "=" * 60)
    print("INSTAGRAM ACCOUNT LOGIN REQUIRED")
    print("To upload Reels directly to Instagram, enter your login details:")
    print("=" * 60)
    try:
        username = input("Instagram Username (or blank to cancel): ").strip()
        if not username:
            return None
        import getpass
        password = getpass.getpass("Instagram Password: ").strip()
        if not password:
            return None

        print(f"Logging in to Instagram as @{username}...")
        try:
            cl.login(username, password)
        except TwoFactorRequired:
            code = input("Enter Instagram 2FA Security Code: ").strip()
            cl.login(username, password, verification_code=code)

        Path.home().joinpath(".hermes").mkdir(parents=True, exist_ok=True)
        cl.dump_settings(INSTAGRAM_SESSION_PATH)
        INSTAGRAM_CREDS_PATH.write_text(json.dumps({"username": username, "password": password}))
        print(f"✔ Successfully logged in to Instagram as @{username}!")
        return cl
    except Exception as e:
        print(f"Instagram login failed: {e}")
        return None


def get_facebook_config() -> Dict:
    """Load Facebook personal account cross-posting configuration."""
    cfg = {
        "auto_share_to_facebook": True,
        "destination_type": "USER"
    }
    for p in [FACEBOOK_CONFIG_PATH, FACEBOOK_CONFIG_LOCAL]:
        if p.exists():
            try:
                cfg.update(json.loads(p.read_text()))
                return cfg
            except Exception:
                pass
    return cfg


def save_facebook_config(data: Dict):
    """Save Facebook configuration."""
    cfg = get_facebook_config()
    cfg.update(data)
    FACEBOOK_CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    try:
        FACEBOOK_CONFIG_LOCAL.write_text(json.dumps(cfg, indent=2))
    except Exception:
        pass
    return cfg


def resolve_facebook_crosspost_destination(cl) -> Optional[Dict]:
    """Resolve Facebook cross-posting destination from Meta Accounts Center."""
    fb_cfg = get_facebook_config()
    cached_id = fb_cfg.get("destination_id")
    cached_type = fb_cfg.get("destination_type", "USER")
    cached_aud = fb_cfg.get("destination_audience_type", "PUBLIC")

    if cached_id:
        return {
            "destination_id": str(cached_id),
            "destination_type": str(cached_type),
            "destination_audience_type": str(cached_aud)
        }

    try:
        dest = cl.clip_share_to_fb_unified_destination()
        if dest and dest.get("destination_id"):
            dest_id = str(dest["destination_id"])
            dest_type = str(dest.get("destination_type", "USER"))
            dest_aud = str(dest.get("destination_audience_type", "PUBLIC"))
            fb_cfg["destination_id"] = dest_id
            fb_cfg["destination_type"] = dest_type
            fb_cfg["destination_audience_type"] = dest_aud
            save_facebook_config(fb_cfg)
            return {
                "destination_id": dest_id,
                "destination_type": dest_type,
                "destination_audience_type": dest_aud
            }
    except Exception as e:
        print(f"[Facebook] Could not query Accounts Center destination: {e}")

    return None


def upload_video_to_instagram(cl, video_data: Dict) -> Dict:
    """Upload video to Instagram as a Reel with clean sanitized caption, thumbnail, and Facebook account cross-posting."""
    file_path = video_data.get("file_path")
    if not file_path or not os.path.exists(file_path):
        raise RuntimeError(f"Video file not found: {file_path}")

    dur = video_data.get("duration") or 0
    if dur > 900:
        raise ValueError(
            f"Video duration ({dur // 3600}h {(dur % 3600) // 60}m) exceeds Instagram Reels limit of 15 minutes (900s). "
            f"Instagram Reels cannot exceed 15 minutes."
        )

    title = (video_data.get("title") or "Reel")[:100]
    raw_desc = video_data.get("description") or ""
    caption = clean_and_rewrite_description(raw_desc, title)[:2200]

    thumb_path = video_data.get("thumbnail_path")
    clean_id = (video_data.get("video_id") or "").replace("ig_", "")
    if not thumb_path or not os.path.exists(thumb_path):
        cand = (
            list(THUMBNAILS_DIR.glob(f"*{clean_id}*.jpg")) or
            list(DOWNLOADS_DIR.glob(f"*{clean_id}*.jpg")) or
            list(THUMBNAILS_DIR.glob(f"*{video_data.get('video_id', '')}*.jpg"))
        )
        if cand:
            thumb_path = str(cand[0])
        elif file_path and os.path.exists(file_path):
            gen_thumb = THUMBNAILS_DIR / f"{video_data.get('video_id', 'thumb')}.jpg"
            try:
                subprocess.run([
                    "ffmpeg", "-y", "-ss", "00:00:01", "-i", file_path,
                    "-vframes", "1", "-q:v", "2", str(gen_thumb)
                ], capture_output=True, check=True)
                if gen_thumb.exists() and gen_thumb.stat().st_size > 0:
                    thumb_path = str(gen_thumb)
                    print(f"Generated video thumbnail with ffmpeg: {thumb_path}")
            except Exception as e:
                print(f"[Notice] Could not generate video thumbnail with ffmpeg: {e}")

    thumb = Path(thumb_path) if thumb_path and os.path.exists(thumb_path) else None

    print(f"\nUploading to Instagram as Reel: {title}")
    print(f"File: {file_path} ({os.path.getsize(file_path)/(1024*1024):.1f} MB)")
    if thumb:
        print(f"Thumbnail: {thumb} ({os.path.getsize(str(thumb))} bytes)")

    fb_cfg = get_facebook_config()
    auto_share_fb = fb_cfg.get("auto_share_to_facebook", True)

    fb_extra = {}
    dest_id = None
    dest_type = "USER"
    dest_aud = "PUBLIC"

    if auto_share_fb:
        fb_dest = resolve_facebook_crosspost_destination(cl)
        if fb_dest and fb_dest.get("destination_id"):
            dest_id = fb_dest["destination_id"]
            dest_type = fb_dest.get("destination_type", "USER")
            dest_aud = fb_dest.get("destination_audience_type", "PUBLIC")

            try:
                fb_extra = cl.clip_share_to_fb_extra_data(
                    destination_id=dest_id,
                    destination_type=dest_type,
                    destination_audience_type=dest_aud,
                    validation_check_bypass=True,
                )
            except Exception:
                pass

        if not fb_extra and dest_id:
            fb_extra = {
                "share_to_facebook": "1",
                "is_reel_shared_to_fb": True,
                "share_to_facebook_reels": True,
                "cross_app_share_type": "2",
                "share_to_fb_destination_id": dest_id,
                "share_to_fb_destination_type": dest_type,
                "share_to_fb_destination_audience_type": dest_aud,
                "xpost_surface": "IG_REELS_COMPOSER",
                "no_token_crosspost": "1",
                "attempt_id": str(uuid4()),
                "share_to_facebook_validation_bypass": json.dumps(["AUTO_CROSSPOST_SETTING"]),
            }

        if dest_id:
            print(f"Facebook Auto Cross-Posting: ENABLED (Target {dest_type}: {dest_id[:18]}... Audience: {dest_aud})")
        else:
            print("Facebook Auto Cross-Posting: ENABLED (Default connected account)")

    media = None
    if auto_share_fb and dest_id:
        try:
            print("Posting Reel to Instagram with automatic Facebook cross-posting...")
            media = cl.clip_upload(
                path=Path(file_path),
                caption=caption,
                thumbnail=thumb,
                share_to_facebook=True,
                fb_destination_id=dest_id,
                fb_destination_type=dest_type,
                fb_destination_audience_type=dest_aud,
                fb_validation_check_bypass=True,
                extra_data=fb_extra
            )
        except Exception as preflight_err:
            print(f"[Notice] Direct share_to_facebook parameter note: {preflight_err}. Retrying with extra_data flags...")
            try:
                media = cl.clip_upload(
                    path=Path(file_path),
                    caption=caption,
                    thumbnail=thumb,
                    extra_data=fb_extra
                )
            except Exception as e:
                print(f"[Notice] Retrying clean upload without extra cross-post flags ({e})...")
                media = cl.clip_upload(
                    path=Path(file_path),
                    caption=caption,
                    thumbnail=thumb
                )
    else:
        media = cl.clip_upload(
            path=Path(file_path),
            caption=caption,
            thumbnail=thumb
        )

    code = getattr(media, "code", None) or getattr(media, "pk", "")
    reel_url = f"https://www.instagram.com/reel/{code}/" if code else "https://www.instagram.com"
    print(f"✔ Successfully uploaded to Instagram! URL: {reel_url}")
    if auto_share_fb:
        print("✔ Cross-post request sent to your connected Facebook account.")

    return {
        "media_id": str(getattr(media, "pk", "")),
        "code": str(code),
        "url": reel_url
    }


def upload_item(
    video_data: Dict,
    destination: str = "youtube",
    yt_service = None,
    ig_client = None,
    privacy_status: str = "public"
) -> Dict:
    """
    Upload a video to YouTube, Instagram, Facebook (Direct), Instagram + Facebook, or All Platforms.
    destination: 'youtube', 'instagram', 'facebook', 'instagram_only', 'both', 'all'
    """
    results = {}
    vid = video_data.get("video_id")

    if destination in ["youtube", "both", "all"]:
        if not yt_service:
            yt_service = get_youtube_service()
        print(f"Uploading video to YouTube as {privacy_status}...")
        yt_id = upload_video_to_youtube(yt_service, video_data, privacy_status=privacy_status)
        results["youtube_id"] = yt_id
        update_video_status(vid, "uploaded", youtube_video_id=yt_id, upload_destination=destination)

    if destination in ["instagram", "instagram_only", "instagram_facebook", "both", "all"]:
        if not ig_client:
            ig_client = get_instagram_client(interactive=True)
            if not ig_client:
                raise RuntimeError("Instagram login required to upload to Instagram.")
        print(f"Uploading reel to Instagram...")
        ig_res = upload_video_to_instagram(ig_client, video_data)
        results["instagram_id"] = ig_res.get("media_id")
        results["instagram_url"] = ig_res.get("url")
        update_video_status(vid, "uploaded", instagram_media_id=ig_res.get("media_id"), upload_destination=destination)

    if destination in ["facebook", "instagram", "instagram_facebook", "both", "all"]:
        import facebook_uploader
        file_path = video_data.get("file_path")
        title = (video_data.get("title") or "Reel")[:100]
        raw_desc = video_data.get("description") or ""
        caption = clean_and_rewrite_description(raw_desc, title)[:2200]

        if facebook_uploader.is_facebook_configured():
            mode_desc = "Meta Graph API (Page: ai-edit)" if facebook_uploader.is_facebook_graph_configured() else "headless browser"
            print(f"\nUploading reel directly to Facebook via {mode_desc}...")
            try:
                fb_res = facebook_uploader.upload_facebook(file_path, caption=caption, title=title)
                results["facebook_status"] = "uploaded"
                results["facebook_result"] = fb_res
                results["facebook_url"] = fb_res.get("url")
                fb_post_id = fb_res.get("video_id") or fb_res.get("post_id") or fb_res.get("screenshot") or "fb_uploaded"
                update_video_status(vid, "uploaded", facebook_post_id=fb_post_id, upload_destination=destination)
            except Exception as e:
                print(f"Error uploading directly to Facebook: {e}")
                results["facebook_status"] = "failed"
                results["facebook_error"] = str(e)
                if destination == "facebook":
                    raise e
        else:
            if destination == "facebook":
                raise RuntimeError("Facebook is not configured. Please configure Graph API or cookies in Settings -> Facebook Settings.")
            else:
                print(f"\n[Notice] Direct Facebook upload skipped: Facebook credentials not configured.")
                print("To upload directly to Facebook alongside Instagram, configure Graph API in facebook_api/.env or cookies in Settings -> Facebook Settings.")

    return results


def cmd_setup_instagram(args):
    """View or configure Instagram account login."""
    if getattr(args, "logout", False):
        if INSTAGRAM_SESSION_PATH.exists():
            INSTAGRAM_SESSION_PATH.unlink()
        if INSTAGRAM_CREDS_PATH.exists():
            INSTAGRAM_CREDS_PATH.unlink()
        print("Logged out from Instagram and cleared saved session.")
        return

    status = get_instagram_account_status()
    if getattr(args, "status", False):
        if status["logged_in"]:
            print(f"Instagram Account: Logged in as @{status['username']}")
        else:
            print("Instagram Account: Not logged in")
        return

    if status["logged_in"]:
        print(f"Already logged in as @{status['username']}")
        relogin = input("Do you want to re-login with different account? [y/N]: ").strip().lower()
        if relogin not in ["y", "yes"]:
            return

    get_instagram_client(interactive=True)


# =============================================================================
# CLI COMMAND IMPLEMENTATIONS
# =============================================================================

def cmd_process_channel(args):
    """Find most-viewed videos from a YouTube channel or Instagram profile, display ranking, and let user choose how many to process."""
    channel_platform = detect_platform(args.channel_url)
    print(f"\nFetching videos from {channel_platform.capitalize()}: {args.channel_url}")
    print(f"Scanning up to {args.max_videos} items...")
    videos = get_channel_videos(args.channel_url, args.max_videos)

    if not videos:
        print(f"No videos found on {channel_platform}.")
        return

    top_n = min(args.count, len(videos))
    print(f"\nFound {len(videos)} items. TOP {top_n} MOST-VIEWED:")
    print("-" * 75)
    print(f"{'#':<3} | {'Views':<10} | {'Duration':<8} | {'Title':<45}")
    print("-" * 75)

    for i, v in enumerate(videos[:top_n]):
        views = v.get("view_count") or 0
        dur = v.get("duration") or 0
        dur_str = format_duration(dur) if dur else "?"
        title = (v.get("title") or "Unknown")[:45]
        print(f"{i+1:<3} | {views:<10,} | {dur_str:<8} | {title}")
    print("-" * 75)

    if args.auto:
        selected_indices = list(range(top_n))
        print(f"\n[Auto-mode] Selected top {top_n} items.")
    else:
        try:
            choice = input(f"\nEnter numbers to download (e.g. '1,2,3', 'all' for top {top_n}, or Ctrl+C to cancel): ").strip()
            if choice.lower() in ['all', '']:
                selected_indices = list(range(top_n))
            else:
                selected_indices = [int(x.strip()) - 1 for x in choice.split(",") if x.strip().isdigit()]
        except (KeyboardInterrupt, EOFError):
            print("\nCancelled.")
            return

    downloaded = []
    max_height = getattr(args, "max_height", 720)
    for idx in selected_indices:
        if 0 <= idx < len(videos):
            v = videos[idx]
            vid = v.get("id")
            url = v.get("webpage_url") or v.get("url")
            if not url or not str(url).startswith("http"):
                if v.get("platform") == "instagram" or channel_platform == "instagram":
                    url = f"https://www.instagram.com/reel/{vid}/"
                else:
                    url = f"https://www.youtube.com/watch?v={vid}"
            try:
                res = download_video(url, max_height=max_height)
                if res:
                    downloaded.append(res)
            except Exception as e:
                print(f"Error downloading {vid}: {e}")

    print(f"\nCompleted: {len(downloaded)} videos downloaded and queued for upload.")

    if args.upload_now and downloaded:
        print("\nUploading downloaded videos now...")
        cmd_upload(argparse.Namespace(count=len(downloaded), privacy=args.privacy, retry_failed=False))


def cmd_download(args):
    """Download specific video URLs."""
    urls = args.urls
    max_height = getattr(args, "max_height", 720)
    print(f"\nProcessing {len(urls)} video URL(s) (max quality: {max_height}p)...")
    success_count = 0

    for url in urls:
        print(f"\nProcessing: {url}")
        try:
            data = download_video(url, max_height=max_height)
            if data:
                print(f"Queued for upload: {data.get('title')}")
                success_count += 1
        except Exception as e:
            print(f"Download failed for {url}: {e}")

    print(f"\nFinished: {success_count}/{len(urls)} videos successfully processed & queued.")
    if args.upload_now and success_count > 0:
        cmd_upload(argparse.Namespace(count=success_count, privacy=args.privacy, retry_failed=False))


def cmd_upload(args):
    """Upload queued videos."""
    destination = getattr(args, "destination", "youtube")
    videos = get_pending_videos(limit=args.count, include_failed=args.retry_failed)

    if not videos:
        print("\nNo videos currently pending upload.")
        print("Run 'yw download <URL>' or 'yw process-channel <URL>' to add videos.")
        return

    dest_title = "INSTAGRAM + FACEBOOK" if destination == "instagram" else destination.upper()
    print(f"\nPreparing to upload {len(videos)} video(s) to {dest_title}...")
    success = 0
    failed = 0

    yt_service = None
    ig_client = None

    if destination in ["youtube", "both", "all"]:
        yt_service = get_youtube_service()
    if destination in ["instagram", "instagram_only", "instagram_facebook", "both", "all"]:
        ig_client = get_instagram_client(interactive=True)
        if not ig_client:
            print("Aborting Instagram upload: login required.")
            return

    for i, v in enumerate(videos):
        vid = v["video_id"]
        title = v.get("title") or vid
        print(f"\n[{i+1}/{len(videos)}] Starting upload for: {title}")
        update_video_status(vid, "uploading")

        try:
            upload_item(v, destination=destination, yt_service=yt_service, ig_client=ig_client, privacy_status=args.privacy)
            success += 1
        except Exception as e:
            print(f"  Upload error for {vid}: {e}")
            update_video_status(vid, "failed", error=str(e))
            failed += 1

    print("\n" + "=" * 50)
    print(f"Upload Batch Complete: {success} Successful, {failed} Failed")
    print("=" * 50)


def cmd_status(args):
    """Show upload queue and history status."""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT upload_status, COUNT(*) FROM videos GROUP BY upload_status")
    counts = dict(c.fetchall())

    c.execute("SELECT platform, COUNT(*) FROM videos GROUP BY platform")
    plat_counts = dict(c.fetchall())

    ig_acc = get_instagram_account_status()
    ig_acc_status = f"Logged in as @{ig_acc['username']}" if ig_acc["logged_in"] else "Not logged in"

    print("\n" + "=" * 50)
    print("MULTI-PLATFORM WORKFLOW STATUS (YouTube & Instagram)")
    print("=" * 50)
    for s in ["pending", "uploading", "uploaded", "failed"]:
        print(f"  {s.capitalize():<12}: {counts.get(s, 0)}")
    print("-" * 50)
    print(f"  Platform Breakdown:")
    print(f"    YouTube Videos  : {plat_counts.get('youtube', 0)}")
    print(f"    Instagram Media : {plat_counts.get('instagram', 0)}")
    print("-" * 50)
    print(f"  Instagram Account : {ig_acc_status}")
    ig_cookie_status = "Configured" if INSTAGRAM_COOKIES_PATH.exists() else "Not configured (optional for public / required if login asked)"
    print(f"  Instagram Cookies : {ig_cookie_status}")
    print("=" * 50)

    if args.verbose or args.all:
        c.execute("""
            SELECT video_id, title, upload_status, youtube_video_id, uploaded_at, upload_error, platform, instagram_media_id, upload_destination
            FROM videos ORDER BY created_at DESC LIMIT 25
        """)
        rows = c.fetchall()
        if rows:
            print("\nRecent Items:")
            for r in rows:
                plat_tag = f"[{r[6].upper()}]" if len(r) > 6 and r[6] else "[YOUTUBE]"
                dest_tag = f" -> [{r[8].upper()}]" if len(r) > 8 and r[8] else ""
                links = []
                if r[3]:
                    links.append(f"YouTube: https://youtu.be/{r[3]}")
                if len(r) > 7 and r[7]:
                    links.append(f"Instagram ID: {r[7]}")
                link_str = f" ({', '.join(links)})" if links else ""
                err = f" [Error: {r[5]}]" if r[5] else ""
                print(f"  [{r[2].upper()}] {plat_tag}{dest_tag} {r[1][:38]}...{link_str}{err}")
    conn.close()


def cmd_setup_cookies(args):
    """View or configure Instagram and YouTube cookies."""
    print("\n" + "=" * 50)
    print("COOKIE CONFIGURATION (YouTube & Instagram)")
    print("=" * 50)
    print(f"YouTube Cookies file   : {COOKIES_PATH} ({'EXISTS' if COOKIES_PATH.exists() else 'MISSING'})")
    print(f"Instagram Cookies file : {INSTAGRAM_COOKIES_PATH} ({'EXISTS' if INSTAGRAM_COOKIES_PATH.exists() else 'NOT SET'})")
    print("-" * 50)
    if getattr(args, "instagram_file", None):
        src = Path(args.instagram_file)
        if src.exists():
            shutil.copy(str(src), str(INSTAGRAM_COOKIES_PATH))
            print(f"Successfully copied Instagram cookies from {src} to {INSTAGRAM_COOKIES_PATH}")
        else:
            print(f"Error: Source file {src} not found.")
    else:
        print("To configure Instagram cookies:")
        print(f"  1. Export cookies from your browser (Netscape format).")
        print(f"  2. Save to: {INSTAGRAM_COOKIES_PATH}")
        print(f"  Or run: yw setup-cookies --instagram-file /path/to/cookies.txt")


def cmd_retry(args):
    """Retry failed uploads."""
    conn = get_db()
    c = conn.cursor()
    if args.video_id:
        c.execute("UPDATE videos SET upload_status = 'pending' WHERE video_id = ?", (args.video_id,))
    else:
        c.execute("UPDATE videos SET upload_status = 'pending' WHERE upload_status = 'failed'")
    count = c.rowcount
    conn.commit()
    conn.close()
    print(f"Reset {count} video(s) to 'pending' state.")
    if count > 0 and args.upload_now:
        cmd_upload(argparse.Namespace(count=count, privacy=args.privacy, retry_failed=False))


def cmd_setup_oauth(args):
    """Verify or configure OAuth credentials."""
    if OAUTH_TOKEN_PATH.exists():
        try:
            service = get_youtube_service()
            resp = service.channels().list(part="snippet", mine=True).execute()
            if resp.get("items"):
                ch = resp["items"][0]["snippet"]["title"]
                print(f"Already authenticated as YouTube Channel: {ch}")
                return
        except Exception as e:
            print(f"Existing token invalid: {e}")

    print(f"Starting OAuth authorization server...")
    subprocess.run(["python3", str(WORKFLOW_DIR / "oauth_server.py")])


# =============================================================================
# MAIN ENTRYPOINT
# =============================================================================

def main():
    init_db()

    parser = argparse.ArgumentParser(
        description="Multi-Platform Video Automation Workflow (YouTube & Instagram)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  yw process-channel https://www.youtube.com/@Channel -c 5 --auto
  yw process-channel https://www.instagram.com/username/ -c 5 --auto
  yw download https://www.youtube.com/watch?v=VIDEO_ID
  yw download https://www.instagram.com/reel/REEL_ID/
  yw upload -n 10 --privacy public
  yw status -v
  yw setup-cookies --instagram-file /path/to/cookies.txt
  yw retry
"""
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # process-channel
    pc = subparsers.add_parser("process-channel", help="Find most-viewed videos from a YouTube channel or Instagram profile")
    pc.add_argument("channel_url", help="YouTube channel or Instagram profile URL")
    pc.add_argument("-c", "--count", type=int, default=10, help="Number of top videos/reels to select (default: 10)")
    pc.add_argument("--max-videos", type=int, default=50, help="Max videos/reels to scan on channel (default: 50)")
    pc.add_argument("--max-height", "--quality", type=int, default=720, help="Max video resolution height in pixels (default: 720)")
    pc.add_argument("--auto", action="store_true", help="Auto-select top N without manual prompt")
    pc.add_argument("--upload-now", action="store_true", help="Immediately upload after downloading")
    pc.add_argument("--privacy", choices=["private", "unlisted", "public"], default="public", help="Upload privacy (default: public)")

    # download
    dl = subparsers.add_parser("download", help="Download video(s) from YouTube or Instagram and queue for upload")
    dl.add_argument("urls", nargs="+", help="One or more YouTube or Instagram video/reel URLs")
    dl.add_argument("--max-height", "--quality", type=int, default=720, help="Max video resolution height in pixels (default: 720)")
    dl.add_argument("--upload-now", action="store_true", help="Immediately upload after downloading")
    dl.add_argument("--privacy", choices=["private", "unlisted", "public"], default="public", help="Upload privacy (default: public)")

    # upload
    up = subparsers.add_parser("upload", help="Upload queued videos to YouTube, Instagram, or Both")
    up.add_argument("-n", "--count", type=int, default=10, help="Number of videos to upload (default: 10)")
    up.add_argument("--destination", choices=["youtube", "instagram", "facebook", "instagram_only", "both", "all"], default="youtube", help="Destination platform: youtube, instagram (IG + FB), facebook, instagram_only, both, or all (default: youtube)")
    up.add_argument("--privacy", choices=["private", "unlisted", "public"], default="public", help="Privacy status for YouTube (default: public)")
    up.add_argument("--retry-failed", action="store_true", help="Include failed videos in upload batch")

    # status
    st = subparsers.add_parser("status", help="Show queue breakdown and upload history")
    st.add_argument("-v", "--verbose", action="store_true", help="Show detailed video list")
    st.add_argument("--all", action="store_true", help="Show all recent videos")

    # retry
    rt = subparsers.add_parser("retry", help="Retry failed uploads")
    rt.add_argument("video_id", nargs="?", help="Specific video ID to retry (optional)")
    rt.add_argument("--upload-now", action="store_true", help="Immediately start upload after resetting")
    rt.add_argument("--destination", choices=["youtube", "instagram", "facebook", "instagram_only", "both", "all"], default="youtube", help="Destination platform (default: youtube)")
    rt.add_argument("--privacy", choices=["private", "unlisted", "public"], default="public", help="Privacy status (default: public)")

    # setup-oauth
    subparsers.add_parser("setup-oauth", help="Verify or configure YouTube OAuth credentials")

    # setup-instagram
    si = subparsers.add_parser("setup-instagram", help="View or configure Instagram account login")
    si.add_argument("--status", action="store_true", help="Show current login status")
    si.add_argument("--logout", action="store_true", help="Log out from Instagram")

    # setup-cookies
    sc = subparsers.add_parser("setup-cookies", help="Configure YouTube or Instagram cookie files")
    sc.add_argument("--instagram-file", help="Path to exported Instagram cookies.txt file")

    args = parser.parse_args()

    if args.command == "process-channel":
        cmd_process_channel(args)
    elif args.command == "download":
        cmd_download(args)
    elif args.command == "upload":
        cmd_upload(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "retry":
        cmd_retry(args)
    elif args.command == "setup-oauth":
        cmd_setup_oauth(args)
    elif args.command == "setup-instagram":
        cmd_setup_instagram(args)
    elif args.command == "setup-cookies":
        cmd_setup_cookies(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
