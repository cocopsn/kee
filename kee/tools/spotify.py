"""Spotify tool — currently-playing awareness + control.

Phase 6 roadmap item. Uses Spotify Web API (Authorization Code w/ PKCE).

Setup (one-time):
  1. Create app at https://developer.spotify.com/dashboard
  2. Set redirect URI to `http://127.0.0.1:7330/spotify/callback`
  3. Set env vars in D:/Kee/.env:
        SPOTIFY_CLIENT_ID=...
        SPOTIFY_CLIENT_SECRET=...   (optional with PKCE)
  4. Run `python -m kee.main spotify-auth` to get the refresh token
     (browser pops, user signs in once, token cached at
      data/spotify_token.json)

Tool actions:
  - now_playing      → {is_playing, track, artist, album, progress_ms, ...}
  - play / pause     → resume / pause current playback
  - next / previous  → skip
  - volume(level=0-100)
  - search(query, kind="track|artist|album", limit=5)
  - play_uri(uri)    → spotify:track:... or spotify:album:...
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from kee.config import settings
from kee.tools.base import Tool

logger = logging.getLogger(__name__)


_TOKEN_PATH = settings.data_dir / "spotify_token.json"
_BASE = "https://api.spotify.com/v1"
_AUTH = "https://accounts.spotify.com/api/token"


def _load_token() -> dict[str, Any] | None:
    if not _TOKEN_PATH.exists():
        return None
    try:
        return json.loads(_TOKEN_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _save_token(tok: dict[str, Any]) -> None:
    _TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    _TOKEN_PATH.write_text(json.dumps(tok), encoding="utf-8")


async def _refresh(refresh_token: str) -> dict[str, Any] | None:
    """Use the refresh token to get a fresh access_token. Returns the new
    token dict or None if refresh fails."""
    import httpx
    cid = os.environ.get("SPOTIFY_CLIENT_ID", "").strip()
    secret = os.environ.get("SPOTIFY_CLIENT_SECRET", "").strip()
    if not cid:
        return None
    data = {"grant_type": "refresh_token", "refresh_token": refresh_token,
            "client_id": cid}
    auth = (cid, secret) if secret else None
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(_AUTH, data=data, auth=auth)
    if r.status_code != 200:
        logger.warning("Spotify refresh failed %s: %s", r.status_code, r.text[:200])
        return None
    body = r.json()
    body["obtained_at"] = int(time.time())
    if "refresh_token" not in body:
        body["refresh_token"] = refresh_token  # spotify keeps the same refresh_token
    return body


async def _access_token() -> str | None:
    """Get a valid access_token, refreshing if necessary."""
    tok = _load_token()
    if not tok:
        return None
    expires_at = tok.get("obtained_at", 0) + tok.get("expires_in", 0) - 60
    if time.time() >= expires_at and tok.get("refresh_token"):
        new = await _refresh(tok["refresh_token"])
        if new:
            _save_token(new)
            tok = new
    return tok.get("access_token")


async def _api(method: str, path: str, **kwargs) -> tuple[int, Any]:
    import httpx
    token = await _access_token()
    if not token:
        return 401, {"error": "No Spotify token cached. Run `python -m kee.main spotify-auth` once."}
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = f"Bearer {token}"
    url = path if path.startswith("http") else _BASE + path
    async with httpx.AsyncClient(timeout=8.0) as c:
        r = await c.request(method, url, headers=headers, **kwargs)
    if r.status_code == 204:
        return 204, {}
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, {"raw": r.text[:300]}


class SpotifyTool(Tool):
    name = "spotify"
    description = (
        "Spotify control: now_playing, play, pause, next, previous, "
        "volume(level 0-100), search(query, kind), play_uri(uri). "
        "Token cached in data/spotify_token.json — must be auth'd once via "
        "`python -m kee.main spotify-auth`."
    )
    risk_level = 1  # local action, but has external side effects on the speaker
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["now_playing", "play", "pause", "next", "previous",
                         "volume", "search", "play_uri"],
            },
            "level": {"type": "integer", "description": "0-100 for volume"},
            "query": {"type": "string"},
            "kind": {"type": "string", "enum": ["track", "artist", "album", "playlist"], "default": "track"},
            "limit": {"type": "integer", "default": 5},
            "uri": {"type": "string", "description": "spotify URI (spotify:track:..., spotify:album:..., etc.)"},
        },
        "required": ["action"],
    }

    async def execute(self, action: str, **kwargs: Any) -> dict[str, Any]:
        if action == "now_playing":
            code, body = await _api("GET", "/me/player/currently-playing")
            if code == 204:
                return {"status": "ok", "is_playing": False, "track": None}
            if code != 200:
                return {"status": "error", "code": code, "detail": body}
            item = body.get("item") or {}
            return {
                "status": "ok",
                "is_playing": body.get("is_playing", False),
                "track": item.get("name"),
                "artist": ", ".join(a.get("name", "") for a in item.get("artists", [])),
                "album": (item.get("album") or {}).get("name"),
                "progress_ms": body.get("progress_ms"),
                "duration_ms": item.get("duration_ms"),
                "uri": item.get("uri"),
                "external_url": (item.get("external_urls") or {}).get("spotify"),
            }
        if action == "play":
            code, body = await _api("PUT", "/me/player/play")
            return {"status": "ok" if code in (204, 200) else "error", "code": code, "detail": body}
        if action == "pause":
            code, body = await _api("PUT", "/me/player/pause")
            return {"status": "ok" if code in (204, 200) else "error", "code": code, "detail": body}
        if action == "next":
            code, body = await _api("POST", "/me/player/next")
            return {"status": "ok" if code in (204, 200) else "error", "code": code, "detail": body}
        if action == "previous":
            code, body = await _api("POST", "/me/player/previous")
            return {"status": "ok" if code in (204, 200) else "error", "code": code, "detail": body}
        if action == "volume":
            level = max(0, min(100, int(kwargs.get("level", 50))))
            code, body = await _api("PUT", f"/me/player/volume?volume_percent={level}")
            return {"status": "ok" if code in (204, 200) else "error", "code": code, "level": level}
        if action == "search":
            q = kwargs.get("query", "").strip()
            kind = kwargs.get("kind", "track")
            limit = max(1, min(50, int(kwargs.get("limit", 5))))
            if not q:
                return {"status": "error", "reason": "query required"}
            code, body = await _api("GET", f"/search?q={q}&type={kind}&limit={limit}")
            if code != 200:
                return {"status": "error", "code": code, "detail": body}
            items = (body.get(f"{kind}s") or {}).get("items", [])
            return {
                "status": "ok", "kind": kind, "count": len(items),
                "items": [
                    {
                        "name": it.get("name"),
                        "uri": it.get("uri"),
                        "artists": [a.get("name") for a in it.get("artists", [])] if kind != "artist" else None,
                    }
                    for it in items
                ],
            }
        if action == "play_uri":
            uri = kwargs.get("uri", "")
            if not uri:
                return {"status": "error", "reason": "uri required"}
            payload = ({"uris": [uri]} if uri.startswith("spotify:track:")
                       else {"context_uri": uri})
            code, body = await _api("PUT", "/me/player/play", json=payload)
            return {"status": "ok" if code in (204, 200) else "error", "code": code, "detail": body}
        return {"status": "error", "reason": f"unknown action {action!r}"}


tool = SpotifyTool()
