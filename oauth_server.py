#!/usr/bin/env python3
"""
Robust YouTube Data API v3 OAuth Flow Handler
Persists PKCE code_verifier so process restarts or separate CLI invocations can exchange codes.
"""

import argparse
import base64
import hashlib
import http.server
import json
import os
import sys
import threading
import time
import urllib.parse
from pathlib import Path
import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

CLIENT_SECRET_PATH = Path("/root/.hermes/youtube_client_secret.json")
TOKEN_PATH = Path("/root/.hermes/youtube_token.json")
VERIFIER_FILE = Path("/root/youtube-workflow/.code_verifier")
STATUS_FILE = Path("/root/youtube-workflow/.oauth_status")

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly"
]
PORT = 8080
REDIRECT_URI = f"http://localhost:{PORT}/"


def generate_pkce():
    verifier = base64.urlsafe_b64encode(os.urandom(40)).decode("utf-8").rstrip("=")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("utf-8")).digest()).decode("utf-8").rstrip("=")
    return verifier, challenge


def exchange_code(code: str, code_verifier: str = None) -> bool:
    if not CLIENT_SECRET_PATH.exists():
        print(f"Error: {CLIENT_SECRET_PATH} not found")
        return False

    with open(CLIENT_SECRET_PATH) as f:
        data = json.load(f)
    cs = data.get("installed") or data.get("web")

    if not code_verifier and VERIFIER_FILE.exists():
        code_verifier = VERIFIER_FILE.read_text().strip()

    post_data = {
        "client_id": cs["client_id"],
        "client_secret": cs["client_secret"],
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI
    }
    if code_verifier:
        post_data["code_verifier"] = code_verifier

    token_uri = cs.get("token_uri", "https://oauth2.googleapis.com/token")
    res = requests.post(token_uri, data=post_data)

    if res.status_code != 200:
        print(f"Token exchange failed ({res.status_code}): {res.text}")
        return False

    token_info = res.json()
    creds = Credentials(
        token=token_info["access_token"],
        refresh_token=token_info.get("refresh_token"),
        token_uri=token_uri,
        client_id=cs["client_id"],
        client_secret=cs["client_secret"],
        scopes=token_info.get("scope", "").split()
    )

    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(creds.to_json())

    # Verify channel
    try:
        service = build("youtube", "v3", credentials=creds)
        ch = service.channels().list(part="snippet", mine=True).execute()
        title = ch["items"][0]["snippet"]["title"] if ch.get("items") else "Authenticated (no channel title)"
        print(f"\nSUCCESS! Authenticated as YouTube Channel: {title}")
        print(f"Token saved to: {TOKEN_PATH}")
        STATUS_FILE.write_text(f"success: {title}")
        return True
    except Exception as e:
        print(f"Authenticated, but channel check failed: {e}")
        STATUS_FILE.write_text(f"success: {e}")
        return True


def start_server_and_prompt():
    if not CLIENT_SECRET_PATH.exists():
        print(f"Error: {CLIENT_SECRET_PATH} not found")
        sys.exit(1)

    with open(CLIENT_SECRET_PATH) as f:
        data = json.load(f)
    cs = data.get("installed") or data.get("web")

    verifier, challenge = generate_pkce()
    VERIFIER_FILE.write_text(verifier)

    state = base64.urlsafe_b64encode(os.urandom(16)).decode("utf-8").rstrip("=")
    scope_str = " ".join(SCOPES)

    auth_params = {
        "response_type": "code",
        "client_id": cs["client_id"],
        "redirect_uri": REDIRECT_URI,
        "scope": scope_str,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "consent"
    }
    auth_url = "https://accounts.google.com/o/oauth2/auth?" + urllib.parse.urlencode(auth_params)

    print("\n" + "=" * 70)
    print("YOUTUBE OAUTH AUTHORIZATION")
    print("=" * 70)
    print("Please open this URL to authorize:")
    print(f"\n{auth_url}\n")
    print("=" * 70)
    print("Listening on http://localhost:8080/ for redirect callback...")
    sys.stdout.flush()

    token_acquired = threading.Event()

    class OAuthHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed.query)

            if "code" in query:
                code = query["code"][0]
                ok = exchange_code(code, verifier)
                if ok:
                    self.send_response(200)
                    self.send_header("Content-type", "text/html")
                    self.end_headers()
                    self.wfile.write(b"<h1>Authorization Successful!</h1><p>You can close this window.</p>")
                    token_acquired.set()
                else:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"<h1>Authorization Failed.</h1>")
            else:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Missing code.")

        def log_message(self, format, *args):
            pass

    server = http.server.HTTPServer(("0.0.0.0", PORT), OAuthHandler)
    server.timeout = 1.0

    def server_thread():
        while not token_acquired.is_set():
            server.handle_request()

    t = threading.Thread(target=server_thread, daemon=True)
    t.start()

    start_time = time.time()
    while not token_acquired.is_set():
        if time.time() - start_time > 900:
            print("Timed out.")
            break
        time.sleep(1)

    server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--exchange", help="Full redirect URL or raw auth code to exchange")
    args = parser.parse_args()

    if args.exchange:
        inp = args.exchange.strip()
        code = inp
        if "code=" in inp:
            parsed = urllib.parse.urlparse(inp)
            q = urllib.parse.parse_qs(parsed.query)
            if "code" in q:
                code = q["code"][0]
        ok = exchange_code(code)
        sys.exit(0 if ok else 1)
    else:
        start_server_and_prompt()
