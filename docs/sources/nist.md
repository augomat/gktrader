# NIST Source Adapter

**Status**: MVP Implementation
**Source URL**: `https://www.nist.gov/news-events/news/rss.xml`
**Adapter class**: `NISTAdapter`

## Overview

Polls the NIST news RSS feed every 60 seconds. Fetches linked article
details and preserves program/category metadata such as CHIPS and
quantum context.

## Fetch Paths

| Path | Description |
|------|-------------|
| `rss` | RSS feed entry (summary + categories) |
| `rss_detail` | Full article HTML fetched from the detail URL |

## External ID Scheme

```
nist-<sha256(url-or-guid)[:16]>
```

Derived from the RSS `guid`, `id`, or `link` field.

## Category Metadata

NIST RSS includes `dc:subject` tags that capture program areas
(e.g. "Quantum", "CHIPS", "Cybersecurity", "Semiconductors").
These are preserved in `SourceIndexItem.metadata.categories` and
`NormalizedDocument.source_metadata.categories`.

## Conditional Requests

ETag and Last-Modified headers are propagated through `FetchIndexResult`.

## Versioning

Content hash changes (via updated RSS summary or different detail page
content) produce distinct immutable versions while preserving the
external ID.

## Test Fixtures

- `tests/fixtures/sources/nist_feed.xml` — representative RSS feed
  with 3 entries including CHIPS and Cybersecurity categories
- `tests/fixtures/sources/nist_article.html` — sample article detail
  page

## Contract Tests

- `tests/contract/sources/test_nist.py`