"""Alpaca IEX partial-market data provider.

Uses the free Alpaca IEX feed. All outputs are labeled as
"IEX partial-market data" and must never be treated as authoritative.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from gktrader.domain.contracts import MarketSnapshotContract
from gktrader.domain.enums import MarketStatus
from gktrader.marketdata.base import MarketDataProvider

IEX_LABEL = "IEX partial-market data"

# Alpaca v2 market data API base
_ALPACA_DATA_URL = "https://data.alpaca.markets/v2"


class AlpacaIEXProvider(MarketDataProvider):
    """Market data provider using the Alpaca IEX free feed.

    All snapshots carry the ``IEX partial-market data`` label.
    """

    provider_name = "alpaca"

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._client = http_client or httpx.Client()

    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self._api_key,
            "APCA-API-SECRET-KEY": self._api_secret,
        }

    def snapshot(self, ticker: str) -> MarketSnapshotContract:
        """Fetch an IEX snapshot for *ticker*.

        Returns a ``MarketSnapshotContract`` with the ``IEX partial-market data``
        label.  If the API call fails or returns incomplete data the snapshot
        will have ``None`` price fields and an appropriate quality flag.
        """
        request_time = datetime.now(UTC)
        url = f"{_ALPACA_DATA_URL}/stocks/{ticker}/snapshot?feed=iex"

        try:
            resp = self._client.get(url, headers=self._headers(), timeout=10)
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
        except Exception as exc:
            return MarketSnapshotContract(
                ticker=ticker,
                provider=self.provider_name,
                feed="IEX",
                observed_at=datetime.now(UTC),
                request_time=request_time,
                price=None,
                previous_close=None,
                intraday_move_pct=None,
                market_status=MarketStatus.UNKNOWN,
                volume=None,
                quality_flags=[f"snapshot_fetch_failed: {exc}"],
                label=IEX_LABEL,
            )

        return self._parse_snapshot(ticker, data, request_time)

    def _parse_snapshot(
        self,
        ticker: str,
        data: dict[str, Any],
        request_time: datetime,
    ) -> MarketSnapshotContract:
        """Parse the Alpaca snapshot JSON into a ``MarketSnapshotContract``."""
        observed_at = datetime.now(UTC)
        quality_flags: list[str] = []

        # Latest trade
        latest_trade = data.get("latestTrade") or {}
        price: float | None = latest_trade.get("p")

        # Previous daily bar
        prev_bar = data.get("prevDailyBar") or {}
        previous_close: float | None = prev_bar.get("c")

        # Current daily bar (intraday)
        daily_bar = data.get("dailyBar") or {}
        open_price: float | None = daily_bar.get("o")
        current_close: float | None = daily_bar.get("c")

        # Intraday move
        intraday_move_pct: float | None = None
        if open_price is not None and current_close is not None and open_price != 0:
            intraday_move_pct = ((current_close - open_price) / open_price) * 100.0
        elif price is not None and previous_close is not None and previous_close != 0:
            # Fallback: use latest trade vs previous close
            intraday_move_pct = ((price - previous_close) / previous_close) * 100.0

        # Volume
        volume: int | None = daily_bar.get("v")

        # Market status
        market_status = self._determine_market_status(data)

        # Quality flags
        if price is None:
            quality_flags.append("no_latest_trade_price")
        if previous_close is None:
            quality_flags.append("no_previous_close")
        if daily_bar is None or not daily_bar:
            quality_flags.append("no_intraday_bar")

        return MarketSnapshotContract(
            ticker=ticker,
            provider=self.provider_name,
            feed="IEX",
            observed_at=observed_at,
            request_time=request_time,
            price=price,
            previous_close=previous_close,
            intraday_move_pct=intraday_move_pct,
            market_status=market_status,
            volume=volume,
            quality_flags=quality_flags,
            label=IEX_LABEL,
        )

    @staticmethod
    def _determine_market_status(data: dict[str, Any]) -> MarketStatus:
        """Determine market status from the Alpaca snapshot response."""
        status = (data.get("status") or "").lower()
        if status == "open":
            return MarketStatus.OPEN
        # Alpaca does not expose premarket/after-hours in the snapshot endpoint
        # directly; we infer from context.
        return MarketStatus.UNKNOWN

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()