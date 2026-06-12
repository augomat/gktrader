"""Tests for paper entry rules and notional determination."""

from __future__ import annotations

from datetime import UTC, datetime

from gktrader.domain.contracts import MarketSnapshotContract
from gktrader.domain.enums import AlertLevel, Direction, MarketStatus
from gktrader.reporting.paper import (
    IEX_PARTIAL_LABEL,
    PaperEntry,
    get_paper_notional,
    make_paper_entry,
)


def _snapshot(price: float = 150.0) -> MarketSnapshotContract:
    return MarketSnapshotContract(
        ticker="AAPL",
        provider="alpaca",
        feed="IEX",
        observed_at=datetime.now(UTC),
        request_time=datetime.now(UTC),
        price=price,
        previous_close=148.0,
        intraday_move_pct=1.35,
        market_status=MarketStatus.OPEN,
        volume=10_000_000,
        quality_flags=[],
        label=IEX_PARTIAL_LABEL,
    )


class TestGetPaperNotional:
    """Paper notionals per alert level."""

    def test_watch_notional(self) -> None:
        assert get_paper_notional(AlertLevel.WATCH) == 0.0

    def test_review_notional(self) -> None:
        assert get_paper_notional(AlertLevel.REVIEW) == 500.0

    def test_tradeable_notional(self) -> None:
        assert get_paper_notional(AlertLevel.TRADEABLE) == 1000.0

    def test_avoid_chase_notional(self) -> None:
        assert get_paper_notional(AlertLevel.AVOID_CHASE) == 0.0

    def test_ignore_notional(self) -> None:
        assert get_paper_notional(AlertLevel.IGNORE) == 0.0

    def test_unknown_level_returns_zero(self) -> None:
        assert get_paper_notional("UNKNOWN") == 0.0  # type: ignore[arg-type]


class TestMakePaperEntry:
    """Paper entry construction."""

    def test_tradeable_entry_with_snapshot(self) -> None:
        entry = make_paper_entry(
            ticker="AAPL",
            direction=Direction.BULLISH,
            alert_level=AlertLevel.TRADEABLE,
            snapshot=_snapshot(price=150.0),
        )
        assert isinstance(entry, PaperEntry)
        assert entry.ticker == "AAPL"
        assert entry.direction == Direction.BULLISH
        assert entry.alert_level == AlertLevel.TRADEABLE
        assert entry.notional_eur == 1000.0
        assert entry.entry_price == 150.0
        assert entry.provider == "alpaca"
        assert entry.feed == "IEX"
        assert entry.quality_flags == []

    def test_review_entry_notional(self) -> None:
        entry = make_paper_entry(
            ticker="RGTI",
            direction=Direction.BULLISH,
            alert_level=AlertLevel.REVIEW,
            snapshot=_snapshot(price=4.25),
        )
        assert entry.notional_eur == 500.0

    def test_watch_entry_notional_zero(self) -> None:
        entry = make_paper_entry(
            ticker="RGTI",
            direction=Direction.BULLISH,
            alert_level=AlertLevel.WATCH,
            snapshot=_snapshot(price=4.25),
        )
        assert entry.notional_eur == 0.0

    def test_avoid_chase_entry_notional_zero(self) -> None:
        entry = make_paper_entry(
            ticker="RGTI",
            direction=Direction.BULLISH,
            alert_level=AlertLevel.AVOID_CHASE,
            snapshot=_snapshot(price=4.25),
        )
        assert entry.notional_eur == 0.0

    def test_ignore_entry_notional_zero(self) -> None:
        entry = make_paper_entry(
            ticker="RGTI",
            direction=Direction.BULLISH,
            alert_level=AlertLevel.IGNORE,
            snapshot=_snapshot(price=4.25),
        )
        assert entry.notional_eur == 0.0

    def test_bearish_entry_uses_inverse_direction(self) -> None:
        """Bearish paper analysis is inverse/short for analysis only."""
        entry = make_paper_entry(
            ticker="AAPL",
            direction=Direction.BEARISH,
            alert_level=AlertLevel.TRADEABLE,
            snapshot=_snapshot(price=150.0),
        )
        assert entry.direction == Direction.BEARISH
        assert entry.notional_eur == 1000.0

    def test_no_snapshot_entry(self) -> None:
        entry = make_paper_entry(
            ticker="AAPL",
            direction=Direction.BULLISH,
            alert_level=AlertLevel.TRADEABLE,
            snapshot=None,
        )
        assert entry.entry_price is None
        assert entry.entry_time is None
        assert entry.provider is None
        assert entry.feed is None
        assert "no_market_snapshot" in entry.quality_flags

    def test_label_is_iex_partial(self) -> None:
        entry = make_paper_entry(
            ticker="AAPL",
            direction=Direction.BULLISH,
            alert_level=AlertLevel.TRADEABLE,
            snapshot=_snapshot(),
        )
        assert IEX_PARTIAL_LABEL in entry.label