# GKTrader Source Ingestion Fix Handoff

## Goal

Fix source ingestion so we get reasonable, actionable raw documents from all configured sources:

- `whitehouse`
- `nist`
- `sec_8k`
- `truthsocial`
- `commerce`

Do not broaden SEC to all EDGAR. For this project, keep using **SEC 8-K** as the targeted EDGAR source.

## Current Findings

Latest DB status showed:

| Source | Status | Problem |
|---|---|---|
| `commerce` | failing | Direct HTTP returns 403. Playwright/gkfetch path returns HTML with 0 press-release links. |
| `whitehouse` | polling succeeds | Stored docs have blank titles and missing `published_at`. |
| `nist` | polling succeeds | Stored docs have missing `published_at`, weak URL/title preservation. |
| `sec_8k` | polling succeeds | Stores 40 docs but all missing `published_at`; likely stores EDGAR index-page content, not actual filing docs. |
| `truthsocial` | active | Uses Playwright `index_fallback`; many rows lack timestamps and some rows are just engagement-count fragments. |

Representative DB quality query result:

```sql
select source_name,
       count(*) filter (where title = '') as blank_titles,
       count(*) filter (where published_at is null) as missing_published,
       count(*) as total
from raw_documents
group by source_name
order by source_name;
```

Observed result:

```text
source_name | blank_titles | missing_published | total
nist        | 40           | 40                | 40
sec_8k      | 0            | 40                | 40
truthsocial | 17           | 176               | 227
whitehouse  | 31           | 31                | 31
```

Recent poll status query:

```sql
select distinct on (source_name)
       source_name, status, fetch_path, fetch_count, new_count, ended_at, errors
from source_poll_runs
order by source_name, started_at desc;
```

Observed result:

```text
commerce    FAILED    unknown    0   0   All Commerce acquisition paths failed...
nist        SUCCEEDED rss        0   0
sec_8k      SUCCEEDED rss        40  0
truthsocial SUCCEEDED playwright 18  0
whitehouse  SUCCEEDED rss        0   0
```

Use Docker through `sg docker` because this shell may not have inherited the Docker group:

```bash
sg docker -c "docker compose -f compose.yaml -f compose.dev.yaml ps"
```

## Important Context

The pipeline currently does:

```python
index_result = adapter.fetch_index(...)
for item in index_result.items:
    raw = adapter.fetch_detail(item)
    doc = adapter.normalize(raw)
```

Relevant file: `src/gktrader/tasks/pipeline.py`

Main flaw: for HTML detail sources, `normalize(raw)` receives only the detail HTML string. It loses the original `SourceIndexItem`, so stored docs lose:

- stable `external_id`
- canonical article URL
- feed/listing title
- `published_at`
- `updated_at`
- source metadata such as RSS summary, categories, accession number, prefilter match

TruthSocial mostly works because its `fetch_detail()` preserves self-contained raw payloads.

## SEC Clarification

EDGAR is the full SEC filing system/database.

8-K is one filing type inside EDGAR. It is the "current report" companies file for material events.

For GKTrader's purpose, use `sec_8k`, not full EDGAR.

Reason:

- 8-K is timely and event-driven.
- Full EDGAR is too noisy: 10-K, 10-Q, S-1, Form 4, 13F, proxy filings, amendments, etc.
- We want event/policy catalysts, not broad filing ingestion.

Do not rename `sec_8k` to `edgar`. Do not ingest all EDGAR forms in this fix.

## Files To Inspect And Likely Edit

Source adapters:

- `src/gktrader/sources/whitehouse.py`
- `src/gktrader/sources/nist.py`
- `src/gktrader/sources/sec.py`
- `src/gktrader/sources/commerce.py`
- `src/gktrader/sources/truthsocial.py`
- `src/gktrader/sources/base.py`

Pipeline:

- `src/gktrader/tasks/pipeline.py`

Contracts:

- `src/gktrader/domain/contracts.py`

Tests:

- `tests/contract/sources/test_whitehouse.py`
- `tests/contract/sources/test_nist.py`
- `tests/contract/sources/test_sec.py`
- `tests/contract/sources/test_commerce.py`
- `tests/contract/sources/test_truthsocial.py`
- `tests/unit/test_tasks.py`

## Fix 1: Preserve Index Context Through Detail Normalization

Implement the smallest viable change.

Recommended approach:

- Keep `SourceAdapter.fetch_detail()` return type as `Any`.
- For HTML-detail adapters, return a dict containing both the index item and detail HTML.
- Teach `normalize()` to handle this dict.

Example shape:

```python
{
    "item": item,
    "html": resp.text,
}
```

Apply to:

- `WhiteHouseAdapter.fetch_detail`
- `NISTAdapter.fetch_detail`
- `SECAdapter.fetch_detail`
- `CommerceAdapter.fetch_detail`

For WhiteHouse/NIST/Commerce detail normalization, preserve:

- `external_id=item.external_id`
- `canonical_url=item.detail_url`
- `title=item.title`
- `published_at=item.published_at`
- `updated_at=item.updated_at`
- `source_metadata=dict(item.metadata)`, plus detail-specific fields
- extracted detail text from HTML

Expected result:

- WhiteHouse docs should not have blank titles.
- WhiteHouse/NIST docs should retain feed timestamps.
- Detail text should still be full article text, not only RSS summary.

## Fix 2: WhiteHouse

Current problem:

- `whitehouse.py:_normalize_html_detail()` creates content-hash IDs like `wh-detail-...`.
- It sets canonical URL to `https://www.whitehouse.gov/news/`.
- It sets title to `""`.
- It drops `published_at`.

Fix:

- Add a path for wrapped detail payloads.
- Keep `_normalize_entry()` for tests/lightweight use.
- Keep HTML extraction via `trafilatura`.
- Preserve feed item metadata.

Acceptance:

- New WhiteHouse raw docs have non-empty `title`.
- New WhiteHouse raw docs have article-specific `canonical_url`.
- New WhiteHouse raw docs have `published_at` when feed provides it.
- `external_id` starts with stable `wh-`, not `wh-detail-`, for normal pipeline detail ingestion.

## Fix 3: NIST

Current problem:

- Same as WhiteHouse.
- `nist.py:_normalize_html_detail()` creates `nist-detail-...`.
- It sets generic canonical URL.
- It drops title/date/categories.

Fix:

- Same wrapped detail approach.
- Preserve categories from item metadata.
- Keep full detail text extraction.

Acceptance:

- New NIST raw docs have article-specific URLs.
- New NIST raw docs have titles.
- New NIST raw docs preserve `published_at`.
- `source_metadata.categories` survives.

## Fix 4: SEC 8-K

Current problem:

- `SECAdapter.fetch_index()` fetches current 8-K Atom feed.
- `SECAdapter.fetch_detail()` fetches the EDGAR filing index URL.
- Stored text is likely the filing index page, not the actual 8-K filing document.
- `prefilter_match` is computed but not used to skip noisy items before detail/classification.

Desired behavior:

- Keep source as `sec_8k`.
- Fetch actual primary filing document from the EDGAR filing index page.
- Preserve Atom feed title, URL, published/updated timestamps, accession metadata.
- Apply deterministic prefilter before expensive processing.

Implementation guidance:

- In `fetch_detail(item)`, fetch `item.detail_url` index page.
- Parse the filing index page with BeautifulSoup.
- Find the primary document link from the document table.
- Prefer a row where document type is `8-K` or `8-K/A`.
- Fetch the actual document URL.
- Return wrapped payload with `item`, `index_url`, `filing_url`, and `html`.
- In normalize, preserve item metadata and use full filing text.
- Store `source_metadata.filing_url`, `source_metadata.index_url`, `source_metadata.accession_number`, `source_metadata.prefilter_match`.

Prefilter options:

- Minimal: in pipeline, before `fetch_detail`, skip SEC items where `item.metadata["prefilter_match"] is False`.
- Better: add adapter-level method or metadata convention, but keep changes small.

Be careful:

- Some useful 8-K titles may be generic, e.g. "8-K - Company".
- Current `PREFILTER_KEYWORDS` includes many item numbers, making the prefilter broad.
- Do not over-tighten in this task.

Acceptance:

- New SEC raw docs have `published_at`.
- New SEC raw docs have `canonical_url` pointing to the filing document or index page with `filing_url` in metadata.
- New SEC raw text contains actual filing contents, not only EDGAR filing index navigation/table text.
- No all-EDGAR expansion.

## Fix 5: TruthSocial

Current problem:

- DB shows current `fetch_path=index_fallback`, sourced through Playwright.
- Some stored rows are just engagement counters like `842 767 2.86k`.
- Many rows lack `published_at`.
- Playwright-derived IDs are content-hash based and may create duplicates as engagement counts change.

Recommended minimal fix:

- Improve `_parse_text_listing()` and `_normalize_playwright_line()`.
- Skip lines that are only engagement counters or mostly numbers.
- Strip trailing engagement counters from otherwise valid post text.
- Avoid storing very short low-information lines.
- Prefer CNN mirror when Playwright text quality is poor, or make Playwright parser stricter so fallback rows do not pollute DB.

Relevant file:

- `src/gktrader/sources/truthsocial.py`

Likely helper:

```python
def _looks_like_engagement_counter(text: str) -> bool:
    ...
```

Acceptance:

- New TruthSocial docs should not have titles/text that are just counters.
- Repeated polling should not create new rows just because engagement counts changed.
- If CNN mirror is used, `published_at` should be populated from `created_at`.

## Fix 6: Commerce

Current problem:

- Direct HTTP to `https://www.commerce.gov/news/press-releases` returns 403.
- Common alternatives also returned 403: robots, sitemap, RSS guesses, pagination.
- Current error from DB:

```text
All Commerce acquisition paths failed
http: 403 Forbidden
playwright: Commerce playwright fetch returned HTML with 0 press-release links
```

Additional issue:

- Even if Playwright/gkfetch listing works, `CommerceAdapter.fetch_detail()` currently does direct HTTP only, so detail fetch likely fails with 403 too.

Recommended fix:

- First verify `GKTRADER_GKFETCH_URL` and `GKTRADER_GKFETCH_SECRET` are configured in the worker container.
- If gkfetch is configured, inspect what HTML it returns for Commerce.
- Update Commerce detail fetching to use the same acquisition path as listing when direct HTTP is blocked.
- If listing comes from gkfetch/browser, fetch detail with gkfetch/browser too.
- Ensure parser selectors match current Commerce HTML.

Implementation detail:

- Add `source_metadata["fetch_path"]` or similar to `SourceIndexItem.metadata` when listing is parsed.
- In `fetch_detail(item)`, try direct HTTP first, then `_remote_fetch(str(item.detail_url))`.
- Return wrapped detail payload with item and HTML.
- Normalize using stable listing ID and listing title/date if available.

Acceptance:

- Commerce latest poll is `SUCCEEDED`.
- `fetch_count > 0`.
- At least one `raw_documents` row exists for `commerce`.
- Commerce docs have non-empty title, detail URL, and useful text.

## Tests To Add Or Update

Add tests proving detail normalization preserves index metadata.

WhiteHouse:

- `fetch_detail()` returns wrapped payload.
- `normalize(wrapped)` preserves `item.external_id`.
- `normalize(wrapped)` preserves `item.detail_url`.
- `normalize(wrapped)` preserves `item.title`.
- `normalize(wrapped)` preserves `item.published_at`.

NIST:

- Same as WhiteHouse.
- Assert categories are preserved.

SEC:

- Fixture for EDGAR filing index page with primary document link.
- Fixture for actual 8-K document HTML.
- Test `fetch_detail()` fetches both index and primary document.
- Test normalized doc has feed title/date and actual filing text.
- Test prefilter skip behavior if implemented in pipeline.

Commerce:

- Test fallback detail fetch via `_remote_fetch` when direct HTTP fails.
- Test wrapped detail normalization preserves listing metadata.

TruthSocial:

- Test engagement-counter-only lines are skipped.
- Test trailing counters are stripped or do not affect stable post identity.
- Test very short numeric fragments do not produce `SourceIndexItem`.

Pipeline:

- Add a unit test with fake adapter returning `SourceIndexItem` plus detail HTML.
- Assert resulting `RawDocument` keeps index external ID, title, canonical URL, and published timestamp.

## Commands For Verification

Run tests:

```bash
sg docker -c "docker compose -f compose.yaml -f compose.dev.yaml run --rm worker pytest tests/contract/sources tests/unit/test_tasks.py"
```

If local Python environment is available, this may also work:

```bash
pytest tests/contract/sources tests/unit/test_tasks.py
```

Restart services after code changes:

```bash
./gkt-restart.sh
```

If scheduler behavior changed:

```bash
INCLUDE_SCHEDULER=1 ./gkt-restart.sh
```

Check source health after restart:

```bash
sg docker -c "docker exec gktrader-postgres-1 psql -U gktrader -d gktrader -c \"select distinct on (source_name) source_name, status, fetch_path, fetch_count, new_count, ended_at, errors from source_poll_runs order by source_name, started_at desc;\""
```

Check raw document quality:

```bash
sg docker -c "docker exec gktrader-postgres-1 psql -U gktrader -d gktrader -c \"select source_name, count(*) filter (where title = '') as blank_titles, count(*) filter (where published_at is null) as missing_published, count(*) as total from raw_documents group by source_name order by source_name;\""
```

Inspect latest rows:

```bash
sg docker -c "docker exec gktrader-postgres-1 psql -U gktrader -d gktrader -c \"select source_name, fetch_path, left(external_id,45) as external_id, left(canonical_url,120) as url, left(title,120) as title, length(text) as text_len, published_at, created_at from raw_documents order by created_at desc limit 30;\""
```

## Final Acceptance Criteria

A successful fix should satisfy:

- WhiteHouse, NIST, SEC, and TruthSocial continue polling without regression.
- Commerce either polls successfully or has a clearly documented blocker with captured returned HTML/problem.
- New WhiteHouse docs have non-empty title, article URL, stable feed ID, and published timestamp.
- New NIST docs have non-empty title, article URL, stable feed ID, published timestamp, and categories.
- New SEC docs are actual 8-K filing content, not only EDGAR index page content.
- New TruthSocial docs are not counter-only fragments.
- Tests cover metadata preservation through detail fetch and normalize.
- Do not ingest full EDGAR.
- Do not add broad backward-compatibility migrations unless required.

## What To Return For Review

Return:

- List of files changed.
- Summary of behavioral changes per source.
- Test commands run and full pass/fail result.
- Latest DB query output for source poll status.
- Latest DB query output for raw document quality.
- Any remaining source-specific blockers, especially Commerce.
