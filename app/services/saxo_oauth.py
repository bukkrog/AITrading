"""Saxo OpenAPI OAuth (authorization code flow) — SIM/DEMO environment.

Replaces the daily 24h developer token: the user logs in ONCE in a browser,
we exchange the code for an access token (~20 min) + refresh token, and a
daemon thread renews the session before every expiry. The refresh token is
persisted to a git-ignored JSON file so the session survives restarts.

Security invariants:
- Tokens are never logged.
- App secret comes from .env (SAXO_APP_SECRET) and never leaves the server.
- Everything targets the environment in settings.saxo_environment; live use
  additionally requires a Saxo-approved live application (not this demo app).
"""
from __future__ import annotations

import json
import secrets
import threading
import time
from pathlib import Path

from app.config import settings
from app.logging_config import get_logger

logger = get_logger(__name__)

_TOKEN_FILE = Path(__file__).resolve().parent.parent.parent / "saxo_oauth.json"
_LOCK = threading.Lock()
_STATE: dict = {"pending_state": None, "expires_at": 0.0, "refresh_token": None}
_REFRESH_THREAD: threading.Thread | None = None


def _auth_base() -> str:
    return (
        "https://sim.logonvalidation.net"
        if settings.saxo_environment == "sim"
        else "https://live.logonvalidation.net"
    )


def configured() -> bool:
    return bool(settings.saxo_app_key and settings.saxo_app_secret and settings.saxo_redirect_uri)


def auth_url() -> str:
    """Build the Saxo login URL (and remember the CSRF state)."""
    from urllib.parse import urlencode

    state = secrets.token_urlsafe(24)
    _STATE["pending_state"] = state
    q = urlencode(
        {
            "response_type": "code",
            "client_id": settings.saxo_app_key,
            "redirect_uri": settings.saxo_redirect_uri,
            "state": state,
        }
    )
    return f"{_auth_base()}/authorize?{q}"


def _token_request(data: dict) -> dict:
    import httpx

    resp = httpx.post(
        f"{_auth_base()}/token",
        data=data,
        auth=(settings.saxo_app_key or "", settings.saxo_app_secret or ""),
        timeout=15.0,
    )
    resp.raise_for_status()
    return resp.json()


def _apply(tokens: dict) -> None:
    """Adopt a token response: activate access token, persist refresh token."""
    with _LOCK:
        settings.saxo_access_token = tokens["access_token"]
        _STATE["refresh_token"] = tokens.get("refresh_token") or _STATE["refresh_token"]
        _STATE["expires_at"] = time.time() + float(tokens.get("expires_in", 1200))
        try:
            _TOKEN_FILE.write_text(
                json.dumps({"refresh_token": _STATE["refresh_token"], "env": settings.saxo_environment})
            )
        except OSError as exc:
            logger.warning("Could not persist Saxo refresh token: %s", exc)
    logger.info(
        "Saxo OAuth session active (%s), next refresh in ~%d min",
        settings.saxo_environment,
        max(1, int((_STATE["expires_at"] - time.time() - 120) / 60)),
    )
    _ensure_refresh_thread()


def exchange_code(code: str, state: str | None) -> None:
    if not _STATE["pending_state"] or state != _STATE["pending_state"]:
        raise ValueError("OAuth state mismatch — start login again from /control/saxo/login")
    _STATE["pending_state"] = None
    _apply(
        _token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.saxo_redirect_uri,
            }
        )
    )


def refresh_now() -> bool:
    """Refresh the access token; returns True on success."""
    rt = _STATE["refresh_token"]
    if not (rt and configured()):
        return False
    try:
        _apply(
            _token_request(
                {"grant_type": "refresh_token", "refresh_token": rt, "redirect_uri": settings.saxo_redirect_uri}
            )
        )
        return True
    except Exception as exc:
        logger.warning("Saxo token refresh failed: %s", exc)
        return False


def _refresh_loop() -> None:
    """Renew ~2 min before expiry, forever, until refresh becomes impossible."""
    failures = 0
    while True:
        wait = max(30.0, _STATE["expires_at"] - time.time() - 120)
        time.sleep(wait)
        if not _STATE["refresh_token"]:
            return
        if refresh_now():
            failures = 0
        else:
            failures += 1
            if failures >= 5:
                logger.error("Saxo OAuth session lost after %d refresh failures — log in again.", failures)
                _STATE["refresh_token"] = None
                return
            time.sleep(30)


def _ensure_refresh_thread() -> None:
    global _REFRESH_THREAD
    if _REFRESH_THREAD is None or not _REFRESH_THREAD.is_alive():
        _REFRESH_THREAD = threading.Thread(target=_refresh_loop, daemon=True, name="saxo-oauth-refresh")
        _REFRESH_THREAD.start()


def resume() -> bool:
    """On startup: reuse a persisted refresh token, if any. True if session resumed."""
    if not configured() or not _TOKEN_FILE.exists():
        return False
    try:
        data = json.loads(_TOKEN_FILE.read_text())
    except (OSError, ValueError):
        return False
    if data.get("env") != settings.saxo_environment or not data.get("refresh_token"):
        return False
    _STATE["refresh_token"] = data["refresh_token"]
    return refresh_now()


def status() -> dict:
    ttl = int(_STATE["expires_at"] - time.time())
    return {
        "configured": configured(),
        "connected": bool(_STATE["refresh_token"]) and ttl > 0,
        "access_token_expires_in": max(0, ttl),
        "environment": settings.saxo_environment,
    }
