"""Persist Setup-page changes across restarts.

Settings edited in the web UI used to be runtime-only, so every restart
silently reverted them to .env/defaults. Now every applied update is merged
into a git-ignored JSON file and re-applied at startup.

Precedence (last wins): code defaults < .env < settings_override.json.
Rationale: what the user last chose in the UI is the intended configuration.
Delete the file (or a key in it) to fall back to .env/defaults.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.config import settings
from app.logging_config import get_logger

logger = get_logger(__name__)

_FILE = Path(__file__).resolve().parent.parent.parent / "settings_override.json"

#: Never write these to disk in plain text — they stay runtime-only.
_NEVER_PERSIST = {"anthropic_auth_token", "anthropic_api_key", "saxo_access_token",
                  "saxo_app_secret"}


def persist(updates: dict) -> None:
    """Merge applied updates into the override file (atomic-enough for one writer)."""
    data: dict = {}
    if _FILE.exists():
        try:
            data = json.loads(_FILE.read_text())
        except (OSError, ValueError):
            data = {}
    data.update({k: v for k, v in updates.items() if k not in _NEVER_PERSIST})
    try:
        _FILE.write_text(json.dumps(data, indent=1))
    except OSError as exc:
        logger.warning("Could not persist settings: %s", exc)


def apply_overrides() -> list[str]:
    """At startup: re-apply persisted Setup values onto the settings object."""
    if not _FILE.exists():
        return []
    try:
        data = json.loads(_FILE.read_text())
    except (OSError, ValueError) as exc:
        logger.warning("settings_override.json unreadable (%s) — ignored", exc)
        return []
    applied: list[str] = []
    for key, value in data.items():
        try:
            if key.startswith("risk_"):
                setattr(settings.risk, key.removeprefix("risk_"), value)
            else:
                setattr(settings, key, value)
            applied.append(key)
        except Exception as exc:  # a renamed/removed field must not block startup
            logger.warning("Override %r not applied: %s", key, exc)
    if applied:
        logger.info("Applied %d persisted settings: %s", len(applied), ", ".join(sorted(applied)))
    return applied
