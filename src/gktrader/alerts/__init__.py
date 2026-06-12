"""Alert rendering, keyboard construction, outbox sender, and Telegram delivery."""

from gktrader.alerts.continuation import build_continuation_messages, split_message
from gktrader.alerts.health import (
    HealthTransition,
    build_degradation_notification,
    build_recovery_notification,
)
from gktrader.alerts.keyboard import (
    MAX_CALLBACK_BYTES,
    build_bearish_keyboard,
    build_bullish_keyboard,
    derive_short_id,
    validate_callback_data,
)
from gktrader.alerts.outbox import OutboxClaimResult, claim_outbox, mark_delivery_outcome
from gktrader.alerts.renderer import (
    AlertRenderContext,
    LatencyInfo,
    render_alert_payload,
)
from gktrader.alerts.sender import send_alert, send_continuation_messages

__all__ = [
    "AlertRenderContext",
    "LatencyInfo",
    "MAX_CALLBACK_BYTES",
    "OutboxClaimResult",
    "build_bearish_keyboard",
    "build_bullish_keyboard",
    "build_continuation_messages",
    "build_degradation_notification",
    "build_recovery_notification",
    "claim_outbox",
    "derive_short_id",
    "mark_delivery_outcome",
    "render_alert_payload",
    "send_alert",
    "send_continuation_messages",
    "split_message",
    "validate_callback_data",
]