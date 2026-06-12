"""Paper entry rules and notional determination.

Paper notionals per alert level:
    WATCH:       EUR 0
    REVIEW:      EUR 500
    TRADEABLE:   EUR 1,000
    AVOID_CHASE: EUR 0
    IGNORE:      EUR 0

Bullish events use directional long paper returns.
Bearish events use inverse/short directional returns for analysis only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from gktrader.domain.contracts import MarketSnapshotContract
from gktrader.domain.enums import AlertLevel, Direction

IEX_PARTIAL_LABEL = "IEX partial-market data"

# Paper notionals keyed by alert level
_PAPER_NOTIONALS: dict[AlertLevel, float] = {
    AlertLevel.WATCH: 0.0,
    AlertLevel.REVIEW: 500.0,
    AlertLevel.TRADEABLE: 1000.0,
    AlertLevel.AVOID_CHASE: 0.0,
    AlertLevel.IGNORE: 0.0,
}


@dataclass
class PaperEntry:
    """A computed paper trade entry."""

    ticker: str
    direction: Direction
    alert_level: AlertLevel
    notional_eur: float
    entry_price: float | None
    entry_time: datetime | None
    provider: str | None
    feed: str | None
    quality_flags: list[str] = field(default_factory=list)
    label: str = IEX_PARTIAL_LABEL


def get_paper_notional(level: AlertLevel) -> float:
    """Return the paper notional for *level*.

    Returns 0.0 for any unrecognised level.
    """
    return _PAPER_NOTIONALS.get(level, 0.0)


def make_paper_entry(
    ticker: str,
    direction: Direction,
    alert_level: AlertLevel,
    snapshot: MarketSnapshotContract | None,
) -> PaperEntry:
    """Build a ``PaperEntry`` from an alert and its market snapshot.

    The notional is determined by *alert_level*.  The entry price is
    taken from the snapshot's latest trade price (or ``None`` if
    unavailable).  For out-of-hours alerts the snapshot price is
    retained as context but the caller should use
    ``resolve_entry_session`` to get the first eligible regular-session
    price for actual paper entry.

    Bearish events use inverse/short direction for analysis only.
    """
    notional = get_paper_notional(alert_level)

    if snapshot is not None:
        entry_price = snapshot.price
        entry_time = snapshot.observed_at
        provider = snapshot.provider
        feed = snapshot.feed
        quality_flags = list(snapshot.quality_flags)
    else:
        entry_price = None
        entry_time = None
        provider = None
        feed = None
        quality_flags = ["no_market_snapshot"]

    return PaperEntry(
        ticker=ticker,
        direction=direction,
        alert_level=alert_level,
        notional_eur=notional,
        entry_price=entry_price,
        entry_time=entry_time,
        provider=provider,
        feed=feed,
        quality_flags=quality_flags,
        label=IEX_PARTIAL_LABEL,
    )