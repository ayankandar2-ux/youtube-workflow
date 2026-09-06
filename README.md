# Multi-Platform Video Workflow & Publisher

An automated, terminal-driven pipeline and TUI for fetching, processing, and publishing video content across **YouTube**, **Instagram**, and **Facebook**.

---

## Features

### 1. YouTube Automation
* **Channel & Playlist Processing**: Fetch most-viewed videos from any channel or entire playlists.
* **Smart Filtering**: Configurable duration limits (e.g. skip videos over 2 hours or prompt before processing).
* **Automated Description Cleaning**: Rewrites video descriptions to strip original creator promotions, links, and URLs for clean reposting.
* **YouTube Data API v3**: Automatic OAuth2 authentication and scheduled/batch uploading.

### 2. Instagram Reels
* Upload Reels directly with custom captions, hashtags, and generated thumbnails via `instagrapi`.
* Multi-account and session cookie persistence.

### 3. Facebook Publishing (Dual-Mode)
* **Official Meta Graph API (Pages)**: Fast 3-phase Reels upload (`start` → `rupload` → `publish`) for Facebook Pages. Robust against UI changes.
* **Headless Browser Automation (Personal Profiles)**: Playwright-powered mobile composer automation using session cookies without requiring Meta API verification.
* **Instagram Cross-Posting**: Optional auto-share to Facebook via Meta Accounts Center.

### 4. Interactive Keyboard-Navigable TUI
* Clean terminal UI with arrow-key navigation, Enter selection, and real-time upload progress.
* Multi-platform destination selector: YouTube Only, Instagram Only, Facebook Only, or All Platforms simultaneously.

---

## Project Structure

```text
├── app.py                  # Main interactive TUI application
├── youtube_workflow.py     # Core pipeline logic and CLI tool
├── facebook_uploader.py    # Facebook Graph API & Headless Playwright uploader
├── oauth_server.py         # Local OAuth helper for YouTube authorization
├── tui_menu.py             # Keyboard menu engine (arrow keys + Enter)
├── run.sh                  # One-click launcher script
├── yw                      # CLI shortcut wrapper
├── requirements.txt        # Python dependencies
└── .env.example            # Environment variables template
```

---

## Getting Started

### 1. Clone & Install Dependencies
```bash
git clone <REPO_URL>
cd youtube-workflow
pip install -r requirements.txt
playwright install chromium
```

### 2. Configure Credentials
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Add your credentials:
* **Facebook Page**: Set `FB_PAGE_ID` and `FB_PAGE_TOKEN` in `.env` (or configure via Settings in `app.py`).
* **YouTube**: Place your `client_secret_*.json` from Google Cloud Console in the project root or `~/.hermes/`.
* **Instagram**: Configure your login credentials in the Settings menu of `app.py`.

### 3. Run
Launch the interactive interface:
```bash
./run.sh
# or
python3 app.py
```

Or use the CLI directly:
```bash
# Check Facebook integration status
python3 facebook_uploader.py status

# Upload a video directly to Facebook
python3 facebook_uploader.py upload "/path/to/video.mp4" "My Video Caption #shorts"

# Download and process a channel
./yw channel "https://youtube.com/@channel" --count 5
```

---

## License
MIT License
