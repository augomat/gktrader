"""Deterministic English alert rendering for all alert variants.

Produces a complete AlertPayload with text, continuation_messages, and buttons.
All content is generated deterministically from structured inputs — no LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from gktrader.alerts.continuation import build_continuation_messages
from gktrader.alerts.keyboard import build_bearish_keyboard, build_bullish_keyboard
from gktrader.domain.contracts import (
    AlertPayload,
    MarketSnapshotContract,
    PriorBullishSignal,
    SignalDecision,
)
from gktrader.domain.enums import AlertLevel, Direction

# Telegram message body limit in characters
_TELEGRAM_MAX_LEN = 4096


@dataclass
class LatencyInfo:
    """Computed latency information for an alert."""

    latency_seconds: int | None = None
    latency_label: str = "N/A"


@dataclass
class AlertRenderContext:
    """All structured data needed to render an alert payload."""

    alert_id: str
    level: AlertLevel
    decision: SignalDecision
    ticker: str
    company_name: str
    event_type: str
    direction: Direction
    source_name: str
    source_url: str
    fetch_path: str
    published_at: datetime | None = None
    detected_at: datetime | None = None
    rationale: str = ""
    evidence: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    classifier_confidence: float = 0.0
    mapping_confidence: float = 0.0
    market_snapshot: MarketSnapshotContract | None = None
    prior_bullish_signals: list[PriorBullishSignal] = field(default_factory=list)

    def get_latency_info(self) -> LatencyInfo:
        """Compute latency from detection to publish time."""
        if self.published_at is not None and self.detected_at is not None:
            delta = self.detected_at - self.published_at
            secs = int(delta.total_seconds())
            if secs >= 0:
                return LatencyInfo(
                    latency_seconds=secs,
                    latency_label=_format_duration(secs),
                )
        return LatencyInfo()


def _format_duration(seconds: int) -> str:
    """Format a duration in seconds to a human-readable label."""
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    secs_remaining = seconds % 60
    if minutes < 60:
        return f"{minutes}m{secs_remaining}s" if secs_remaining else f"{minutes}m"
    hours = minutes // 60
    mins_remaining = minutes % 60
    return f"{hours}h{mins_remaining}m" if mins_remaining else f"{hours}h"


def _direction_emoji(direction: Direction) -> str:
    """Return alert-level emoji and label prefix."""
    mapping = {
        Direction.BULLISH: ("🚀", "BULLISH"),
        Direction.BEARISH: ("🔴", "BEARISH"),
        Direction.NEUTRAL: ("⚪", "NEUTRAL"),
        Direction.UNCLEAR: ("❓", "UNCLEAR"),
    }
    return mapping.get(direction, ("ℹ️", "INFO"))


def _level_label(level: AlertLevel) -> str:
    """Return a display label for the alert level."""
    labels = {
        AlertLevel.TRADEABLE: "TRADEABLE",
        AlertLevel.REVIEW: "REVIEW",
        AlertLevel.AVOID_CHASE: "AVOID CHASE",
        AlertLevel.WATCH: "WATCH",
        AlertLevel.IGNORE: "IGNORE",
    }
    return labels.get(level, str(level))


def _escape_markdown_v2(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2.

    Telegrams's MarkdownV2 requires escaping: _ * [ ] ( ) ~ ` > # + - = | { } . !
    We use MarkdownV2 selectively for bold/italic only, escaping the rest.
    """
    special_chars = r"_*[]()~`>#+-=|{}.!\\"
    result: list[str] = []
    for ch in text:
        if ch in special_chars:
            result.append(f"\\{ch}")
        else:
            result.append(ch)
    return "".join(result)


# ── Main rendering entry point ─────────────────────────────────────────────


def render_alert_payload(context: AlertRenderContext) -> AlertPayload:
    """Render a complete alert payload from structured context.

    Args:
        context: All structured inputs for the alert.

    Returns:
        An AlertPayload with text, buttons, continuation_messages, and dedupe_key.

    Raises:
        ValueError: If the alert level is WATCH (should never be rendered for delivery).
    """
    if context.level == AlertLevel.WATCH:
        raise ValueError("WATCH alerts must not be rendered for delivery")

    dedupe_key = _build_dedupe_key(context)

    # Build the main alert text
    text = _render_alert_body(context)

    # Build buttons
    buttons = _build_buttons(context)

    # Build bearish history continuation messages
    continuation_messages = _render_continuation(context)

    # If the main text alone exceeds the limit, split it.
    # The continuation messages field is for bearish history overflow only.
    if len(text) > _TELEGRAM_MAX_LEN:
        # Truncate the main alert body gracefully, push detail to continuations
        main_part, extra = _split_oversized_body(text)
        text = main_part
        continuation_messages = [extra] + continuation_messages

    return AlertPayload(
        alert_id=context.alert_id,
        level=context.level,
        text=text,
        continuation_messages=continuation_messages,
        buttons=buttons,
        dedupe_key=dedupe_key,
        ticker=context.ticker,
        company=context.company_name,
    )


def _build_dedupe_key(context: AlertRenderContext) -> str:
    """Build a deterministic deduplication key from alert context."""
    ticker = context.ticker.upper() if context.ticker else "UNKNOWN"
    return f"{ticker}:{context.event_type}:{context.direction.value}:{context.level.value}"


# ── Alert body rendering ────────────────────────────────────────────────────


def _render_alert_body(context: AlertRenderContext) -> str:
    """Render the full alert message body."""
    emoji, dir_label = _direction_emoji(context.direction)
    level_label = _level_label(context.level)

    lines: list[str] = []

    # Header
    ticker_line = f"${context.ticker}" if context.ticker else ""
    company_part = f" ({context.company_name})" if context.company_name else ""
    header = f"{emoji} {dir_label} ALERT — {ticker_line}{company_part}"
    lines.append(header)
    lines.append(f"Level: {level_label}")
    lines.append(f"Event: {context.event_type} ({context.direction.value})")
    lines.append("")

    # Source block
    lines.append("📡 Source:")
    source_url_str = context.source_url if context.source_url else "N/A"
    lines.append(f"  Source: {context.source_name} | {context.fetch_path}")
    lines.append(f"  URL: {source_url_str}")

    if context.published_at:
        pub = context.published_at.strftime("%Y-%m-%d %H:%M UTC")
        lines.append(f"  Published: {pub}")
    if context.detected_at:
        det = context.detected_at.strftime("%Y-%m-%d %H:%M UTC")
        lines.append(f"  Detected: {det}")
    latency = context.get_latency_info()
    lines.append(f"  Latency: {latency.latency_label}")
    lines.append("")

    # Score block
    lines.append("📊 Catalyst Score:")
    lines.append(
        f"  Score: {context.decision.catalyst_score}/5 "
        f"(Confidence: {context.classifier_confidence:.0%})"
    )
    for reason in context.decision.reasons:
        lines.append(f"  • {reason}")
    for mod in context.decision.modifiers:
        lines.append(f"  • {mod}")
    lines.append("")

    # Rationale
    if context.rationale:
        lines.append("📝 Rationale:")
        lines.append(f"  {context.rationale}")
        lines.append("")

    # Evidence
    if context.evidence:
        lines.append("📋 Evidence:")
        for snippet in context.evidence:
            lines.append(f"  \"{snippet}\"")
        lines.append("")

    # Risks
    if context.risks:
        lines.append("⚠️ Risks:")
        for risk in context.risks:
            lines.append(f"  • {risk}")
        lines.append("")

    # Market context
    if context.market_snapshot:
        lines.append("📈 Market Context (IEX partial-market data):")
        ms = context.market_snapshot
        price_str = f"${ms.price:.2f}" if ms.price is not None else "N/A"
        prev_close_str = f"${ms.previous_close:.2f}" if ms.previous_close is not None else "N/A"
        move_str = f"{ms.intraday_move_pct:+.2f}%" if ms.intraday_move_pct is not None else "N/A"
        lines.append(f"  Price: {price_str} | Prev Close: {prev_close_str}")
        lines.append(f"  Intraday: {move_str} | Market: {ms.market_status.value}")
        if ms.volume is not None:
            lines.append(f"  Volume: {ms.volume:,}")
        if ms.quality_flags:
            lines.append(f"  Quality flags: {', '.join(ms.quality_flags)}")
        lines.append("")

    # Action framing
    lines.append("💡 Action:")
    action_text = _build_action_text(context)
    lines.append(f"  {action_text}")
    lines.append("")

    # Bearish history
    if context.direction == Direction.BEARISH and context.prior_bullish_signals:
        lines.append("📈 Prior bullish signals:")
        for sig in context.prior_bullish_signals[:5]:  # show first 5 inline
            date_str = sig.source_date.strftime("%Y-%m-%d")
            lines.append(
                f"  • {date_str} | {sig.event_type} | {sig.alert_level} | "
                f"{sig.rationale}"
            )
        total = len(context.prior_bullish_signals)
        if total > 5:
            remaining = total - 5
            lines.append(f"  ... and {remaining} more (see continuation)")

    return "\n".join(lines)


def _build_action_text(context: AlertRenderContext) -> str:
    """Build the action framing text based on alert level and direction."""
    level = context.level
    direction = context.direction

    if level == AlertLevel.TRADEABLE and direction == Direction.BULLISH:
        return "Strong bullish catalyst. All gates pass. Consider a position."
    elif level == AlertLevel.TRADEABLE and direction == Direction.BEARISH:
        return "Strong bearish catalyst. All gates pass. Consider reducing or hedging."
    elif level == AlertLevel.AVOID_CHASE:
        return "Strong catalyst but price has already moved significantly. Avoid chasing."
    elif level == AlertLevel.REVIEW and direction == Direction.BULLISH:
        return "Moderate or uncertain bullish signal. Review before acting."
    elif level == AlertLevel.REVIEW and direction == Direction.BEARISH:
        return "Moderate or uncertain bearish signal. Review before acting."
    elif level == AlertLevel.REVIEW:
        return "Uncertain signal. Manual review required."
    else:
        return "Signal recorded. No immediate action recommended."


# ── Buttons ─────────────────────────────────────────────────────────────────


def _build_buttons(context: AlertRenderContext) -> list[list[dict]]:
    """Build the inline keyboard for the alert."""
    source_url = context.source_url if context.source_url else None

    if context.direction in (Direction.BULLISH, Direction.UNCLEAR):
        return build_bullish_keyboard(context.alert_id, source_url=source_url)
    else:
        return build_bearish_keyboard(context.alert_id, source_url=source_url)


# ── Continuation messages ──────────────────────────────────────────────────


def _render_continuation(context: AlertRenderContext) -> list[str]:
    """Build continuation messages for bearish alert prior histories."""
    if (
        context.direction != Direction.BEARISH
        or not context.prior_bullish_signals
    ):
        return []

    lines: list[str] = [
        f"📈 Continuation — Prior bullish signals for "
        f"{context.company_name} ({context.ticker}):"
    ]
    for sig in context.prior_bullish_signals:
        date_str = sig.source_date.strftime("%Y-%m-%d")
        lines.append(
            f"  • {date_str} | {sig.event_type} | {sig.alert_level} | "
            f"{sig.rationale}"
        )

    full_text = "\n".join(lines)

    # Split into continuation messages respecting the 4096 limit
    return build_continuation_messages(full_text, max_len=_TELEGRAM_MAX_LEN)


def _split_oversized_body(text: str) -> tuple[str, str]:
    """Split an oversized alert body into main part and extra detail.

    The main part keeps the header, source, score, and rationale,
    then truncates with a note.
    """
    # Find a good split point — after the rationale block
    parts = text.split("\n\n", 3)
    if len(parts) <= 3:
        # Can't meaningfully split, just truncate
        return text[:_TELEGRAM_MAX_LEN], ""

    main = "\n\n".join(parts[:3])
    extra = "\n\n".join(parts[3:])

    # If main is already over limit, hard truncate
    if len(main) > _TELEGRAM_MAX_LEN:
        main = main[:_TELEGRAM_MAX_LEN - 100] + "\n\n... (content truncated)"

    return main, extra