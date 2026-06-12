"""SEC EDGAR 8-K adapter with filing detail parsing and ticker master helpers.

Primary endpoints:
- Current 8-K feed: https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&output=atom
- Company ticker master: https://www.sec.gov/files/company_tickers.json
- Company submissions: https://data.sec.gov/submissions/CIK##########.json

Always uses an identifying SEC User-Agent.
Stays below the SEC's 10 requests/second guideline.
Includes a deterministic keyword prefilter for government-relevant filings.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from typing import Any

import feedparser
from pydantic import HttpUrl

from gktrader.domain.contracts import FetchIndexResult, NormalizedDocument, SourceIndexItem
from gktrader.domain.enums import SourceTier
from gktrader.sources.base import SourceAdapter

SEC_8K_FEED_URL = (
    "https://www.sec.gov/cgi-bin/browse-edgar"
    "?action=getcurrent&type=8-K&output=atom"
)
SEC_TICKER_MASTER_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_BASE = "https://data.sec.gov/submissions"
SEC_SOURCE_NAME = "sec_8k"

# Minimum interval between SEC requests (seconds) to stay under 10 req/sec
SEC_MIN_INTERVAL = 0.15

# Keyword prefilter — only filings containing at least one of these
# terms in the title or summary are passed to the LLM.
PREFILTER_KEYWORDS: list[str] = [
    "contract", "award", "grant", "loan", "funding", "warrant",
    "government", "federal", "agency", "appropriation", "subsidy",
    "national security", "defense", "tariff", "trade", "sanction",
    "investigation", "cancellation", "termination", "material definitive",
    "1.01", "1.02", "2.01", "2.02", "2.03", "2.04", "2.05", "2.06",
    "3.01", "3.02", "3.03", "4.01", "4.02", "5.01", "5.02", "5.03",
    "5.04", "5.05", "5.06", "5.07", "5.08", "6.01", "6.02", "6.03",
    "6.04", "6.05", "7.01", "8.01", "9.01",
]
PREFILTER_REGEX = re.compile(
    "|".join(re.escape(kw) for kw in PREFILTER_KEYWORDS),
    re.IGNORECASE,
)


def pad_cik(cik: str | int) -> str:
    """Pad a CIK number to exactly 10 digits with leading zeros."""
    return str(int(cik)).zfill(10)


class SECAdapter(SourceAdapter):
    """Adapter for SEC EDGAR 8-K filings.

    Features:
    - Polls the current 8-K Atom feed.
    - Parses filing detail from linked HTML documents.
    - Applies a deterministic keyword prefilter.
    - Records SEC User-Agent behaviour.
    - Provides a class method for parsing the company ticker master.
    - Enforces a minimum interval between requests.
    """

    source_name: str = SEC_SOURCE_NAME
    source_tier: SourceTier = SourceTier.TIER_1
    poll_interval_seconds: int = 60

    def __init__(
        self,
        client: httpx.Client | None = None,  # noqa: F821 — from __future__
        user_agent: str = "GKTrader/0.1 (contact@gktrader.example.com)",
    ) -> None:
        import httpx  # noqa: PLC0415

        super().__init__(client=client)
        self._user_agent = user_agent
        self._last_request_time: float = 0.0

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------

    def _rate_limit(self) -> None:
        """Wait if necessary to stay under 10 requests/second."""
        now = time.monotonic()
        elapsed = now - self._last_request_time
        if elapsed < SEC_MIN_INTERVAL:
            time.sleep(SEC_MIN_INTERVAL - elapsed)
        self._last_request_time = time.monotonic()

    def _request(self, url: str, headers: dict[str, str] | None = None) -> httpx.Response:  # noqa: F821
        self._rate_limit()
        req_headers = dict(headers or {})
        req_headers.setdefault("User-Agent", self._user_agent)
        req_headers.setdefault("Accept", "application/json, application/atom+xml, text/html")
        resp = self.client.get(url, headers=req_headers)
        resp.raise_for_status()
        return resp

    # ------------------------------------------------------------------
    # fetch_index
    # ------------------------------------------------------------------

    def fetch_index(
        self,
        cursor: str | None = None,
        conditional_headers: dict[str, str] | None = None,
    ) -> FetchIndexResult:
        url = SEC_8K_FEED_URL
        if cursor:
            url += f"&cursor={cursor}"

        headers = dict(conditional_headers or {})
        resp = self._request(url, headers=headers or None)

        feed = feedparser.parse(resp.content)
        items: list[SourceIndexItem] = []
        for entry in feed.entries:
            ext_id = self.derive_stable_external_id(entry)
            link = entry.get("link", "")
            pub = _parse_rss_datetime(entry.get("published_parsed"))
            updated = _parse_rss_datetime(entry.get("updated_parsed"))
            summary = entry.get("summary", "")
            title = entry.get("title", "")

            # Apply keyword prefilter
            prefilter_match = bool(PREFILTER_REGEX.search(title + " " + summary))

            items.append(
                SourceIndexItem(
                    external_id=ext_id,
                    detail_url=HttpUrl(link),
                    title=title,
                    published_at=pub,
                    updated_at=updated,
                    metadata={
                        "summary": summary,
                        "accession_number": _extract_accession(link),
                        "prefilter_match": prefilter_match,
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

    # ------------------------------------------------------------------
    # fetch_detail
    # ------------------------------------------------------------------

    def fetch_detail(self, item: SourceIndexItem) -> Any:
        """Fetch the filing detail page and return its HTML content."""
        resp = self._request(str(item.detail_url))
        return resp.text

    # ------------------------------------------------------------------
    # normalize
    # ------------------------------------------------------------------

    def normalize(self, raw_item: Any) -> NormalizedDocument:
        if isinstance(raw_item, str):
            return self._normalize_filing_html(raw_item)
        if hasattr(raw_item, "get") and (
            raw_item.get("link") is not None or raw_item.get("summary") is not None
        ):
            return self._normalize_feed_entry(raw_item)
        if isinstance(raw_item, dict):
            return self._normalize_filing_dict(raw_item)
        # Feedparser entry
        return self._normalize_feed_entry(raw_item)

    def _normalize_feed_entry(self, entry: Any) -> NormalizedDocument:
        ext_id = self.derive_stable_external_id(entry)
        link = str(entry.get("link", ""))
        pub = _parse_rss_datetime(entry.get("published_parsed"))
        updated = _parse_rss_datetime(entry.get("updated_parsed"))
        summary = entry.get("summary", "") or ""
        title = entry.get("title", "") or ""
        return NormalizedDocument(
            source_name=self.source_name,
            source_tier=self.source_tier,
            fetch_path="rss",
            external_id=ext_id,
            canonical_url=HttpUrl(link),
            title=title,
            text=summary,
            published_at=pub,
            updated_at=updated,
            detected_at=datetime.now(timezone.utc),
            source_metadata={
                "accession_number": _extract_accession(link),
                "prefilter_match": bool(PREFILTER_REGEX.search(title + " " + summary)),
            },
        )

    def _normalize_filing_html(self, html: str) -> NormalizedDocument:
        """Extract text content from an SEC filing HTML page."""
        from bs4 import BeautifulSoup  # noqa: PLC0415

        soup = BeautifulSoup(html, "html.parser")
        # Remove scripts, styles, nav
        for tag in soup(["script", "style", "nav", "header", "footer"]):
            tag.decompose()

        title_tag = soup.find("h1") or soup.find("title")
        title = title_tag.get_text(strip=True) if title_tag else "SEC Filing"
        text = soup.get_text(separator=" ", strip=True)
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

        return NormalizedDocument(
            source_name=self.source_name,
            source_tier=self.source_tier,
            fetch_path="filing_detail",
            external_id=f"sec-detail-{content_hash}",
            canonical_url=HttpUrl("https://www.sec.gov/cgi-bin/browse-edgar"),
            title=title,
            text=text,
            published_at=None,
            updated_at=None,
            detected_at=datetime.now(timezone.utc),
            source_metadata={"type": "filing_detail"},
        )

    def _normalize_filing_dict(self, raw: dict) -> NormalizedDocument:
        """Normalize from a structured filing dict (e.g. from CIK JSON)."""
        text = raw.get("text", "") or raw.get("description", "") or raw.get("title", "")
        title = raw.get("title", "")
        accession = raw.get("accessionNumber", "")
        return NormalizedDocument(
            source_name=self.source_name,
            source_tier=self.source_tier,
            fetch_path="submissions_api",
            external_id="sec-" + hashlib.sha256(str(raw).encode()).hexdigest()[:16],
            canonical_url=HttpUrl("https://data.sec.gov/submissions"),
            title=title,
            text=text,
            published_at=None,
            updated_at=None,
            detected_at=datetime.now(timezone.utc),
            source_metadata={
                "accession_number": accession,
                "form": raw.get("form"),
                "primary_document": raw.get("primaryDocument"),
            },
        )

    # ------------------------------------------------------------------
    # derive_stable_external_id
    # ------------------------------------------------------------------

    def derive_stable_external_id(self, raw_item: Any) -> str:
        if hasattr(raw_item, "get"):
            # Feedparser entry or dict
            link = raw_item.get("link") or raw_item.get("id", "")
            accession = _extract_accession(link)
            if accession:
                return f"sec-8k-{accession}"
            accession = _extract_accession(raw_item.get("id", ""))
            if accession:
                return f"sec-8k-{accession}"
            return "sec-" + hashlib.sha256(link.encode("utf-8")).hexdigest()[:16]
        if isinstance(raw_item, str):
            return "sec-" + hashlib.sha256(raw_item.encode("utf-8")).hexdigest()[:16]
        return "sec-" + hashlib.sha256(str(raw_item).encode("utf-8")).hexdigest()[:16]

    # ------------------------------------------------------------------
    # Company ticker master helper
    # ------------------------------------------------------------------

    @staticmethod
    def parse_ticker_master(data: str | dict[str, Any]) -> list[dict[str, Any]]:
        """Parse the SEC company tickers JSON into a list of company records.

        The SEC ticker master has the format:
            {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, ...}

        Returns a list of dicts with keys: cik, ticker, name, cik_padded.
        """
        if isinstance(data, str):
            records = json.loads(data)
        else:
            records = data

        result: list[dict[str, Any]] = []
        for key in sorted(records, key=int):  # Sort by index for determinism
            entry = records[key]
            cik = str(entry.get("cik_str", ""))
            result.append({
                "cik": cik,
                "cik_padded": pad_cik(cik),
                "ticker": entry.get("ticker", "").strip().upper(),
                "name": entry.get("title", "").strip(),
            })
        return result

    @staticmethod
    def matches_prefilter(title: str, summary: str = "") -> bool:
        """Check whether a filing title or summary matches the keyword prefilter.

        This is a static method so it can be used in tests and
        downstream logic without instantiating the adapter.
        """
        return bool(PREFILTER_REGEX.search(title + " " + summary))

    @classmethod
    def fetch_ticker_master(cls, client: httpx.Client, user_agent: str) -> list[dict[str, Any]]:
        """Convenience: fetch and parse the SEC company ticker master.

        Usage:
            with httpx.Client() as client:
                companies = SECAdapter.fetch_ticker_master(client, "MyUA/1.0")
        """
        headers = {"User-Agent": user_agent, "Accept": "application/json"}
        resp = client.get(SEC_TICKER_MASTER_URL, headers=headers)
        resp.raise_for_status()
        return cls.parse_ticker_master(resp.text)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_rss_datetime(struct_time: Any) -> datetime | None:
    if struct_time is None:
        return None
    import calendar

    ts = calendar.timegm(struct_time)
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def _extract_accession(link: str) -> str | None:
    """Extract the accession number from an SEC EDGAR URL."""
    # Typical SEC URL: /cgi-bin/browse-edgar?action=getcompany&CIK=...
    # or /Archives/edgar/data/.../0001234567-23-000001-index.html
    match = re.search(
        r"/Archives/edgar/data/\d+/(\d{10})-(\d{2})-(\d{6})",
        link,
    )
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    # Try to find accession in query params
    match = re.search(r"[?&]accession_number=([^&]+)", link)
    if match:
        return match.group(1)
    return None
