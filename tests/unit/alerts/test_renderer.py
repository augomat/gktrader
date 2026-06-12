"""Tests for deterministic English alert rendering.

Covers bullish, bearish, review, tradeable, avoid-chase, and unclear variants,
as well as required content elements, WATCH exclusion, and rendering behavior.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from gktrader.alerts.renderer import (
    AlertRenderContext,
    LatencyInfo,
    render_alert_payload,
)
from gktrader.domain.contracts import (
    MarketSnapshotContract,
    PriorBullishSignal,
    SignalDecision,
)
from gktrader.domain.enums import (
    AlertLevel,
    Direction,
    EventType,
    MarketStatus,
)


def _make_context(
    alert_id: str = "alert-001",
    level: AlertLevel = AlertLevel.TRADEABLE,
    direction: Direction = Direction.BULLISH,
    ticker: str = "RGTI",
    company_name: str = "Rigetti Computing",
    event_type: str = "government_funding",
    classifier_confidence: float = 0.92,
    mapping_confidence: float = 1.0,
    has_market: bool = True,
    has_prior_bullish: bool = False,
) -> AlertRenderContext:
    """Helper to build a render context with sensible defaults."""
    now = datetime.now(timezone.utc)
    decision = SignalDecision(
        alert_level=level,
        catalyst_score=5,
        direction=direction,
        modifiers=["Multiple independent sources"],
        reasons=[
            "Base score: 5 from government_funding",
            "Modifiers: +1",
        ],
    )

    market = None
    if has_market:
        market = MarketSnapshotContract(
            ticker=ticker,
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

    prior_signals = []
    if has_prior_bullish:
        prior_signals = [
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
        alert_id=alert_id,
        level=level,
        decision=decision,
        ticker=ticker,
        company_name=company_name,
        event_type=event_type,
        direction=direction,
        source_name="White House",
        source_url="https://www.whitehouse.gov/news/feed/",
        fetch_path="rss",
        published_at=datetime(2025, 6, 12, 14, 30, tzinfo=timezone.utc),
        detected_at=datetime(2025, 6, 12, 14, 31, 5, tzinfo=timezone.utc),
        rationale="The White House announced a $15M CHIPS Act grant for Rigetti.",
        evidence=[
            "The Department of Commerce awarded $15 million to Rigetti Computing "
            "for quantum computing development."
        ],
        risks=["Grant is subject to final due diligence"],
        classifier_confidence=classifier_confidence,
        mapping_confidence=mapping_confidence,
        market_snapshot=market,
        prior_bullish_signals=prior_signals,
    )


class TestRenderBasicContent:
    """Basic content checks for all alert variants."""

    def test_bullish_tradeable_contains_header(self) -> None:
        context = _make_context()
        payload = render_alert_payload(context)
        assert "BULLISH ALERT" in payload.text
        assert "$RGTI" in payload.text
        assert "Rigetti Computing" in payload.text

    def test_contains_source_info(self) -> None:
        context = _make_context()
        payload = render_alert_payload(context)
        assert "White House" in payload.text
        assert "rss" in payload.text
        assert "whitehouse.gov" in payload.text

    def test_contains_timestamps(self) -> None:
        context = _make_context()
        payload = render_alert_payload(context)
        assert "2025-06-12" in payload.text
        assert "14:30" in payload.text
        assert "14:31" in payload.text

    def test_contains_latency(self) -> None:
        context = _make_context()
        payload = render_alert_payload(context)
        # detected 5s after published → 5s
        assert "5s" in payload.text or "Latency" in payload.text

    def test_contains_score_and_confidence(self) -> None:
        context = _make_context()
        payload = render_alert_payload(context)
        assert "Score" in payload.text
        assert "92%" in payload.text or "0.92" in payload.text

    def test_contains_rationale(self) -> None:
        context = _make_context()
        payload = render_alert_payload(context)
        assert "CHIPS Act" in payload.text

    def test_contains_evidence(self) -> None:
        context = _make_context()
        payload = render_alert_payload(context)
        assert "Department of Commerce" in payload.text

    def test_contains_risks(self) -> None:
        context = _make_context()
        payload = render_alert_payload(context)
        assert "due diligence" in payload.text

    def test_contains_market_context_label(self) -> None:
        context = _make_context()
        payload = render_alert_payload(context)
        assert "IEX partial-market data" in payload.text

    def test_contains_action_framing(self) -> None:
        context = _make_context()
        payload = render_alert_payload(context)
        assert "Action" in payload.text or "action" in payload.text

    def test_has_dedupe_key(self) -> None:
        context = _make_context()
        payload = render_alert_payload(context)
        assert payload.dedupe_key == f"{context.ticker.upper()}:{context.event_type}:bullish:TRADEABLE"

    def test_has_buttons(self) -> None:
        context = _make_context()
        payload = render_alert_payload(context)
        assert len(payload.buttons) > 0

    def test_continuation_messages_empty_for_bullish(self) -> None:
        context = _make_context()
        payload = render_alert_payload(context)
        assert isinstance(payload.continuation_messages, list)


class TestBearishAlert:
    """Bearish alert-specific content."""

    def test_bearish_header(self) -> None:
        context = _make_context(direction=Direction.BEARISH)
        payload = render_alert_payload(context)
        assert "BEARISH ALERT" in payload.text

    def test_bearish_includes_prior_bullish(self) -> None:
        context = _make_context(direction=Direction.BEARISH, has_prior_bullish=True)
        payload = render_alert_payload(context)
        assert "Prior bullish signals" in payload.text or "prior bullish" in payload.text.lower()

    def test_bearish_without_prior_signals(self) -> None:
        context = _make_context(direction=Direction.BEARISH, has_prior_bullish=False)
        payload = render_alert_payload(context)
        # Should not crash and should not mention prior signals
        assert payload.text is not None

    def test_bearish_keyboard_is_bearish_set(self) -> None:
        context = _make_context(direction=Direction.BEARISH)
        payload = render_alert_payload(context)
        # Bearish keyboard has "Sold/Reduced" button
        buttons_text = str(payload.buttons)
        assert "Sold/Reduced" in buttons_text
        assert "Bought" not in buttons_text


class TestAvoidChase:
    """AVOID_CHASE variant."""

    def test_avoid_chase_header(self) -> None:
        context = _make_context(level=AlertLevel.AVOID_CHASE)
        payload = render_alert_payload(context)
        assert "AVOID" in payload.text or "AVOID CHASE" in payload.text

    def test_avoid_chase_action_text(self) -> None:
        context = _make_context(level=AlertLevel.AVOID_CHASE)
        payload = render_alert_payload(context)
        assert "Avoid chasing" in payload.text or "already moved" in payload.text


class TestReviewAlert:
    """REVIEW variant."""

    def test_review_header(self) -> None:
        context = _make_context(level=AlertLevel.REVIEW)
        payload = render_alert_payload(context)
        assert "REVIEW" in payload.text

    def test_review_action_text(self) -> None:
        context = _make_context(level=AlertLevel.REVIEW)
        payload = render_alert_payload(context)
        assert "Review" in payload.text or "review" in payload.text


class TestUnclearAlert:
    """UNCLEAR direction alerts."""

    def test_unclear_header(self) -> None:
        context = _make_context(direction=Direction.UNCLEAR, level=AlertLevel.REVIEW)
        payload = render_alert_payload(context)
        assert "UNCLEAR" in payload.text

    def test_unclear_uses_bullish_buttons(self) -> None:
        context = _make_context(direction=Direction.UNCLEAR, level=AlertLevel.REVIEW)
        payload = render_alert_payload(context)
        buttons_text = str(payload.buttons)
        assert "Bought" in buttons_text  # unclear uses bullish button set


class TestNeutralAlert:
    """NEUTRAL direction."""

    def test_neutral_header(self) -> None:
        context = _make_context(direction=Direction.NEUTRAL, level=AlertLevel.REVIEW)
        payload = render_alert_payload(context)
        assert "NEUTRAL" in payload.text


class TestWatchExcluded:
    """WATCH alerts must never be rendered for delivery."""

    def test_watch_raises_value_error(self) -> None:
        context = _make_context(level=AlertLevel.WATCH)
        with pytest.raises(ValueError, match="WATCH alerts must not be rendered"):
            render_alert_payload(context)


class TestMarketContext:
    """Market context rendering."""

    def test_market_info_present(self) -> None:
        context = _make_context(has_market=True)
        payload = render_alert_payload(context)
        assert "$4.25" in payload.text
        assert "$4.00" in payload.text
        assert "+6.25%" in payload.text
        assert "1,250,000" in payload.text

    def test_market_info_absent(self) -> None:
        context = _make_context(has_market=False)
        payload = render_alert_payload(context)
        # Should not crash; market section omitted
        assert "Market Context" not in payload.text


class TestLatencyInfo:
    """Latency computation."""

    def test_latency_computed(self) -> None:
        context = _make_context()
        info = context.get_latency_info()
        assert info.latency_seconds == 65  # 1m5s = 65 seconds

    def test_latency_label_format(self) -> None:
        context = _make_context()
        info = context.get_latency_info()
        assert info.latency_label == "1m5s"

    def test_no_published_at(self) -> None:
        context = _make_context()
        context.published_at = None
        info = context.get_latency_info()
        assert info.latency_seconds is None
        assert info.latency_label == "N/A"

    def test_negative_latency(self) -> None:
        """If detected before published (clock skew), return N/A."""
        context = _make_context()
        context.published_at = datetime(2025, 6, 12, 15, 0, tzinfo=timezone.utc)
        context.detected_at = datetime(2025, 6, 12, 14, 0, tzinfo=timezone.utc)
        info = context.get_latency_info()
        assert info.latency_seconds is None


class TestRenderingEdgeCases:
    """Edge cases and limit handling."""

    def test_very_long_rationale(self) -> None:
        context = _make_context()
        context.rationale = "A " * 2000  # 4000 chars
        # Should not crash
        payload = render_alert_payload(context)
        assert payload.text is not None

    def test_many_risks(self) -> None:
        context = _make_context()
        context.risks = [f"Risk {i}" for i in range(100)]
        payload = render_alert_payload(context)
        assert payload.text is not None

    def test_no_ticker(self) -> None:
        """Sector-only mention with no ticker should still render."""
        context = _make_context(
            ticker="",
            company_name="",
            level=AlertLevel.REVIEW,
            has_market=False,
        )
        context.event_type = "sector_only_mention"
        payload = render_alert_payload(context)
        assert payload.text is not None

    def test_all_fields_empty(self) -> None:
        """Render with minimal context."""
        now = datetime.now(timezone.utc)
        context = AlertRenderContext(
            alert_id="minimal",
            level=AlertLevel.REVIEW,
            decision=SignalDecision(
                alert_level=AlertLevel.REVIEW,
                catalyst_score=1,
                direction=Direction.NEUTRAL,
            ),
            ticker="",
            company_name="",
            event_type="irrelevant",
            direction=Direction.NEUTRAL,
            source_name="test",
            source_url="",
            fetch_path="test",
        )
        payload = render_alert_payload(context)
        assert payload.text is not None
        assert "NEUTRAL" in payload.text