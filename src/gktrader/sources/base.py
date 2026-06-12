from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import httpx

from gktrader.domain.contracts import FetchIndexResult, NormalizedDocument, SourceIndexItem
from gktrader.domain.enums import SourceTier


class SourceAdapter(ABC):
    source_name: str
    source_tier: SourceTier
    poll_interval_seconds: int = 60

    def __init__(self, client: httpx.Client | None = None) -> None:
        self.client = client or httpx.Client(timeout=20.0, follow_redirects=True)

    @abstractmethod
    def fetch_index(
        self,
        cursor: str | None,
        conditional_headers: dict[str, str] | None,
    ) -> FetchIndexResult:
        raise NotImplementedError

    @abstractmethod
    def fetch_detail(self, item: SourceIndexItem) -> Any:
        raise NotImplementedError

    @abstractmethod
    def normalize(self, raw_item: Any) -> NormalizedDocument:
        raise NotImplementedError

    @abstractmethod
    def derive_stable_external_id(self, raw_item: Any) -> str:
        raise NotImplementedError
