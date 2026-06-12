"""Commerce (Department of Commerce) press release adapter.

Acquisition order:
1. Normal HTTP/RSS/index fetch when available.
2. Local Playwright persistent browser fallback.

No CAPTCHA solving or proxy bypass.
Marks the source degraded if neither path succeeds.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any

import httpx
import trafilatura
from bs4 import BeautifulSoup
from pydantic import HttpUrl

from gktrader.domain.contracts import FetchIndexResult, NormalizedDocument, SourceIndexItem
from gktrader.domain.enums import SourceTier
from gktrader.sources.base import SourceAdapter

COMMERCE_PR_URL = "https://www.commerce.gov/news/press-releases"
SOURCE_NAME = "commerce"


class CommerceAdapter(SourceAdapter):
    """Adapter for Department of Commerce press releases.

    Tries direct HTTP first, then Playwright fallback.
    Records the successful fetch path in every NormalizedDocument.
    """

    source_name: str = SOURCE_NAME
    source_tier: SourceTier = SourceTier.TIER_1
    poll_interval_seconds: int = 60

    def __init__(
        self,
        client: httpx.Client | None = None,
        browser_context: Any = None,
        gkfetch_url: str = "",
        gkfetch_secret: str = "",
    ) -> None:
        super().__init__(client=client)
        self._browser_context = browser_context
        self._gkfetch_url = gkfetch_url.rstrip("/")
        self._gkfetch_secret = gkfetch_secret

    # ------------------------------------------------------------------
    # fetch_index
    # ------------------------------------------------------------------

    def fetch_index(
        self,
        cursor: str | None = None,
        conditional_headers: dict[str, str] | None = None,
    ) -> FetchIndexResult:
        # 1. Direct HTTP
        try:
            return self._fetch_http_index(cursor, conditional_headers)
        except (httpx.HTTPStatusError, httpx.RequestError):
            pass

        # 2. Playwright fallback
        try:
            return self._fetch_playwright_index(cursor)
        except Exception:
            pass

        # Neither path succeeded — raise so the caller can mark degraded
        msg = "All Commerce acquisition paths failed"
        raise RuntimeError(msg)

    def _fetch_http_index(
        self,
        cursor: str | None = None,
        conditional_headers: dict[str, str] | None = None,
    ) -> FetchIndexResult:
        url = COMMERCE_PR_URL
        if cursor:
            url += f"?page={cursor}"

        headers = dict(conditional_headers or {})
        resp = self.client.get(url, headers=headers or None)
        resp.raise_for_status()

        items = self._parse_listing_html(resp.text)
        next_cursor = self._extract_next_page(resp.text)

        return FetchIndexResult(
            items=items,
            cursor=next_cursor,
            etag=resp.headers.get("etag"),
            last_modified=resp.headers.get("last-modified"),
            fetch_path="http",
        )

    def _fetch_playwright_index(
        self,
        cursor: str | None = None,
    ) -> FetchIndexResult:
        url = COMMERCE_PR_URL
        if cursor:
            url += f"?page={cursor}"

        html = self._remote_fetch(url)

        items = self._parse_listing_html(html)
        next_cursor = self._extract_next_page(html)

        return FetchIndexResult(
            items=items,
            cursor=next_cursor,
            etag=None,
            last_modified=None,
            fetch_path="playwright",
        )

    def _remote_fetch(self, url: str) -> str:
        """Fetch *url* via the CM4 gkfetch service or the local browser context."""
        if self._gkfetch_url:
            resp = self.client.get(
                f"{self._gkfetch_url}/fetch",
                params={"url": url},
                headers={"X-Secret": self._gkfetch_secret},
                timeout=130.0,
            )
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                msg = f"Commerce: gkfetch error: {data['error']}"
                raise RuntimeError(msg)
            return data["html"]

        if self._browser_context:
            page = self._browser_context.new_page()
            try:
                page.goto(url, wait_until="networkidle")
                return page.content()
            finally:
                page.close()

        msg = "Commerce: neither gkfetch service nor Playwright browser context configured"
        raise RuntimeError(msg)

    # ------------------------------------------------------------------
    # fetch_detail
    # ------------------------------------------------------------------

    def fetch_detail(self, item: SourceIndexItem) -> Any:
        resp = self.client.get(str(item.detail_url))
        resp.raise_for_status()
        return resp.text

    # ------------------------------------------------------------------
    # normalize
    # ------------------------------------------------------------------

    def normalize(self, raw_item: Any) -> NormalizedDocument:
        if isinstance(raw_item, str):
            return self._normalize_detail_html(raw_item)
        if isinstance(raw_item, SourceIndexItem):
            return self._normalize_from_index(raw_item)
        if isinstance(raw_item, dict):
            return self._normalize_dict(raw_item)
        msg = f"Unsupported raw_item type: {type(raw_item)}"
        raise TypeError(msg)

    def _normalize_detail_html(self, html: str) -> NormalizedDocument:
        extracted = trafilatura.extract(html, output_format="txt", include_tables=False)
        text = (extracted or "").strip()
        # Extract title from HTML
        soup = BeautifulSoup(html, "html.parser")
        title_tag = soup.find("h1")
        title = title_tag.get_text(strip=True) if title_tag else ""
        content_hash = hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]
        return NormalizedDocument(
            source_name=self.source_name,
            source_tier=self.source_tier,
            fetch_path="http_detail",
            external_id=f"commerce-detail-{content_hash}",
            canonical_url=HttpUrl(COMMERCE_PR_URL),
            title=title,
            text=text or "",
            published_at=None,
            updated_at=None,
            detected_at=datetime.now(timezone.utc),
            source_metadata={"type": "detail_page"},
        )

    def _normalize_from_index(self, item: SourceIndexItem) -> NormalizedDocument:
        return NormalizedDocument(
            source_name=self.source_name,
            source_tier=self.source_tier,
            fetch_path="index",
            external_id=item.external_id,
            canonical_url=item.detail_url,
            title=item.title,
            text=item.title,
            published_at=item.published_at,
            updated_at=item.updated_at,
            detected_at=datetime.now(timezone.utc),
            source_metadata=dict(item.metadata),
        )

    def _normalize_dict(self, raw: dict) -> NormalizedDocument:
        text = raw.get("text", "") or raw.get("body", "") or raw.get("title", "")
        title = raw.get("title", "")
        return NormalizedDocument(
            source_name=self.source_name,
            source_tier=self.source_tier,
            fetch_path="fallback",
            external_id="commerce-" + hashlib.sha256(str(raw).encode()).hexdigest()[:16],
            canonical_url=HttpUrl(COMMERCE_PR_URL),
            title=title,
            text=text,
            published_at=None,
            updated_at=None,
            detected_at=datetime.now(timezone.utc),
            source_metadata=raw,
        )

    # ------------------------------------------------------------------
    # derive_stable_external_id
    # ------------------------------------------------------------------

    def derive_stable_external_id(self, raw_item: Any) -> str:
        if isinstance(raw_item, dict):
            path = raw_item.get("path", "") or raw_item.get("url", "") or str(raw_item)
            return "commerce-" + hashlib.sha256(path.encode("utf-8")).hexdigest()[:16]
        if isinstance(raw_item, str):
            return "commerce-" + hashlib.sha256(raw_item.encode("utf-8")).hexdigest()[:16]
        return "commerce-" + hashlib.sha256(str(raw_item).encode("utf-8")).hexdigest()[:16]

    # ------------------------------------------------------------------
    # Internal HTML parsers
    # ------------------------------------------------------------------

    def _parse_listing_html(self, html: str) -> list[SourceIndexItem]:
        """Extract press release links from the listing page."""
        soup = BeautifulSoup(html, "html.parser")
        items: list[SourceIndexItem] = []
        for link_tag in soup.select("a[href*='/news/press-releases/']"):
            href = link_tag.get("href", "")
            if not href or href.startswith("#"):
                continue
            full_url = href if href.startswith("http") else f"https://www.commerce.gov{href}"
            title = link_tag.get_text(strip=True)
            if not title:
                continue
            ext_id = "commerce-" + hashlib.sha256(full_url.encode("utf-8")).hexdigest()[:16]
            items.append(
                SourceIndexItem(
                    external_id=ext_id,
                    detail_url=HttpUrl(full_url),
                    title=title,
                    published_at=None,
                    updated_at=None,
                    metadata={"selector": "listing_link"},
                )
            )
        return items

    def _extract_next_page(self, html: str) -> str | None:
        """Extract the next page cursor from pagination links."""
        soup = BeautifulSoup(html, "html.parser")
        next_link = soup.select_one("a[rel='next']")
        if next_link:
            href = next_link.get("href", "")
            match = re.search(r"page=(\d+)", href)
            if match:
                return match.group(1)
        return None