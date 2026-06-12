from __future__ import annotations

from abc import ABC, abstractmethod

from gktrader.domain.contracts import MarketSnapshotContract


class MarketDataProvider(ABC):
    provider_name: str

    @abstractmethod
    def snapshot(self, ticker: str) -> MarketSnapshotContract:
        raise NotImplementedError
