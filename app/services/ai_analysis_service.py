"""AI analysis layer — news / sentiment scoring.

Principle #1: the AI is *decision support*, never the sole basis for a trade —
its score is only one of several gates in the decision model.

Authentication (principle: no hard-coded keys):
  * ``ai_auth_mode="oauth"`` (default) — authenticate via an ``ant auth login``
    OAuth profile (a zero-arg client picks it up automatically) or an explicit
    ``ANTHROPIC_AUTH_TOKEN`` bearer token. No static API key required.
  * ``ai_auth_mode="api_key"`` — use ``ANTHROPIC_API_KEY``.
  * ``ai_auth_mode="off"`` — never call Claude.

In every mode, if the AI call fails or is disabled, a deterministic keyword
heuristic is used so the platform and its tests run fully offline.

Every score is returned with a rationale (principle #3).
"""
from __future__ import annotations

import json

from app.config import settings
from app.core.enums import SignalDirection
from app.logging_config import get_logger
from app.schemas.trading import NewsScore

logger = get_logger(__name__)

_POSITIVE = {
    "beats", "beat", "surge", "surges", "record", "growth", "upgrade", "upgraded",
    "raises", "strong", "profit", "gains", "wins", "approval", "expansion", "bullish",
    "outperform", "buy", "breakthrough", "partnership", "dividend",
}
_NEGATIVE = {
    "miss", "misses", "plunge", "plunges", "downgrade", "downgraded", "cuts", "cut",
    "weak", "loss", "losses", "probe", "lawsuit", "recall", "warning", "bearish",
    "sell", "fraud", "layoffs", "decline", "slump", "bankruptcy",
}

_SYSTEM_PROMPT = (
    "You are a disciplined equity news analyst. Given recent headlines for a "
    "single stock, assess sentiment for a short-to-medium-term horizon. Be "
    "conservative: only assign extreme scores with strong evidence. Always "
    "explain the specific headlines driving your score."
)

_SCHEMA = {
    "name": "news_assessment",
    "description": "Structured sentiment assessment for a single equity.",
    "input_schema": {
        "type": "object",
        "properties": {
            "score": {
                "type": "number",
                "description": "0-100. 50 = neutral, >50 bullish, <50 bearish.",
            },
            "direction": {
                "type": "string",
                "enum": ["bullish", "bearish", "neutral"],
            },
            "rationale": {
                "type": "string",
                "description": "Concise explanation citing the driving headlines.",
            },
        },
        "required": ["score", "direction", "rationale"],
    },
}


def _heuristic(symbol: str, headlines: list[str]) -> NewsScore:
    if not headlines:
        return NewsScore(
            symbol=symbol,
            score=50.0,
            direction=SignalDirection.NEUTRAL,
            rationale="No headlines available; neutral (conservative default).",
            source="heuristic",
        )
    pos = neg = 0
    for line in headlines:
        words = {w.strip(".,!?:;\"'()").lower() for w in line.split()}
        pos += len(words & _POSITIVE)
        neg += len(words & _NEGATIVE)
    total = pos + neg
    if total == 0:
        score = 50.0
    else:
        # Map net sentiment ratio to 0-100 with a bounded response.
        ratio = (pos - neg) / total
        score = max(0.0, min(100.0, 50.0 + ratio * 45.0))
    direction = (
        SignalDirection.BULLISH
        if score > 55
        else SignalDirection.BEARISH
        if score < 45
        else SignalDirection.NEUTRAL
    )
    return NewsScore(
        symbol=symbol,
        score=round(score, 1),
        direction=direction,
        rationale=f"Keyword sentiment over {len(headlines)} headline(s): "
        f"{pos} positive / {neg} negative signals.",
        source="heuristic",
    )


def _build_client():
    """Construct an Anthropic client per the configured auth mode.

    OAuth mode relies on the SDK's credential resolution: an explicit
    ``auth_token`` (Bearer) if provided, otherwise a zero-arg client that
    picks up the ``ant auth login`` profile from disk — no API key needed.
    """
    from anthropic import Anthropic  # imported lazily; optional dependency

    if settings.ai_auth_mode == "api_key":
        return Anthropic(api_key=settings.anthropic_api_key)
    if settings.anthropic_auth_token:  # oauth with an explicit bearer token
        return Anthropic(auth_token=settings.anthropic_auth_token)
    return Anthropic()  # oauth via `ant auth login` profile


def _ai_enabled() -> bool:
    if settings.ai_auth_mode == "off":
        return False
    if settings.ai_auth_mode == "api_key":
        return bool(settings.anthropic_api_key)
    # oauth: an explicit token, or trust that a login profile exists on disk.
    return True


def _claude(symbol: str, headlines: list[str]) -> NewsScore:
    client = _build_client()
    user_content = (
        f"Ticker: {symbol}\nRecent headlines:\n"
        + "\n".join(f"- {h}" for h in headlines)
        + "\n\nCall the `news_assessment` tool with your assessment."
    )
    resp = client.messages.create(
        model=settings.ai_model,
        max_tokens=1024,
        system=_SYSTEM_PROMPT,
        tools=[_SCHEMA],
        tool_choice={"type": "tool", "name": "news_assessment"},
        messages=[{"role": "user", "content": user_content}],
    )
    tool_use = next(b for b in resp.content if b.type == "tool_use")
    data = tool_use.input if isinstance(tool_use.input, dict) else json.loads(tool_use.input)
    return NewsScore(
        symbol=symbol,
        score=float(data["score"]),
        direction=SignalDirection(data["direction"]),
        rationale=data["rationale"],
        source="claude",
    )


def analyze_news(symbol: str, headlines: list[str] | None = None) -> NewsScore:
    """Return a :class:`NewsScore` for ``symbol`` given recent headlines."""
    headlines = headlines or []
    if _ai_enabled():
        try:
            return _claude(symbol, headlines)
        except Exception as exc:  # pragma: no cover - network/dep/auth failure path
            logger.warning(
                "Claude analysis failed (%s); falling back to heuristic", exc
            )
    return _heuristic(symbol, headlines)
