# White House Source Adapter

**Status**: MVP Implementation
**Source URL**: `https://www.whitehouse.gov/news/feed/`
**Adapter class**: `WhiteHouseAdapter`

## Overview

Polls the White House RSS feed every 60 seconds for new press releases,
briefings, and statements. When a new item is detected, fetches the linked
article detail page and normalises the content into the shared
`NormalizedDocument` schema.

## Fetch Paths

| Path | Description |
|------|-------------|
| `rss` | RSS feed entry (summary-only) |
| `rss_detail` | Full article HTML fetched from the detail URL |

## External ID Scheme

```
wh-<sha256(url-or-guid)[:16]>
```

Derived from the RSS `guid`, `id`, or `link` field. Stable across polls.

## Conditional Requests

ETag and Last-Modified headers from the RSS response are passed back
through `FetchIndexResult` for downstream use.

## Versioning

Content changes are detected by comparing the full `text` field (or the
summary when detail isn't fetched). External IDs remain stable; content
changes produce new immutable versions.

## Test Fixtures

- `tests/fixtures/sources/whitehouse_feed.xml` — representative RSS feed
  with 3 entries
- `tests/fixtures/sources/whitehouse_article.html` — sample article detail
  page

## Contract Tests

- `tests/contract/sources/test_whitehouse.py`

## Operational Notes

- The RSS feed is publicly accessible; no authentication required.
- Fails open with HTTP error propagation; upstream retry logic handles
  transient failures.
- Trafilatura extracts article text from detail pages.