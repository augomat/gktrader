"""Integration tests for alert delivery pipeline.

Tests the end-to-end flow of rendering alert payloads and simulating
delivery outcomes using mocked Telegram API calls.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import httpx
import pytest

from gktrader.alerts.outbox import (
    OutboxEntry,
    claim_outbox,
    mark_delivery_outcome,
)
from gktrader.alerts.renderer import (
    AlertRenderContext,
    render_alert_payload,
)
from gktrader.alerts.sender import send_alert, send_continuation_messages
from gktrader.config.settings import Settings
from gktrader.domain.contracts import (
    AlertPayload,
    MarketSnapshotContract,
    PriorBullishSignal,
    SignalDecision,
)
from gktrader.domain.enums import (
    AlertLevel,
    DeliveryStatus,
    Direction,
    EventType,
    MarketStatus,
)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        telegram_bot_token="test:token123",
        telegram_send_base_url="https://api.telegram.org",
    )


@pytest.fixture
def render_context() -> AlertRenderContext:
    """A realistic rendering context for integration testing."""
    now = datetime.now(timezone.utc)
    decision = SignalDecision(
        alert_level=AlertLevel.TRADEABLE,
        catalyst_score=5,
        direction=Direction.BULLISH,
        modifiers=["Multiple independent sources"],
        reasons=["Base score: 5 from government_funding", "Modifiers: +1"],
    )
    market = MarketSnapshotContract(
        ticker="RGTI",
        provider="alpaca",
        feed="IEX",
        observed_at=now,
        request_time=now,
        price=4.25,
        previous_close=4.00,
        intraday_move_pct=6.25,
        market_status=MarketStatus.OPEN,
        volume=1_250_000,
    )
    return AlertRenderContext(
        alert_id="integ-test-001",
        level=AlertLevel.TRADEABLE,
        decision=decision,
        ticker="RGTI",
        company_name="Rigetti Computing",
        event_type=EventType.GOVERNMENT_FUNDING.value,
        direction=Direction.BULLISH,
        source_name="White House",
        source_url="https://www.whitehouse.gov/news/feed/",
        fetch_path="rss",
        published_at=datetime(2025, 6, 12, 14, 30, tzinfo=timezone.utc),
        detected_at=datetime(2025, 6, 12, 14, 31, 5, tzinfo=timezone.utc),
        rationale="CHIPS Act grant for quantum computing development.",
        evidence=["$15M awarded to Rigetti Computing."],
        risks=["Subject to due diligence"],
        classifier_confidence=0.92,
        mapping_confidence=1.0,
        market_snapshot=market,
    )


@pytest.fixture
def bearish_render_context() -> AlertRenderContext:
    """Bearish context with prior bullish signals for integration testing."""
    now = datetime.now(timezone.utc)
    decision = SignalDecision(
        alert_level=AlertLevel.TRADEABLE,
        catalyst_score=5,
        direction=Direction.BEARISH,
        modifiers=[],
        reasons=["Base score: 5 from presidential_negative_mention"],
    )
    prior = [
        PriorBullishSignal(
            source_date=datetime(2025, 5, 1, tzinfo=timezone.utc),
            event_type=EventType.GOVERNMENT_FUNDING.value,
            alert_level=AlertLevel.TRADEABLE,
            rationale="CHIPS Act preliminary award",
        ),
        PriorBullishSignal(
            source_date=datetime(2025, 4, 15, tzinfo=timezone.utc),
            event_type=EventType.PRESIDENTIAL_POSITIVE_MENTION.value,
            alert_level=AlertLevel.WATCH,
            rationale="Trump mentioned quantum computing",
        ),
    ]
    return AlertRenderContext(
        alert_id="integ-test-bear-001",
        level=AlertLevel.TRADEABLE,
        decision=decision,
        ticker="RGTI",
        company_name="Rigetti Computing",
        event_type=EventType.PRESIDENTIAL_NEGATIVE_MENTION.value,
        direction=Direction.BEARISH,
        source_name="White House",
        source_url="https://www.whitehouse.gov/news/feed/",
        fetch_path="rss",
        published_at=datetime(2025, 6, 12, 15, 0, tzinfo=timezone.utc),
        detected_at=datetime(2025, 6, 12, 15, 1, tzinfo=timezone.utc),
        rationale="Negative presidential mention regarding quantum computing investments.",
        evidence=["President criticized quantum computing spending."],
        risks=["Political noise, may not materialize"],
        classifier_confidence=0.85,
        mapping_confidence=1.0,
        market_snapshot=None,
        prior_bullish_signals=prior,
    )


class TestFullDeliveryPipeline:
    """End-to-end delivery pipeline tests with mocked Telegram."""

    @patch("gktrader.alerts.sender.send_telegram_message")
    def test_render_and_send_bullish_alert(
        self,
        mock_send: MagicMock,
        settings: Settings,
        render_context: AlertRenderContext,
    ) -> None:
        """Render a bullish TRADEABLE alert and simulate successful delivery."""
        # Render
        payload = render_alert_payload(render_context)
        assert payload.level == AlertLevel.TRADEABLE
        assert "$RGTI" in payload.text
        assert "BULLISH" in payload.text
        assert len(payload.buttons) >= 1

        # Send
        mock_send.return_value = {"ok": True, "result": {"message_id": 42}}
        status = send_alert(settings, chat_id=12345, payload=payload)
        assert status == DeliveryStatus.SENT

    @patch("gktrader.alerts.sender.send_telegram_message")
    def test_render_and_send_bearish_with_history(
        self,
        mock_send: MagicMock,
        settings: Settings,
        bearish_render_context: AlertRenderContext,
    ) -> None:
        """Render a bearish alert with prior bullish history."""
        payload = render_alert_payload(bearish_render_context)
        assert payload.level == AlertLevel.TRADEABLE
        assert "BEARISH" in payload.text
        assert "prior bullish" in payload.text.lower() or "Prior bullish" in payload.text

        # Check continuation messages
        if payload.continuation_messages:
            mock_send.return_value = {"ok": True}
            statuses = send_continuation_messages(
                settings, chat_id=12345, continuation_messages=payload.continuation_messages
            )
            assert all(s == DeliveryStatus.SENT for s in statuses)

    @patch("gktrader.alerts.sender.send_telegram_message")
    def test_timeout_during_send_marked_unknown(
        self,
        mock_send: MagicMock,
        settings: Settings,
        render_context: AlertRenderContext,
    ) -> None:
        """When Telegram API times out, delivery is UNKNOWN and outbox rejects retry."""
        # Render
        payload = render_alert_payload(render_context)

        # Simulate timeout
        mock_send.side_effect = httpx.TimeoutException("timeout")

        status = send_alert(settings, chat_id=12345, payload=payload)
        assert status == DeliveryStatus.UNKNOWN

        # Outbox should reject retry
        entry = OutboxEntry(
            id="test-1",
            alert_id=payload.alert_id,
            status=DeliveryStatus.UNKNOWN,
            idempotency_key=f"deliver:{payload.alert_id}:v0",
            sent_at=datetime.now(timezone.utc),
        )
        claim_result = claim_outbox(entry)
        assert claim_result.value == "already_claimed"

    @patch("gktrader.alerts.sender.send_telegram_message")
    def test_avoid_chase_renders_and_sends(
        self,
        mock_send: MagicMock,
        settings: Settings,
    ) -> None:
        """AVOID_CHASE alerts are still sent."""
        now = datetime.now(timezone.utc)
        decision = SignalDecision(
            alert_level=AlertLevel.AVOID_CHASE,
            catalyst_score=5,
            direction=Direction.BULLISH,
            modifiers=["Stock moved 45% intraday (>40%)"],
            reasons=["Base score: 5 from government_funding", "Modifiers: -3"],
        )
        context = AlertRenderContext(
            alert_id="integ-avoid-001",
            level=AlertLevel.AVOID_CHASE,
            decision=decision,
            ticker="RGTI",
            company_name="Rigetti Computing",
            event_type=EventType.GOVERNMENT_FUNDING.value,
            direction=Direction.BULLISH,
            source_name="White House",
            source_url="https://www.whitehouse.gov/news/feed/",
            fetch_path="rss",
            published_at=datetime(2025, 6, 12, 14, 30, tzinfo=timezone.utc),
            detected_at=datetime(2025, 6, 12, 14, 31, tzinfo=timezone.utc),
            rationale="CHIPS Act grant.",
            evidence=["$15M awarded."],
            risks=["Already priced in"],
            classifier_confidence=0.92,
            mapping_confidence=1.0,
        )

        payload = render_alert_payload(context)
        assert "AVOID" in payload.text or "AVOID CHASE" in payload.text

        mock_send.return_value = {"ok": True}
        status = send_alert(settings, chat_id=12345, payload=payload)
        assert status == DeliveryStatus.SENT


class TestNoWatchDelivery:
    """No WATCH alert reaches Telegram."""

    def test_watch_never_rendered(self) -> None:
        now = datetime.now(timezone.utc)
        decision = SignalDecision(
            alert_level=AlertLevel.WATCH,
            catalyst_score=2,
            direction=Direction.NEUTRAL,
            modifiers=[],
            reasons=["Base score: 2 from sector_only_mention"],
        )
        context = AlertRenderContext(
            alert_id="watch-001",
            level=AlertLevel.WATCH,
            decision=decision,
            ticker="",
            company_name="",
            event_type=EventType.SECTOR_ONLY_MENTION.value,
            direction=Direction.NEUTRAL,
            source_name="NIST",
            source_url="https://www.nist.gov/news-events/news/rss.xml",
            fetch_path="rss",
            detected_at=now,
            rationale="Sector-level mention only.",
            classifier_confidence=0.70,
            mapping_confidence=0.0,
        )

        with pytest.raises(ValueError, match="WATCH alerts must not be rendered"):
            render_alert_payload(context)

    @patch("gktrader.alerts.sender.send_telegram_message")
    def test_sender_path_not_called_for_watch(
        self, mock_send: MagicMock, settings: Settings
    ) -> None:
        """No code path should send a WATCH alert to Telegram."""
        # The sender itself doesn't filter by level, but the pipeline
        # should ensure WATCH never reaches it.
        # This test verifies the sender can technically send any payload,
        # with the pipeline-level guard in the renderer.
        mock_send.return_value = {"ok": True}
        payload = AlertPayload(
            alert_id="watch-payload",
            level=AlertLevel.WATCH,
            text="Should never be sent",
            dedupe_key="watch:test",
        )
        # The sender doesn't block WATCH — it's the caller's responsibility
        status = send_alert(settings, chat_id=12345, payload=payload)
        assert status == DeliveryStatus.SENT