"""Truth Social adapter with tiered acquisition and cross-path deduplication.

Acquisition order:
1. Direct Mastodon-compatible Truth Social account/status API.
2. Local Playwright persistent browser session.
3. CNN mirror at https://ix.cnn.io/data/truth-social/truth_archive.json

Every normalized document records the successful fetch path and latency.
Cross-path deduplication prefers the earliest detected version.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from typing import Any

from pydantic import HttpUrl

from gktrader.domain.contracts import FetchIndexResult, NormalizedDocument, SourceIndexItem
from gktrader.domain.enums import SourceTier
from gktrader.sources.base import SourceAdapter

TRUTH_SOCIAL_API_BASE = "https://truthsocial.com"
TRUTH_SOCIAL_ACCOUNT_ID = "107780257626246573"  # @realDonaldTrump
TRUTH_SOCIAL_PROFILE_URL = f"{TRUTH_SOCIAL_API_BASE}/@realDonaldTrump"
CNN_MIRROR_URL = "https://ix.cnn.io/data/truth-social/truth_archive.json"
TRUTH_SOURCE_NAME = "truthsocial"

# The CNN mirror returns the *entire* archive (tens of thousands of posts) on
# every request.  Bound each poll to the most recent posts so a single poll
# cannot enqueue tens of thousands of fetch/normalize/DB operations.  Dedup on
# (source_name, external_id, content_hash) still guarantees idempotency.
MAX_MIRROR_ITEMS = 50
_PLAYWRIGHT_RELATIVE_TIME_RE = re.compile(r"\s*·\s*\d+\s*[smhdwy]\b", re.IGNORECASE)
_PLAYWRIGHT_PREFIX_RE = re.compile(
    r"^(?:Pinned Truth\s+)?Donald J\. Trump\s+@realDonaldTrump\b\s*",
    re.IGNORECASE,
)
# Matches a trailing run of engagement-counter tokens (e.g., "842 767 2.86K"
# or "45.2K 12.3K 1,234").  Used to produce a stable identity across scrapes
# where engagement counts shift.
_PLAYWRIGHT_TRAILING_COUNTERS_RE = re.compile(
    r"(?:\s+[\d,]+(?:\.\d+)?[KkMmBb]?){2,}\s*$|\s+[\d,]+(?:\.\d+)?[KkMmBb]\s*$",
)
# Matches a line that is entirely a numeric counter value (optionally with
# K/M/B suffix).  Used to reject pure counter lines.
_PLAYWRIGHT_COUNTER_ONLY_RE = re.compile(r"^[\d,]+(?:\.\d+)?[KkMmBb]?$")
_PLAYWRIGHT_COUNTER_SEQUENCE_RE = re.compile(
    r"^[\d,]+(?:\.\d+)?[KkMmBb]?(?:\s+[\d,]+(?:\.\d+)?[KkMmBb]?)+$",
)


class TruthSocialAdapter(SourceAdapter):
    """Adapter for Truth Social with three-tier fallback acquisition.

    Cursors are kept per acquisition path so that each path can
    independently resume after failure.
    """

    source_name: str = TRUTH_SOURCE_NAME
    source_tier: SourceTier = SourceTier.TIER_1
    poll_interval_seconds: int = 600

    def __init__(
        self,
        client: httpx.Client | None = None,  # noqa: F821 — imported via from __future__
        browser_context: Any = None,
        gkfetch_url: str = "",
        gkfetch_secret: str = "",
    ) -> None:
        import httpx  # noqa: PLC0415

        super().__init__(client=client)
        self._browser_context = browser_context
        self._gkfetch_url = gkfetch_url.rstrip("/")
        self._gkfetch_secret = gkfetch_secret

    # ------------------------------------------------------------------
    # fetch_index: try paths in order
    # ------------------------------------------------------------------

    def fetch_index(
        self,
        cursor: str | None = None,
        conditional_headers: dict[str, str] | None = None,
    ) -> FetchIndexResult:
        # 1. Direct API
        try:
            return self._fetch_direct_api(cursor, conditional_headers)
        except Exception:
            pass

        # 2. Playwright
        try:
            return self._fetch_playwright(cursor)
        except Exception:
            pass

        # 3. CNN mirror
        return self._fetch_cnn_mirror(cursor, conditional_headers)

    def _fetch_direct_api(
        self,
        cursor: str | None = None,
        conditional_headers: dict[str, str] | None = None,
    ) -> FetchIndexResult:
        start = time.monotonic()
        url = (
            f"{TRUTH_SOCIAL_API_BASE}/api/v1/accounts/{TRUTH_SOCIAL_ACCOUNT_ID}/statuses"
            f"?exclude_replies=true&exclude_reblogs=true"
        )
        if cursor:
            url += f"&max_id={cursor}"

        headers = dict(conditional_headers or {})
        headers.setdefault("Accept", "application/json")
        resp = self.client.get(url, headers=headers)
        resp.raise_for_status()
        elapsed = time.monotonic() - start

        data = resp.json()
        items = self._parse_api_statuses(data)
        next_cursor = self._extract_max_id(data)

        return FetchIndexResult(
            items=items,
            cursor=next_cursor,
            etag=resp.headers.get("etag"),
            last_modified=resp.headers.get("last-modified"),
            fetch_path="direct_api",
        )

    def _fetch_playwright(self, cursor: str | None = None) -> FetchIndexResult:
        url = TRUTH_SOCIAL_PROFILE_URL
        if cursor:
            url += f"?cursor={cursor}"

        raw = self._remote_fetch_text(url)
        items = self._parse_text_listing(raw)

        return FetchIndexResult(
            items=items,
            cursor=None,
            etag=None,
            last_modified=None,
            fetch_path="playwright",
        )

    def _remote_fetch_text(self, url: str) -> str:
        """Fetch *url* via gkfetch service or local browser; return visible body text.

        Strips <script>, <style>, <noscript> before extracting text so the
        output approximates what browser innerText returns.
        """
        from bs4 import BeautifulSoup  # noqa: PLC0415

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
                msg = f"TruthSocial: gkfetch error: {data['error']}"
                raise RuntimeError(msg)
            soup = BeautifulSoup(data["html"], "html.parser")
            # Extract visible post text from status elements (Truth Social React app)
            status_els = soup.find_all(attrs={"data-testid": "status"})
            if status_els:
                lines = []
                for el in status_els:
                    for tag in el.find_all(["script", "style", "noscript"]):
                        tag.decompose()
                    text = el.get_text(separator=" ", strip=True)
                    if text:
                        lines.append(text)
                return "\n".join(lines)
            # Fallback: stripped body text
            for tag in soup.find_all(["script", "style", "noscript"]):
                tag.decompose()
            body = soup.find("body")
            return body.get_text(separator="\n") if body else ""

        if self._browser_context:
            page = self._browser_context.new_page()
            try:
                page.goto(url, wait_until="networkidle")
                return page.evaluate("() => document.body.innerText")
            finally:
                page.close()

        msg = "TruthSocial: neither gkfetch service nor Playwright browser context configured"
        raise RuntimeError(msg)

    def _fetch_cnn_mirror(
        self,
        cursor: str | None = None,
        conditional_headers: dict[str, str] | None = None,
    ) -> FetchIndexResult:
        start = time.monotonic()
        headers = dict(conditional_headers or {})
        resp = self.client.get(CNN_MIRROR_URL, headers=headers or None)
        resp.raise_for_status()
        elapsed = time.monotonic() - start

        data = resp.json()
        items = self._parse_cnn_mirror(data)
        return FetchIndexResult(
            items=items,
            cursor=None,
            etag=resp.headers.get("etag"),
            last_modified=resp.headers.get("last-modified"),
            fetch_path="cnn_mirror",
        )

    # ------------------------------------------------------------------
    # fetch_detail — not directly applicable for TS posts (self-contained)
    # ------------------------------------------------------------------

    def fetch_detail(self, item: SourceIndexItem) -> Any:
        # Truth Social statuses are self-contained in the index.  The original
        # post payload is preserved under ``metadata["raw"]`` by the parsers, so
        # return it wrapped with the acquisition path so ``normalize`` can route
        # it to the correct path-specific normalizer (and record the true
        # fetch_path).  Without this, the generic ingest loop hands an
        # index-shaped dict to ``_normalize_api_post`` and every item fails.
        meta = item.metadata or {}
        raw = meta.get("raw")
        path = meta.get("source") or "direct_api"
        if raw is not None:
            return {"_path": path, "post": raw}
        # No original payload available (e.g. playwright text listing): fall back
        # to normalizing the index item directly.
        return item

    # ------------------------------------------------------------------
    # normalize
    # ------------------------------------------------------------------

    def normalize(self, raw_item: Any) -> NormalizedDocument:
        if isinstance(raw_item, SourceIndexItem):
            return self._normalize_from_index(raw_item)
        if isinstance(raw_item, dict):
            # Wrapped detail payload produced by ``fetch_detail``: dispatch on
            # the recorded acquisition path so cnn-mirror posts keep their full
            # text and correct fetch_path.
            if "_path" in raw_item and "post" in raw_item:
                post = raw_item["post"]
                if raw_item["_path"] == "cnn_mirror":
                    return self.normalize_cnn_mirror_post(post)
                return self._normalize_api_post(post)
            # Bare Mastodon-compatible API post dict (direct API / unit tests).
            return self._normalize_api_post(raw_item)
        msg = f"Unsupported raw_item type: {type(raw_item)}"
        raise TypeError(msg)

    def _normalize_api_post(self, post: dict) -> NormalizedDocument:
        ext_id = self.derive_stable_external_id(post)
        content = post.get("content", "") or ""
        created = _parse_iso(post.get("created_at"))
        edited = _parse_iso(post.get("edited_at"))
        url = post.get("url") or post.get("uri", "")
        return NormalizedDocument(
            source_name=self.source_name,
            source_tier=self.source_tier,
            fetch_path="direct_api",
            external_id=ext_id,
            canonical_url=HttpUrl(url),
            title=_truncate_title(content),
            text=content,
            published_at=created,
            updated_at=edited,
            detected_at=datetime.now(timezone.utc),
            source_metadata={
                "post_id": post.get("id"),
                "visibility": post.get("visibility"),
                "account": post.get("account", {}).get("acct"),
                "media_attachments": [
                    a.get("url") for a in post.get("media_attachments", [])
                ],
            },
        )

    def _normalize_from_index(self, item: SourceIndexItem) -> NormalizedDocument:
        metadata = dict(item.metadata)
        full_text = (
            metadata.get("normalized_line")
            or metadata.get("raw_line")
            or item.title
        )
        return NormalizedDocument(
            source_name=self.source_name,
            source_tier=self.source_tier,
            fetch_path="index_fallback",
            external_id=item.external_id,
            canonical_url=item.detail_url,
            title=item.title,
            text=full_text,
            published_at=item.published_at,
            updated_at=item.updated_at,
            detected_at=datetime.now(timezone.utc),
            source_metadata=metadata,
        )

    def normalize_cnn_mirror_post(self, post: dict) -> NormalizedDocument:
        """Normalize a CNN mirror post entry, recording the fallback fetch path."""
        ext_id = self.derive_stable_external_id(post)
        content = post.get("text") or post.get("content", "")
        created = _parse_iso(post.get("created_at") or post.get("date"))
        url = post.get("url") or post.get("permalink", "")
        return NormalizedDocument(
            source_name=self.source_name,
            source_tier=self.source_tier,
            fetch_path="cnn_mirror",
            external_id=ext_id,
            canonical_url=HttpUrl(url),
            title=_truncate_title(content),
            text=content,
            published_at=created,
            updated_at=None,
            detected_at=datetime.now(timezone.utc),
            source_metadata={
                "source": "cnn_mirror",
                "original_id": post.get("id"),
                "mirror_timestamp": f"archived_at:{post.get('archived_at')}",
            },
        )

    # ------------------------------------------------------------------
    # derive_stable_external_id
    # ------------------------------------------------------------------

    def derive_stable_external_id(self, raw_item: Any) -> str:
        if isinstance(raw_item, dict):
            post_id = raw_item.get("id", "")
            return f"ts-{post_id}"
        if isinstance(raw_item, str):
            return "ts-" + hashlib.sha256(raw_item.encode("utf-8")).hexdigest()[:16]
        return "ts-" + hashlib.sha256(str(raw_item).encode("utf-8")).hexdigest()[:16]

    # ------------------------------------------------------------------
    # Internal parsers
    # ------------------------------------------------------------------

    def _parse_api_statuses(self, data: list[dict]) -> list[SourceIndexItem]:
        items: list[SourceIndexItem] = []
        for post in data:
            ext_id = self.derive_stable_external_id(post)
            url = post.get("url") or post.get("uri", "")
            content = post.get("content", "") or ""
            created = _parse_iso(post.get("created_at"))
            edited = _parse_iso(post.get("edited_at"))
            items.append(
                SourceIndexItem(
                    external_id=ext_id,
                    detail_url=HttpUrl(url),
                    title=_truncate_title(content),
                    published_at=created,
                    updated_at=edited,
                    metadata={"post_id": post.get("id"), "raw": post},
                )
            )
        return items

    def _parse_cnn_mirror(self, data: Any) -> list[SourceIndexItem]:
        posts = data if isinstance(data, list) else data.get("data", [])
        # The mirror is the full archive; keep only the most recent posts so a
        # single poll stays bounded.  Posts without a parseable timestamp sort
        # last (treated as oldest).
        _floor = datetime.min.replace(tzinfo=timezone.utc)

        def _sort_key(p: dict) -> datetime:
            dt = _parse_iso(p.get("created_at") or p.get("date"))
            if dt is None:
                return _floor
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt

        posts = sorted(posts, key=_sort_key, reverse=True)[:MAX_MIRROR_ITEMS]

        items: list[SourceIndexItem] = []
        for post in posts:
            ext_id = self.derive_stable_external_id(post)
            url = post.get("url") or post.get("permalink", "")
            content = post.get("text") or post.get("content", "")
            created = _parse_iso(post.get("created_at") or post.get("date"))
            items.append(
                SourceIndexItem(
                    external_id=ext_id,
                    detail_url=HttpUrl(url),
                    title=_truncate_title(content),
                    published_at=created,
                    updated_at=None,
                    # Preserve the full original post so fetch_detail/normalize
                    # can recover the complete text via normalize_cnn_mirror_post.
                    metadata={"post_id": post.get("id"), "source": "cnn_mirror", "raw": post},
                )
            )
        return items

    def _parse_text_listing(self, raw: str) -> list[SourceIndexItem]:
        # Minimal heuristic extraction from raw text when Playwright is used.
        # In production this would parse JSON-LD embedded in the page.
        items: list[SourceIndexItem] = []
        lines = [l for l in raw.split("\n") if l.strip()]
        for i, line in enumerate(lines[:50]):
            normalized_line = _normalize_playwright_line(line)
            # Reject counter-only lines (e.g. "45.2K", "12.3K", "1,234").
            if not normalized_line:
                continue
            if len(normalized_line) < 3:
                continue
            if _looks_like_engagement_counter(normalized_line):
                continue
            ext_id = f"ts-pw-{hashlib.sha256(normalized_line.encode()).hexdigest()[:16]}"
            items.append(
                SourceIndexItem(
                    external_id=ext_id,
                    detail_url=HttpUrl(TRUTH_SOCIAL_PROFILE_URL),
                    title=_truncate_title(normalized_line),
                    published_at=None,
                    updated_at=None,
                    metadata={
                        "source": "playwright",
                        "source_page_url": TRUTH_SOCIAL_PROFILE_URL,
                        "playwright_line": i,
                        "raw_line": line,
                        "normalized_line": normalized_line,
                    },
                )
            )
        return items

    def _extract_max_id(self, data: list[dict]) -> str | None:
        if not data:
            return None
        last = data[-1].get("id")
        return str(last) if last else None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt
    except (ValueError, TypeError):
        return None


def _truncate_title(text: str, max_len: int = 120) -> str:
    cleaned = text.strip()
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[:max_len].rsplit(" ", 1)[0] + "…"


def _normalize_playwright_line(text: str) -> str:
    cleaned = " ".join(text.split())
    cleaned = _PLAYWRIGHT_PREFIX_RE.sub("", cleaned)
    cleaned = _PLAYWRIGHT_RELATIVE_TIME_RE.sub(" ", cleaned)
    cleaned = _PLAYWRIGHT_TRAILING_COUNTERS_RE.sub("", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -·")
    return cleaned.strip()


def _looks_like_engagement_counter(text: str) -> bool:
    cleaned = " ".join(text.split())
    if not cleaned:
        return True
    if _PLAYWRIGHT_COUNTER_ONLY_RE.match(cleaned):
        return True
    return bool(_PLAYWRIGHT_COUNTER_SEQUENCE_RE.match(cleaned))


def resolve_truthsocial_source_url(
    canonical_url: str,
    metadata: dict[str, Any] | None = None,
) -> str:
    metadata = metadata or {}

    for key in ("detail_url", "source_page_url", "permalink", "url"):
        value = metadata.get(key)
        if isinstance(value, str) and value.startswith("http"):
            return value

    post_id = metadata.get("post_id")
    if isinstance(post_id, str) and post_id:
        return f"{TRUTH_SOCIAL_PROFILE_URL}/{post_id}"

    normalized = canonical_url.rstrip("/")
    if normalized == TRUTH_SOCIAL_API_BASE:
        return TRUTH_SOCIAL_PROFILE_URL
    return canonical_url


# Late import for type annotation used in __init__
import httpx  # noqa: E402, F811, PLC0415
