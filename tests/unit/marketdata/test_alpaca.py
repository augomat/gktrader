"""Tests for the Alpaca IEX market data provider."""

from __future__ import annotations

import re
from datetime import UTC, datetime

import httpx
import pytest
import respx

from gktrader.domain.contracts import MarketSnapshotContract
from gktrader.domain.enums import MarketStatus
from gktrader.marketdata.alpaca import AlpacaIEXProvider, IEX_LABEL

_ALPACA_SNAPSHOT_URL = "https://data.alpaca.markets/v2/stocks/AAPL/snapshot?feed=iex"
_ALPACA_BARS_URL_RE = re.compile(r"https://data\.alpaca\.markets/v2/stocks/bars.*")


def _fake_snapshot_json(
    price: float = 150.0,
    prev_close: float = 148.0,
    open_price: float = 149.0,
    close_price: float = 151.0,
    volume: int = 10_000_000,
    status: str = "open",
) -> dict:
    return {
        "status": status,
        "latestTrade": {"p": price, "s": 100, "t": "2026-06-12T14:30:00Z"},
        "prevDailyBar": {"c": prev_close, "h": prev_close + 2, "l": prev_close - 2, "o": prev_close - 1, "v": 8_000_000},
        "dailyBar": {"c": close_price, "h": close_price + 2, "l": open_price - 1, "o": open_price, "v": volume},
    }


def _fake_bars_json() -> dict:
    return {
        "bars": {
            "AAPL": [
                {
                    "t": "2026-06-10T13:00:00Z",
                    "o": 198.5,
                    "h": 200.0,
                    "l": 197.9,
                    "c": 199.8,
                    "v": 123_456,
                    "n": 789,
                    "vw": 199.3,
                },
                {
                    "t": "2026-06-11T13:00:00Z",
                    "o": 199.8,
                    "h": 201.2,
                    "l": 199.0,
                    "c": 200.4,
                    "v": 234_567,
                    "n": 890,
                    "vw": 200.1,
                },
            ]
        }
    }


class TestAlpacaIEXProvider:
    """Alpaca IEX provider construction and snapshot parsing."""

    def test_provider_name(self) -> None:
        provider = AlpacaIEXProvider(api_key="k", api_secret="s", http_client=httpx.Client())
        assert provider.provider_name == "alpaca"

    def test_snapshot_success(self) -> None:
        with respx.mock:
            route = respx.get(_ALPACA_SNAPSHOT_URL).mock(
                return_value=httpx.Response(200, json=_fake_snapshot_json())
            )
            provider = AlpacaIEXProvider(api_key="k", api_secret="s", http_client=httpx.Client())
            snap = provider.snapshot("AAPL")

        assert route.called
        assert isinstance(snap, MarketSnapshotContract)
        assert snap.ticker == "AAPL"
        assert snap.provider == "alpaca"
        assert snap.feed == "IEX"
        assert snap.price == 150.0
        assert snap.previous_close == 148.0
        assert snap.intraday_move_pct is not None
        assert snap.market_status == MarketStatus.OPEN
        assert snap.volume == 10_000_000
        assert snap.label == IEX_LABEL
        assert snap.quality_flags == []

    def test_snapshot_label_is_iex_partial(self) -> None:
        """Every snapshot must carry the IEX partial-market label."""
        with respx.mock:
            respx.get(_ALPACA_SNAPSHOT_URL).mock(
                return_value=httpx.Response(200, json=_fake_snapshot_json())
            )
            provider = AlpacaIEXProvider(api_key="k", api_secret="s", http_client=httpx.Client())
            snap = provider.snapshot("AAPL")

        assert "IEX partial-market data" in snap.label

    def test_snapshot_http_error_returns_fallback(self) -> None:
        with respx.mock:
            route = respx.get(_ALPACA_SNAPSHOT_URL).mock(
                return_value=httpx.Response(500)
            )
            provider = AlpacaIEXProvider(api_key="k", api_secret="s", http_client=httpx.Client())
            snap = provider.snapshot("AAPL")

        assert route.called
        assert snap.price is None
        assert snap.previous_close is None
        assert snap.intraday_move_pct is None
        assert snap.market_status == MarketStatus.UNKNOWN
        assert any("snapshot_fetch_failed" in f for f in snap.quality_flags)

    def test_snapshot_network_error_returns_fallback(self) -> None:
        with respx.mock:
            route = respx.get(_ALPACA_SNAPSHOT_URL).mock(
                side_effect=httpx.ConnectError("connection refused")
            )
            provider = AlpacaIEXProvider(api_key="k", api_secret="s", http_client=httpx.Client())
            snap = provider.snapshot("AAPL")

        assert route.called
        assert snap.price is None
        assert any("snapshot_fetch_failed" in f for f in snap.quality_flags)

    def test_snapshot_missing_trade_sets_quality_flag(self) -> None:
        data = _fake_snapshot_json()
        data.pop("latestTrade", None)
        with respx.mock:
            respx.get(_ALPACA_SNAPSHOT_URL).mock(
                return_value=httpx.Response(200, json=data)
            )
            provider = AlpacaIEXProvider(api_key="k", api_secret="s", http_client=httpx.Client())
            snap = provider.snapshot("AAPL")

        assert snap.price is None
        assert "no_latest_trade_price" in snap.quality_flags

    def test_snapshot_missing_prev_close_sets_quality_flag(self) -> None:
        data = _fake_snapshot_json()
        data.pop("prevDailyBar", None)
        with respx.mock:
            respx.get(_ALPACA_SNAPSHOT_URL).mock(
                return_value=httpx.Response(200, json=data)
            )
            provider = AlpacaIEXProvider(api_key="k", api_secret="s", http_client=httpx.Client())
            snap = provider.snapshot("AAPL")

        assert snap.previous_close is None
        assert "no_previous_close" in snap.quality_flags

    def test_snapshot_missing_daily_bar_sets_quality_flag(self) -> None:
        data = _fake_snapshot_json()
        data.pop("dailyBar", None)
        with respx.mock:
            respx.get(_ALPACA_SNAPSHOT_URL).mock(
                return_value=httpx.Response(200, json=data)
            )
            provider = AlpacaIEXProvider(api_key="k", api_secret="s", http_client=httpx.Client())
            snap = provider.snapshot("AAPL")

        assert snap.volume is None
        assert "no_intraday_bar" in snap.quality_flags

    def test_snapshot_closed_market_status(self) -> None:
        data = _fake_snapshot_json(status="closed")
        with respx.mock:
            respx.get(_ALPACA_SNAPSHOT_URL).mock(
                return_value=httpx.Response(200, json=data)
            )
            provider = AlpacaIEXProvider(api_key="k", api_secret="s", http_client=httpx.Client())
            snap = provider.snapshot("AAPL")

        assert snap.market_status == MarketStatus.UNKNOWN  # Alpaca snapshot doesn't expose closed

    def test_close_method(self) -> None:
        provider = AlpacaIEXProvider(api_key="k", api_secret="s", http_client=httpx.Client())
        provider.close()  # Should not raise

    def test_historical_bars_success(self) -> None:
        with respx.mock:
            route = respx.get(_ALPACA_BARS_URL_RE).mock(
                return_value=httpx.Response(200, json=_fake_bars_json())
            )
            provider = AlpacaIEXProvider(api_key="k", api_secret="s", http_client=httpx.Client())
            bars = provider.historical_bars(
                "AAPL",
                start=datetime(2026, 6, 10, tzinfo=UTC),
                end=datetime(2026, 6, 12, tzinfo=UTC),
                timeframe="1Day",
            )

        assert route.called
        assert len(bars) == 2
        assert bars[0]["timestamp"] == datetime(2026, 6, 10, 13, 0, tzinfo=UTC)
        assert bars[0]["close"] == 199.8
        assert bars[1]["volume"] == 234_567
