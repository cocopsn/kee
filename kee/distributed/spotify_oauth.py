"""Spotify OAuth — Authorization Code with PKCE.

One-time flow that opens a browser, lets the user sign in, and caches
the refresh_token so the SpotifyTool can call the API without further
human interaction.

Usage: python -m kee.main spotify-auth
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import http.server
import logging
import os
import secrets
import threading
import time
import urllib.parse as up
import webbrowser
from typing import Any

from kee.config import settings

logger = logging.getLogger(__name__)


_AUTH_URL = "https://accounts.spotify.com/authorize"
_TOKEN_URL = "https://accounts.spotify.com/api/token"
_SCOPES = " ".join([
    "user-read-currently-playing",
    "user-read-playback-state",
    "user-modify-playback-state",
    "user-read-private",
])


def _pkce_pair() -> tuple[str, str]:
    """(verifier, challenge). RFC 7636."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def _save_token(tok: dict[str, Any]) -> None:
    import json
    p = settings.data_dir / "spotify_token.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    tok["obtained_at"] = int(time.time())
    p.write_text(json.dumps(tok), encoding="utf-8")


def run_oauth() -> int:
    cid = os.environ.get("SPOTIFY_CLIENT_ID", "").strip()
    if not cid:
        print("ERROR: SPOTIFY_CLIENT_ID not set in D:/Kee/.env")
        print("Create an app at https://developer.spotify.com/dashboard")
        print("Set redirect URI to http://127.0.0.1:8765/callback")
        return 1
    secret = os.environ.get("SPOTIFY_CLIENT_SECRET", "").strip()  # optional
    redirect = "http://127.0.0.1:8765/callback"
    state = secrets.token_urlsafe(16)
    verifier, challenge = _pkce_pair()

    auth_params = {
        "client_id": cid,
        "response_type": "code",
        "redirect_uri": redirect,
        "scope": _SCOPES,
        "state": state,
        "code_challenge_method": "S256",
        "code_challenge": challenge,
    }
    auth_url = f"{_AUTH_URL}?{up.urlencode(auth_params)}"

    received: dict[str, Any] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args, **kwargs):  # silence
            pass

        def do_GET(self):
            parsed = up.urlparse(self.path)
            qs = up.parse_qs(parsed.query)
            if parsed.path != "/callback":
                self.send_response(404); self.end_headers(); return
            if "error" in qs:
                received["error"] = qs.get("error", [""])[0]
            else:
                received["code"] = qs.get("code", [""])[0]
                received["state"] = qs.get("state", [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h2>Spotify auth complete. You can close this tab.</h2>")
            threading.Thread(target=self.server.shutdown, daemon=True).start()

    server = http.server.HTTPServer(("127.0.0.1", 8765), Handler)
    print(f"Open this URL in your browser if it doesn't open automatically:\n{auth_url}\n")
    try:
        webbrowser.open(auth_url)
    except Exception:
        pass
    print("Waiting for callback on http://127.0.0.1:8765 …")
    server.serve_forever()

    if "error" in received:
        print(f"Auth error: {received['error']}")
        return 2
    if received.get("state") != state:
        print("State mismatch — aborting (CSRF guard)")
        return 3
    code = received.get("code")
    if not code:
        print("No code received")
        return 4

    # Exchange code → tokens
    import httpx
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect,
        "client_id": cid,
        "code_verifier": verifier,
    }
    auth = (cid, secret) if secret else None
    r = httpx.post(_TOKEN_URL, data=data, auth=auth, timeout=10)
    if r.status_code != 200:
        print(f"Token exchange failed: {r.status_code} {r.text[:300]}")
        return 5
    tok = r.json()
    _save_token(tok)
    print(f"OK. Token cached at {settings.data_dir / 'spotify_token.json'}")
    print(f"  scopes: {tok.get('scope')}")
    print(f"  expires_in: {tok.get('expires_in')} s")
    return 0
