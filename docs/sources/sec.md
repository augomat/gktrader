# SEC EDGAR 8-K Source Adapter

**Status**: MVP Implementation
**Source URL**: `https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&output=atom`
**Adapter class**: `SECAdapter`

## Overview

Polls the SEC EDGAR current 8-K feed every 60 seconds. Parses filing
detail pages, applies a deterministic keyword prefilter, and provides
a company ticker master parsing helper.

## Endpoints

| Endpoint | Usage |
|----------|-------|
| `https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&output=atom` | Current 8-K Atom feed |
| `https://www.sec.gov/files/company_tickers.json` | Company ticker/CIK master (fetched separately) |
| `https://data.sec.gov/submissions/CIK##########.json` | Company submissions (detail lookups) |

## Fetch Paths

| Path | Description |
|------|-------------|
| `rss` | Atom feed entry |
| `filing_detail` | Filing detail HTML |
| `submissions_api` | Structured submissions API response |

## External ID Scheme

```
sec-8k-<accession_number>
```

When an accession number can be extracted from the SEC EDGAR URL,
it is used directly. Otherwise falls back to:

```
sec-<sha256(link)[:16]>
```

## Keyword Prefilter

Before invoking the LLM, apply the deterministic prefilter. Filings
must match at least one keyword in the title or summary:

Key terms include: `contract`, `award`, `grant`, `loan`, `funding`,
`warrant`, `government`, `federal`, `agency`, `tariff`, `trade`,
`sanction`, `investigation`, `cancellation`, `termination`,
`material definitive`, SEC filing item numbers (e.g. `1.01`, `2.03`).

Use `SECAdapter.matches_prefilter(title, summary)` to check.

## SEC User-Agent

Always send an identifying User-Agent string (default:
`GKTrader/0.1 (contact@gktrader.example.com)`). Configure via the
`user_agent` constructor parameter. The SEC requires this for API
access.

## Rate Limiting

The adapter enforces a minimum 0.15s interval between requests
(≈6.7 req/s) to stay below the SEC's 10 requests/second guideline.
The `_rate_limit()` method is called before every HTTP request.

## Ticker Master

Use `SECAdapter.parse_ticker_master(data)` to parse the SEC company
tickers JSON. Returns a list of dicts with keys: `cik`, `cik_padded`,
`ticker`, `name`. CIK values are zero-padded to 10 digits via
`pad_cik()`.

Use `SECAdapter.fetch_ticker_master(client, user_agent)` for a
convenience method that fetches and parses in one call.

Daily refresh is recommended for the ticker master data.

## Versioning

Content changes in the feed summary or filing detail produce new
immutable versions with the same external ID. Filing amendments
and corrections are detected this way.

## Test Fixtures

- `tests/fixtures/sources/sec_8k_feed.xml` — 3-entry Atom feed:
  2 government-relevant filings + 1 unrelated
- `tests/fixtures/sources/sec_company_tickers.json` — 9-company
  ticker master sample
- `tests/fixtures/sources/sec_filing_detail.html` — Sample 8-K
  filing HTML with contract award content

## Contract Tests

- `tests/contract/sources/test_sec.py`