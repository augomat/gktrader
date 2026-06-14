"""NIST news RSS adapter.

Primary path: https://www.nist.gov/news-events/news/rss.xml

Polls the RSS feed, fetches article detail, preserves program and
category metadata (e.g. CHIPS, quantum context), and normalises
to the locked NormalizedDocument schema.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

import feedparser
import trafilatura
from pydantic import HttpUrl

from gktrader.domain.contracts import FetchIndexResult, NormalizedDocument, SourceIndexItem
from gktrader.domain.enums import SourceTier
from gktrader.sources.base import SourceAdapter

FEED_URL = "https://www.nist.gov/news-events/news/rss.xml"


class NISTAdapter(SourceAdapter):
    """Adapter for the NIST news RSS feed."""

    source_name: str = "nist"
    source_tier: SourceTier = SourceTier.TIER_1
    poll_interval_seconds: int = 60

    def fetch_index(
        self,
        cursor: str | None = None,
        conditional_headers: dict[str, str] | None = None,
    ) -> FetchIndexResult:
        headers = dict(conditional_headers or {})
        resp = self.client.get(FEED_URL, headers=headers or None)
        if resp.status_code == 304:
            etag = resp.headers.get("etag") or (conditional_headers or {}).get("If-None-Match")
            last_mod = resp.headers.get("last-modified") or (conditional_headers or {}).get("If-Modified-Since")
            return FetchIndexResult(items=[], fetch_path="rss", etag=etag, last_modified=last_mod)
        resp.raise_for_status()

        feed = feedparser.parse(resp.content)
        items: list[SourceIndexItem] = []
        for entry in feed.entries:
            ext_id = self.derive_stable_external_id(entry)
            link = entry.get("link", "")
            pub = _parse_rss_datetime(entry.get("published_parsed"))
            updated = _parse_rss_datetime(entry.get("updated_parsed"))
            # NIST RSS often includes category tags and dc:subject
            categories = [t["term"] for t in entry.get("tags", []) if "term" in t]
            items.append(
                SourceIndexItem(
                    external_id=ext_id,
                    detail_url=HttpUrl(link),
                    title=entry.get("title", ""),
                    published_at=pub,
                    updated_at=updated,
                    metadata={
                        "summary": entry.get("summary", ""),
                        "categories": categories,
                    },
                )
            )

        etag = resp.headers.get("etag")
        last_modified = resp.headers.get("last-modified")
        return FetchIndexResult(
            items=items,
            fetch_path="rss",
            etag=etag,
            last_modified=last_modified,
        )

    def fetch_detail(self, item: SourceIndexItem) -> Any:
        resp = self.client.get(str(item.detail_url))
        resp.raise_for_status()
        return {"item": item, "html": resp.text}

    def normalize(self, raw_item: Any) -> NormalizedDocument:
        if isinstance(raw_item, dict) and "item" in raw_item and "html" in raw_item:
            return self._normalize_wrapped_detail(raw_item["item"], raw_item["html"])
        if isinstance(raw_item, str):
            return self._normalize_html_detail(raw_item)
        return self._normalize_entry(raw_item)

    def _normalize_wrapped_detail(
        self, item: SourceIndexItem, html: str,
    ) -> NormalizedDocument:
        extracted = trafilatura.extract(html, output_format="txt", include_tables=False)
        text = (extracted or "").strip()
        metadata = dict(item.metadata)
        metadata["type"] = "detail_page"
        # Preserve categories from item metadata
        if "categories" not in metadata:
            categories = item.metadata.get("categories", [])
            if categories:
                metadata["categories"] = categories
        return NormalizedDocument(
            source_name=self.source_name,
            source_tier=self.source_tier,
            fetch_path="rss_detail",
            external_id=item.external_id,
            canonical_url=item.detail_url,
            title=item.title,
            text=text or "",
            published_at=item.published_at,
            updated_at=item.updated_at,
            detected_at=datetime.now(timezone.utc),
            source_metadata=metadata,
        )

    def _normalize_entry(self, entry: Any) -> NormalizedDocument:
        ext_id = self.derive_stable_external_id(entry)
        link = str(entry.get("link", ""))
        pub = _parse_rss_datetime(entry.get("published_parsed"))
        updated = _parse_rss_datetime(entry.get("updated_parsed"))
        categories = [t["term"] for t in entry.get("tags", []) if "term" in t]
        text = entry.get("summary", "") or entry.get("title", "")
        return NormalizedDocument(
            source_name=self.source_name,
            source_tier=self.source_tier,
            fetch_path="rss",
            external_id=ext_id,
            canonical_url=HttpUrl(link),
            title=entry.get("title", ""),
            text=text,
            published_at=pub,
            updated_at=updated,
            detected_at=datetime.now(timezone.utc),
            source_metadata={
                "summary": entry.get("summary", ""),
                "categories": categories,
            },
        )

    def _normalize_html_detail(self, html: str) -> NormalizedDocument:
        extracted = trafilatura.extract(html, output_format="txt", include_tables=False)
        text = (extracted or "").strip()
        content_hash = hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]
        return NormalizedDocument(
            source_name=self.source_name,
            source_tier=self.source_tier,
            fetch_path="rss_detail",
            external_id=f"nist-detail-{content_hash}",
            canonical_url=HttpUrl("https://www.nist.gov/news-events/news"),
            title="",
            text=text or "",
            published_at=None,
            updated_at=None,
            detected_at=datetime.now(timezone.utc),
            source_metadata={"type": "detail_page"},
        )

    def derive_stable_external_id(self, raw_item: Any) -> str:
        if hasattr(raw_item, "get"):
            guid = raw_item.get("id") or raw_item.get("guid") or raw_item.get("link", "")
            return _stable_id_from_url(guid)
        if isinstance(raw_item, str):
            return _stable_id_from_url(raw_item)
        return _stable_id_from_url(str(raw_item))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_rss_datetime(struct_time: Any) -> datetime | None:
    if struct_time is None:
        return None
    import calendar
    import time

    ts = calendar.timegm(struct_time)
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def _stable_id_from_url(url: str) -> str:
    """Derive a deterministic external ID from a URL or guid string."""
    return "nist-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]