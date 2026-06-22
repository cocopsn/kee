"""Google OAuth token manager.

Single source of truth for `google.oauth2.credentials.Credentials` objects
inside Kee. Reads the client_secret JSON, runs the install flow on first
use (browser-based), caches the token + refresh token, transparently
renews when expired.

Token storage: `data/google_token.json` (Kee-local).

Setup choices for the user:

  A) **Reuse an existing Google Cloud OAuth client** (e.g. auctorum-systems
     or auctorum-personal) — works only if you add `http://localhost`
     to its authorised redirect URIs in Google Cloud Console
     (APIs & Services → Credentials → click client → add URI).
     Then point `KEE_GOOGLE_CLIENT_SECRET` at the JSON.

  B) **Create a new "Desktop app" OAuth client** (recommended for Kee).
     Console → Credentials → + Create Credentials → OAuth Client ID →
     Application type: Desktop. Download JSON, drop at
     `D:/Kee/data/google_client.json`. No env var needed (default lookup).

Either path: first call to `get_credentials(scopes)` opens a browser tab
once, you click your account, paste-back happens automatically, token is
cached. Subsequent calls reuse the cached token + auto-refresh.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Iterable

from kee.config import settings

logger = logging.getLogger(__name__)


def _client_secret_path() -> Path | None:
    """Resolve the Google OAuth client_secret JSON path.

    Lookup order:
      1. `KEE_GOOGLE_CLIENT_SECRET` env var (absolute path)
      2. `D:/Kee/data/google_client.json` (canonical default)
      3. The first `client_secret_*.json` found under `D:/Kee/data/`
    """
    explicit = os.environ.get("KEE_GOOGLE_CLIENT_SECRET")
    if explicit and Path(explicit).exists():
        return Path(explicit)
    canonical = settings.data_dir / "google_client.json"
    if canonical.exists():
        return canonical
    candidates = sorted(settings.data_dir.glob("client_secret_*.json"))
    return candidates[0] if candidates else None


def _token_path() -> Path:
    return settings.data_dir / "google_token.json"


def get_credentials(
    scopes: Iterable[str],
    interactive: bool = True,
):
    """Return a `google.oauth2.credentials.Credentials` for the given scopes.

    Two failure modes the previous version had silently:
      1. Loading the file with a NARROWER scopes_list and then refreshing
         saved BACK only that narrow set, irrevocably shrinking the token.
      2. `creds.valid` does not check whether the granted scopes cover
         what the caller asked for, so the token would appear "valid"
         even when missing scopes (calendar tool then 403'd).
    Fix both: read the granted scopes from the file, take the UNION with
    what's requested, and force re-auth (or hard error if non-interactive)
    if a requested scope isn't actually granted.
    """
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow

    requested = sorted(set(scopes))
    token_p = _token_path()

    granted_in_file: list[str] = []
    creds = None
    if token_p.exists():
        try:
            data = json.loads(token_p.read_text(encoding="utf-8"))
            granted_in_file = list(data.get("scopes") or [])
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Token file unreadable (%s) — will re-auth.", e)

        # Load with the UNION so refresh preserves all granted scopes.
        union_scopes = sorted(set(requested) | set(granted_in_file))
        try:
            creds = Credentials.from_authorized_user_file(str(token_p), union_scopes)
        except Exception as e:
            logger.warning("Failed to load cached token (%s) — re-authing.", e)
            creds = None

    # Coverage check with scope hierarchy. Some scopes imply others:
    # `…/calendar` covers `…/calendar.readonly`, `…/gmail.modify` covers
    # `…/gmail.readonly`, `https://mail.google.com/` covers all gmail.
    def _covers(granted: set[str], req: str) -> bool:
        if req in granted:
            return True
        # Hierarchical implications
        for g in granted:
            # full calendar implies any calendar.* readonly variant
            if req.startswith(g + ".") or g.startswith(req.rstrip("/") + "."):
                pass  # not enough on its own
            if req.endswith(".readonly"):
                base = req[: -len(".readonly")]
                if g == base:
                    return True
            if g == "https://mail.google.com/" and "/auth/gmail" in req:
                return True
            if g == "https://www.googleapis.com/auth/gmail.modify" and req == \
               "https://www.googleapis.com/auth/gmail.readonly":
                return True
        return False

    granted_set = set(granted_in_file)
    missing = sorted(r for r in requested if not _covers(granted_set, r))
    if missing:
        logger.warning(
            "Cached token missing scopes %s — needs re-auth (interactive=%s)",
            missing, interactive,
        )
        creds = None

    if creds and creds.valid and not missing:
        return creds
    if creds and creds.expired and creds.refresh_token and not missing:
        try:
            creds.refresh(Request())
            token_p.write_text(creds.to_json(), encoding="utf-8")
            return creds
        except Exception as e:
            logger.warning("Token refresh failed (%s) — re-authing.", e)
            creds = None

    if not interactive:
        if missing:
            raise RuntimeError(
                f"Google token cached but missing scopes {missing}. "
                "Run `python -m kee.main google-auth` to grant them."
            )
        raise RuntimeError(
            "Google credentials missing or expired and `interactive=False`. "
            "Run `python -m kee.main google-auth` once to authorize."
        )

    secret_p = _client_secret_path()
    if secret_p is None:
        raise RuntimeError(
            "No Google OAuth client_secret JSON found. Either:\n"
            "  - set KEE_GOOGLE_CLIENT_SECRET=<path/to/client_secret.json>, or\n"
            "  - drop the file at D:/Kee/data/google_client.json"
        )

    # Re-auth flow asks for the UNION of what's needed now plus what was
    # already granted, so we never narrow the token's powers.
    flow_scopes = sorted(set(requested) | set(granted_in_file))
    flow = InstalledAppFlow.from_client_secrets_file(str(secret_p), flow_scopes)
    creds = flow.run_local_server(port=0, prompt="consent")
    token_p.parent.mkdir(parents=True, exist_ok=True)
    token_p.write_text(creds.to_json(), encoding="utf-8")
    logger.info("Google token cached at %s with scopes %s", token_p, creds.scopes)
    return creds


def status() -> dict:
    """Quick health snapshot for /check and the Telegram /status command."""
    token_p = _token_path()
    secret_p = _client_secret_path()
    info: dict = {
        "client_secret_present": secret_p is not None,
        "client_secret_path": str(secret_p) if secret_p else None,
        "token_cached": token_p.exists(),
        "token_path": str(token_p) if token_p.exists() else None,
    }
    if token_p.exists():
        try:
            data = json.loads(token_p.read_text(encoding="utf-8"))
            info["scopes"] = data.get("scopes")
            info["expiry"] = data.get("expiry")
        except json.JSONDecodeError:
            info["token_corrupt"] = True
    return info
