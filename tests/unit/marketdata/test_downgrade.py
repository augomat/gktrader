"""Tests for market-data actionability downgrade helpers.

Market data may only downgrade, never promote.
All user-facing output must be labeled ``IEX partial-market data``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from gktrader.domain.contracts import MarketSnapshotContract
from gktrader.domain.enums import AlertLevel, MarketStatus
from gktrader.marketdata.downgrade import (
    IEX_PARTIAL_LABEL,
    DowngradeResult,
    apply_market_downgrade,
)


def _snapshot(
    price: float | None = 150.0,
    prev_close: float | None = 148.0,
    intraday_move_pct: float | None = 2.0,
    market_status: MarketStatus = MarketStatus.OPEN,
) -> MarketSnapshotContract:
    return MarketSnapshotContract(
        ticker="AAPL",
        provider="alpaca",
        feed="IEX",
        observed_at=datetime.now(UTC),
        request_time=datetime.now(UTC),
        price=price,
        previous_close=prev_close,
        intraday_move_pct=intraday_move_pct,
        market_status=market_status,
        volume=10_000_000,
        quality_flags=[],
        label=IEX_PARTIAL_LABEL,
    )


class TestApplyMarketDowngrade:
    """Core downgrade logic."""

    def test_label_is_iex_partial(self) -> None:
        """Every downgrade result must carry the IEX partial-market label."""
        result = apply_market_downgrade(AlertLevel.TRADEABLE, _snapshot(intraday_move_pct=5.0))
        assert IEX_PARTIAL_LABEL in result.label

    def test_no_snapshot_downgrades_tradeable_to_review(self) -> None:
        """Missing market data forces TRADEABLE to REVIEW."""
        result = apply_market_downgrade(AlertLevel.TRADEABLE, None)
        assert result.downgraded_level == AlertLevel.REVIEW
        assert "Missing market data" in result.reasons[0]

    def test_no_snapshot_preserves_review(self) -> None:
        """Missing market data does not promote REVIEW."""
        result = apply_market_downgrade(AlertLevel.REVIEW, None)
        assert result.downgraded_level == AlertLevel.REVIEW

    def test_no_snapshot_preserves_watch(self) -> None:
        result = apply_market_downgrade(AlertLevel.WATCH, None)
        assert result.downgraded_level == AlertLevel.WATCH

    def test_no_snapshot_preserves_ignore(self) -> None:
        result = apply_market_downgrade(AlertLevel.IGNORE, None)
        assert result.downgraded_level == AlertLevel.IGNORE

    def test_no_snapshot_preserves_avoid_chase(self) -> None:
        result = apply_market_downgrade(AlertLevel.AVOID_CHASE, None)
        assert result.downgraded_level == AlertLevel.AVOID_CHASE

    def test_below_10_pct_retains_tradeable(self) -> None:
        """Below +10%: retain TRADEABLE if otherwise eligible."""
        result = apply_market_downgrade(AlertLevel.TRADEABLE, _snapshot(intraday_move_pct=5.0))
        assert result.downgraded_level == AlertLevel.TRADEABLE

    def test_exactly_10_pct_downgrades_to_review(self) -> None:
        """At exactly +10%: downgrade to REVIEW."""
        result = apply_market_downgrade(AlertLevel.TRADEABLE, _snapshot(intraday_move_pct=10.0))
        assert result.downgraded_level == AlertLevel.REVIEW

    def test_15_pct_downgrades_to_review(self) -> None:
        """Between +10% and +25%: downgrade to REVIEW."""
        result = apply_market_downgrade(AlertLevel.TRADEABLE, _snapshot(intraday_move_pct=15.0))
        assert result.downgraded_level == AlertLevel.REVIEW

    def test_25_pct_downgrades_to_review(self) -> None:
        """At exactly +25%: downgrade to REVIEW."""
        result = apply_market_downgrade(AlertLevel.TRADEABLE, _snapshot(intraday_move_pct=25.0))
        assert result.downgraded_level == AlertLevel.REVIEW

    def test_above_25_pct_downgrades_to_avoid_chase(self) -> None:
        """Above +25%: downgrade to AVOID_CHASE."""
        result = apply_market_downgrade(AlertLevel.TRADEABLE, _snapshot(intraday_move_pct=30.0))
        assert result.downgraded_level == AlertLevel.AVOID_CHASE

    def test_50_pct_downgrades_to_avoid_chase(self) -> None:
        result = apply_market_downgrade(AlertLevel.TRADEABLE, _snapshot(intraday_move_pct=50.0))
        assert result.downgraded_level == AlertLevel.AVOID_CHASE

    def test_market_data_cannot_promote(self) -> None:
        """Market data may never promote an event."""
        for level in (AlertLevel.WATCH, AlertLevel.REVIEW, AlertLevel.IGNORE, AlertLevel.AVOID_CHASE):
            result = apply_market_downgrade(level, _snapshot(intraday_move_pct=0.0))
            assert result.downgraded_level == level, f"Market data promoted {level}"

    def test_original_level_preserved_in_result(self) -> None:
        result = apply_market_downgrade(AlertLevel.TRADEABLE, _snapshot(intraday_move_pct=30.0))
        assert result.original_level == AlertLevel.TRADEABLE

    def test_reasons_contain_market_context(self) -> None:
        result = apply_market_downgrade(AlertLevel.TRADEABLE, _snapshot(intraday_move_pct=5.0))
        assert any("IEX partial-market data" in r for r in result.reasons)

    def test_reasons_contain_intraday_move(self) -> None:
        result = apply_market_downgrade(AlertLevel.TRADEABLE, _snapshot(intraday_move_pct=5.0))
        assert any("Intraday move" in r for r in result.reasons)

    def test_no_price_no_move_downgrades_tradeable(self) -> None:
        """Snapshot with no price and no move data downgrades TRADEABLE."""
        snap = _snapshot(price=None, intraday_move_pct=None)
        result = apply_market_downgrade(AlertLevel.TRADEABLE, snap)
        assert result.downgraded_level == AlertLevel.REVIEW

    def test_no_intraday_move_downgrades_tradeable(self) -> None:
        """Snapshot with price but no intraday move downgrades TRADEABLE."""
        snap = _snapshot(price=150.0, intraday_move_pct=None)
        result = apply_market_downgrade(AlertLevel.TRADEABLE, snap)
        assert result.downgraded_level == AlertLevel.REVIEW


class TestBearishDowngrade:
    """Bearish events use absolute move symmetrically."""

    def test_negative_5_pct_retains_tradeable(self) -> None:
        """-5% move (abs < 10%) retains TRADEABLE."""
        result = apply_market_downgrade(
            AlertLevel.TRADEABLE,
            _snapshot(intraday_move_pct=-5.0),
            is_bearish=True,
        )
        assert result.downgraded_level == AlertLevel.TRADEABLE

    def test_negative_15_pct_downgrades_to_review(self) -> None:
        """-15% move (abs between 10% and 25%) downgrades to REVIEW."""
        result = apply_market_downgrade(
            AlertLevel.TRADEABLE,
            _snapshot(intraday_move_pct=-15.0),
            is_bearish=True,
        )
        assert result.downgraded_level == AlertLevel.REVIEW

    def test_negative_30_pct_downgrades_to_avoid_chase(self) -> None:
        """-30% move (abs > 25%) downgrades to AVOID_CHASE."""
        result = apply_market_downgrade(
            AlertLevel.TRADEABLE,
            _snapshot(intraday_move_pct=-30.0),
            is_bearish=True,
        )
        assert result.downgraded_level == AlertLevel.AVOID_CHASE


class TestDowngradeResultDataclass:
    """DowngradeResult structure."""

    def test_default_label(self) -> None:
        result = DowngradeResult(
            original_level=AlertLevel.TRADEABLE,
            downgraded_level=AlertLevel.REVIEW,
        )
        assert result.label == IEX_PARTIAL_LABEL

    def test_reasons_default_empty(self) -> None:
        result = DowngradeResult(
            original_level=AlertLevel.TRADEABLE,
            downgraded_level=AlertLevel.TRADEABLE,
        )
        assert result.reasons == []