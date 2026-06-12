# GKTrader Trump/Admin Event Signal Agent

## Implementation Plan and Agent Handoff

Status: concept approved, implementation not started  
Deployment target: existing VPS at `/home/openclaw/gktrader`  
Primary user interface: new dedicated private Telegram bot through the existing OpenClaw installation  
Primary language: English alerts and bot responses  
Timezone for user-facing schedules: `Europe/Vienna`

This document is the implementation contract for the agents building GKTrader. It intentionally contains no implementation. Agents must preserve the safety constraints, module boundaries, and acceptance criteria defined here.

---

## 1. Product Objective

Build a conservative, auditable event-signal system that:

1. Monitors public Trump, White House, US agency, and SEC sources.
2. Detects events involving named publicly traded US companies.
3. Distinguishes positive, negative, neutral, and unclear contexts.
4. Validates company-to-ticker mappings deterministically.
5. Scores catalyst strength and checks whether the market has already moved.
6. Sends immediate, decision-oriented Telegram alerts for actionable events.
7. Lets the user record whether a trade was made and in what amount.
8. Tracks earlier bullish signals and includes all of them in later bearish alerts.
9. Stores complete evidence and outcomes for later performance evaluation.

The system is decision support only. It must never place, prepare, or automate real broker orders.

### Success Definition

The system succeeds when it surfaces high-quality public policy and political catalysts early, explains why they matter, avoids ticker mistakes and duplicate alerts, and produces enough auditable outcome data to determine whether the strategy has a real edge.

### Precision Policy

Optimize for high precision before high recall:

- Capture uncertain events internally.
- Fail closed when classification, mapping, or market context is uncertain.
- Downgrade uncertain events to `REVIEW`.
- Never upgrade an event based only on weak market data.
- Never send `WATCH` events to Telegram.

---

## 2. Confirmed Product Decisions

These decisions are fixed for MVP implementation unless the owner explicitly changes them.

| Area | Decision |
|---|---|
| Telegram surface | New dedicated private Telegram bot |
| Telegram integration | Existing OpenClaw handles inbound messages and button callbacks |
| Alert delivery | Immediate |
| Alert language | English |
| Buttons | Every delivered alert includes contextual buttons |
| Telegram `WATCH` alerts | Never send; store internally only |
| Bearish alerts | Send them and include every earlier bullish signal for that company |
| Trade follow-up | Ask whether a deal was made, then ask amount and optional execution price |
| Weekly review | Sunday at 14:00 `Europe/Vienna`, including open-position confirmation |
| Source polling interval | 60 seconds for all MVP source adapters |
| Same ticker/event cooldown | 6 hours, with material-update overrides |
| Truth Social | Fastest feasible free local watcher; CNN data mirror as fallback |
| Market data | Free Alpaca IEX data first |
| Market-data behavior | Can only downgrade actionability, never promote it |
| Extreme price moves | Do not suppress; send `AVOID_CHASE` where appropriate |
| Paper notionals | `REVIEW`: EUR 500, `TRADEABLE`: EUR 1,000, all others: EUR 0 |
| LLM provider | OpenRouter with strict structured output |
| Initial sectors | No sector filtering |
| Initial source scope | White House, Truth Social, Commerce, NIST, SEC/8-K |
| Deployment | Docker Compose on this VPS |
| Broker integration | None |

### Concept Questions Closed

There are no remaining concept-level blockers. Implementation-time setup will still require secrets and identifiers, including the dedicated Telegram bot token, owner Telegram numeric ID, OpenRouter API key, Alpaca paper credentials, and an identifying SEC User-Agent string.

---

## 3. Hard Safety and Scope Boundaries

### Must Have

- Telegram alerts with source URL, evidence, rationale, score, direction, and market context.
- Immutable raw-document and processing audit trail.
- Deterministic ticker validation independent of the LLM.
- Idempotent ingestion and alert delivery.
- Source-specific backoff, health reporting, and failure isolation.
- Human-confirmed trade and position ledger.
- Paper performance measurements.
- Restart safety.

### Explicitly Forbidden

- Auto-trading or order placement.
- Broker API integration in MVP.
- Browser automation against Trade Republic, flatex, Revolut, or any broker.
- Market orders.
- Unofficial broker APIs using real money.
- LLM-only ticker decisions.
- High-confidence alerts without evidence and source URL.
- An OpenClaw tool that can execute shell commands, access brokers, or place trades.
- A second Telegram `getUpdates` poller for the dedicated bot token.

### Deferred Scope

- DOE, Defense/War contracts, OGE, USAspending, and company IR adapters.
- Licensed secondary news sources.
- Web dashboard.
- Backtest UI.
- IBKR market data, paper trading, or execution.
- Broker availability/routing table.
- Tax reports.

The MVP architecture must make deferred source adapters straightforward to add without changing core domain contracts.

---

## 4. Researched Constraints and Reuse Decisions

### Existing Host and OpenClaw

The VPS already runs OpenClaw from `/home/openclaw/src/openclaw`, with its gateway on port `18789`.

Reuse these existing OpenClaw capabilities:

- Multiple Telegram accounts and account-to-agent bindings.
- Dedicated agents and workspaces.
- Telegram inline buttons and callback handling.
- Custom TypeScript tool plugins through the OpenClaw plugin SDK.
- OpenRouter model references.
- Telegram long polling.

Important Telegram constraint: only one process may poll `getUpdates` for a bot token. OpenClaw is the exclusive inbound poller. The Python backend may use Telegram `sendMessage` with the same token, but must never call `getUpdates` or configure a webhook.

### VPS Deployment Prerequisite

Docker is not currently available in the host `PATH`. Before deployment, install Docker Engine and the Docker Compose plugin. This is an operational prerequisite, not part of application implementation.

### Source Reachability Findings

| Source | Verified path and constraint |
|---|---|
| White House | RSS works at `https://www.whitehouse.gov/news/feed/` |
| NIST | RSS works at `https://www.nist.gov/news-events/news/rss.xml` |
| SEC | APIs work with an identifying User-Agent; respect maximum 10 requests/second |
| Commerce | Direct requests currently encounter Cloudflare `403` from this VPS |
| Truth Social | Direct API currently encounters Cloudflare `403` from this VPS |
| CNN Truth mirror | Free fallback archive updates approximately every five minutes |

### Truth Social Free Strategy

Use a tiered acquisition strategy and record the successful fetch path on every document:

1. Direct Mastodon-compatible Truth Social account/status API.
2. Local Playwright browser with a persistent session.
3. CNN mirror at `https://ix.cnn.io/data/truth-social/truth_archive.json`.

Do not implement CAPTCHA solving, paid proxies, rotating residential proxies, or access-control bypasses. When the fallback mirror is used, alerts must show the fallback path and resulting latency.

### Alpaca IEX Decision

Use an Alpaca paper-only account and free IEX market data. Paper-only access is generally available internationally, including to Austrian users, and does not require a funded US brokerage account. Account availability and terms may change, so the provider adapter must fail gracefully if credentials cannot be created or retained.

IEX represents only a small portion of total US equity trading. Therefore:

- Label all market context and measurements as `IEX partial-market data`.
- Treat price movement as approximate context.
- Do not treat IEX volume or liquidity as authoritative.
- Market data may downgrade an event, but may never promote one.
- Missing market data forces an otherwise high-quality event to `REVIEW`.

---

## 5. System Architecture

Use a modular Python monolith backed by PostgreSQL and Redis. Do not introduce microservices or an agent framework for the deterministic backend.

```text
MVP Source Adapters
  -> Fetch and Version Raw Documents
  -> Normalize and Extract Text
  -> Structured LLM Classification
  -> Deterministic Company/Ticker Resolution
  -> Canonical Event, Dedupe, and Cooldown
  -> Catalyst Score and Actionability
  -> Alpaca IEX Market Snapshot
  -> Deterministic Alert Rendering
  -> Transactional Alert Outbox
  -> Telegram Bot API

Telegram User / Buttons
  -> Existing OpenClaw Telegram Poller
  -> Dedicated GKTrader OpenClaw Agent
  -> Restricted GKTrader Tool Plugin
  -> Internal FastAPI
  -> Trade Decisions and Position Ledger

Scheduler
  -> Performance Snapshots
  -> Sunday Weekly Report and Position Confirmation
  -> Source and System Health Checks
```

### Docker Compose Services

| Service | Responsibility |
|---|---|
| `api` | Internal FastAPI used by the OpenClaw plugin and health checks |
| `worker` | Celery tasks for ingestion, processing, delivery, and snapshots |
| `scheduler` | Celery Beat schedules source polls, snapshots, health checks, and weekly reviews |
| `postgres` | Durable source, event, alert, position, and performance state |
| `redis` | Celery broker, short-lived locks, cooldown cache, and rate-limit coordination |

OpenClaw remains a host service outside Docker Compose.

Bind the internal API to loopback only, for example `127.0.0.1:8787:8000`. Require a shared-secret authorization header even on loopback.

### Architectural Principles

- PostgreSQL is the source of truth.
- Redis is never the only copy of durable state.
- Source ingestion, interpretation, market context, and delivery remain separate stages.
- Raw source versions are immutable.
- Critical Telegram alert facts are rendered deterministically by Python, not composed by the OpenClaw conversational LLM.
- Every stage records status, timestamps, errors, and correlation identifiers.
- Shared domain contracts are defined before parallel implementation begins and changed only by the integration lead.

---

## 6. Proposed Repository Layout

```text
/
  IMPLEMENTATION_PLAN.md
  README.md
  pyproject.toml
  uv.lock
  compose.yaml
  .env.example
  alembic.ini

  src/gktrader/
    api/
    alerts/
    config/
    db/
    domain/
    intelligence/
    marketdata/
    reporting/
    sources/
    tasks/

  migrations/

  integrations/openclaw-gktrader/
    src/
    skill/
    tests/
    README.md

  tests/
    fixtures/
    unit/
    contract/
    integration/
    e2e/

  docs/
    operations/
    sources/
    decisions/

  scripts/
```

### Recommended Core Libraries

| Area | Library |
|---|---|
| Runtime and packaging | Python 3.12+, `uv` |
| HTTP | `httpx` |
| RSS | `feedparser` |
| HTML extraction | `trafilatura`, BeautifulSoup |
| Browser fallback | Playwright |
| PDF later | `pypdf`, `pdfplumber` |
| Models and validation | Pydantic v2 |
| API | FastAPI |
| Database | SQLAlchemy 2, Alembic, PostgreSQL |
| Tasks | Celery, Redis |
| Logging | `structlog` |
| Trading calendars | `exchange_calendars` |
| SEC parsing/reference | `edgartools` where useful |
| Testing | `pytest`, `pytest-asyncio`, `respx` |

Do not use LangChain or another agent framework in the Python backend.

---

## 7. Domain Contracts

Define domain enums and Pydantic contracts during Wave 0. They are shared interfaces and must be treated as locked contracts after integration-lead approval.

### Event Types

```text
presidential_positive_mention
presidential_negative_mention
government_funding
government_equity_stake
government_contract
regulatory_tailwind
regulatory_headwind
oge_purchase_disclosure
oge_sale_disclosure
company_confirmation_8k
sector_only_mention
irrelevant
```

The OGE types are defined now but not ingested in MVP.

### Direction

```text
bullish
bearish
neutral
unclear
```

### Alert Levels

```text
WATCH
REVIEW
TRADEABLE
AVOID_CHASE
IGNORE
```

`WATCH` is persisted but never delivered to Telegram.

### Required Classifier Output

The OpenRouter classifier must return strict JSON matching a versioned schema:

```json
{
  "relevant": true,
  "event_type": "government_funding",
  "direction": "bullish",
  "strength": 5,
  "confidence": 0.88,
  "companies": [
    {
      "name": "Example Corporation"
    }
  ],
  "rationale": "Concise source-grounded explanation.",
  "risks": ["Known uncertainty"],
  "action_status": "announced",
  "monetary_amounts": [],
  "award_or_contract_ids": [],
  "government_actors": [],
  "evidence": [
    {
      "text": "Short source excerpt",
      "start_offset": 100,
      "end_offset": 130
    }
  ]
}
```

Do not accept a ticker from the LLM as validated data. If a model returns one, discard it before ticker resolution.

### Source Adapter Contract

Every source adapter must expose the same behavior:

```text
source_name
source_tier
poll_interval_seconds
fetch_index(cursor, conditional_headers)
fetch_detail(item)
normalize(raw_item)
derive_stable_external_id(raw_item)
```

Normalized documents must include:

- Stable external ID.
- Canonical source URL.
- Title.
- Published and updated timestamps when available.
- Detected timestamp.
- Plain text.
- Original payload or HTML reference.
- Source-specific metadata.
- Fetch path, such as `rss`, `direct_api`, `playwright`, or `cnn_mirror`.
- ETag and Last-Modified values when available.

---

## 8. Persistence Model

Use normalized relational tables for core relationships and JSONB only for source-specific or provider-specific metadata. Add foreign keys, check constraints, and indexes in migrations.

### Source and Ingestion Tables

| Table | Purpose and required fields |
|---|---|
| `source_definitions` | Source name, tier, enabled state, poll interval, health thresholds |
| `source_cursors` | Durable cursor, ETag, Last-Modified, last successful poll |
| `source_poll_runs` | Start/end, status, fetch count, new count, errors, fetch path |
| `raw_documents` | Immutable source document versions, content hash, timestamps, source metadata |

Required raw-document uniqueness:

- Unique `(source_name, external_id, content_hash)`.
- Index by `detected_at`, `published_at`, and `source_name`.
- Preserve revised versions instead of overwriting previous content.

### Intelligence and Company Tables

| Table | Purpose |
|---|---|
| `processing_runs` | Classifier model, prompt version/hash, raw response, parsed result, tokens, cost, status, error |
| `companies` | Validated company identity, normalized name, CIK, ticker, exchange, active/public status |
| `company_aliases` | Deterministic aliases, provenance, review state |
| `extracted_events` | Per-document classifier output |
| `event_companies` | Event-to-company candidates and mapping confidence |
| `signal_events` | Canonical event representing one real-world catalyst |
| `event_evidence` | Source-grounded excerpts and references attached to canonical events |

### Market, Alert, and Delivery Tables

| Table | Purpose |
|---|---|
| `market_snapshots` | Provider/feed, observed price, previous close, move, market status, quality flags |
| `alerts` | Final level, rendered payload, score components, dedupe key, market snapshot |
| `alert_outbox` | Transactional delivery jobs and idempotency state |
| `alert_deliveries` | Telegram request/result, message ID, timestamps, known/unknown delivery state |

### User Interaction and Position Tables

| Table | Purpose |
|---|---|
| `trade_decisions` | Immutable decision made in response to an alert |
| `position_events` | Immutable open, increase, reduce, close, or confirm events |
| `positions` | Current projected position state derived from position events |
| `interaction_states` | Pending questions such as requested EUR amount |
| `user_feedback` | False-positive or free-form review feedback |
| `weekly_reports` | Generated weekly report and delivery state |

### Paper and Performance Tables

| Table | Purpose |
|---|---|
| `paper_trades` | Directional paper entry and notional rule |
| `performance_snapshots` | Return, drawdown, and runup measurements by horizon |

Do not create a broker-instruments table in MVP.

---

## 9. Ingestion and Source Plan

### Shared Ingestion Rules

- Poll each enabled MVP source every 60 seconds.
- Use conditional HTTP requests with ETag and Last-Modified where supported.
- Use a descriptive, configurable User-Agent.
- Apply source-specific retries, exponential backoff, and jitter.
- Acquire one short Redis lock per source poll.
- Treat PostgreSQL unique constraints as final idempotency protection.
- Record `published_at`, `detected_at`, and calculated source latency.
- On first startup, baseline currently available feed items without sending Telegram alerts.
- Allow alerts during replay/backfill only through an explicit administrative flag.
- Treat first-seen documents older than 24 hours as stale and internal-only by default.

### White House Adapter

Primary path: `https://www.whitehouse.gov/news/feed/`

Implementation responsibilities:

- Poll RSS.
- Fetch linked article details.
- Preserve article type and category when available.
- Detect changed versions by content hash.
- Pass all new relevant documents to the classifier; do not use sector filters.

### Truth Social Adapter

Target account: `@realDonaldTrump`.

Acquisition order:

1. Direct Mastodon-compatible API.
2. Local Playwright persistent browser session.
3. CNN mirror.

Responsibilities:

- Keep separate cursors per acquisition path.
- Normalize post IDs, creation time, edited time, text, links, and media descriptions.
- Deduplicate the same post across all paths.
- Prefer the earliest detected version.
- Store which path succeeded.
- Clearly show fallback latency in downstream alerts.
- Mark the source degraded when only the mirror is available, but continue processing.

### Commerce Adapter

Primary target: Commerce press releases.

Acquisition order:

1. Normal HTTP/RSS/index fetch when available.
2. Local Playwright persistent browser fallback.

Responsibilities:

- Extract release listing and detail pages.
- Avoid CAPTCHA solving or proxy bypass.
- Mark the source degraded if neither path succeeds.
- Emit one degradation notification and one recovery notification, not repeated spam.

### NIST Adapter

Primary path: `https://www.nist.gov/news-events/news/rss.xml`

Responsibilities:

- Poll RSS and fetch details.
- Preserve program/category metadata such as CHIPS or quantum context where present.
- Pass all new documents through the standard classifier.

### SEC/8-K Adapter

Primary endpoints:

- Current 8-K feed: `https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&output=atom`
- Company ticker master: `https://www.sec.gov/files/company_tickers.json`
- Company submissions: `https://data.sec.gov/submissions/CIK##########.json`

Responsibilities:

- Always use a declared identifying SEC User-Agent.
- Stay below SEC's 10 requests/second guideline across workers.
- Poll the current 8-K feed.
- Fetch and parse the primary filing document.
- Use a deterministic local keyword prefilter for government contracts, awards, grants, loans, funding, warrants, stakes, cancellations, and investigations before invoking the LLM.
- Store accession number and relevant filing items.
- Refresh SEC ticker/CIK data daily.

### Future Adapter Interfaces

Prepare source definitions and fixtures, but do not implement these in MVP:

- DOE: `https://www.energy.gov/rss/energygov/2193718`
- Defense/War contracts RSS: `https://www.war.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=400&Site=945&Category=Contracts`
- OGE REST: `https://extapps2.oge.gov/201/Presiden.nsf/API.xsp/v2/rest`
- USAspending API: `https://api.usaspending.gov/`
- Company investor-relations feeds.
- Licensed secondary confirmation sources.

---

## 10. Classification and Ticker Resolution

### OpenRouter Classifier

Recommended configurable defaults:

```text
Primary: google/gemini-3.1-flash-lite
Fallback: google/gemini-2.5-flash-lite
Temperature: 0
Response mode: strict Structured Outputs
```

The exact model must be an environment setting, not hard-coded.

Classifier execution rules:

1. Submit normalized title, source metadata, and text with a versioned system prompt.
2. Validate response against the strict Pydantic schema.
3. Retry once with a repair instruction after an invalid response.
4. Fail closed after the retry.
5. Store model, prompt version/hash, raw response, parsed result, token usage, estimated cost, and error.
6. Require evidence snippets tied to source text.
7. Never let the model approve or persist ticker aliases.

### Deterministic Company and Ticker Resolution

Resolution order:

1. Exact normalized alias match.
2. Exact SEC legal-name or known former-name match.
3. Validated curated alias match.
4. Candidate generation through conservative fuzzy matching.
5. Human review for unresolved or ambiguous candidates.

Validation sources:

- SEC company ticker/CIK master.
- Alpaca active US-listed assets where available.
- Curated, provenance-tracked alias table.

Rules:

- A fuzzy result may produce a candidate but may not auto-approve a mapping below threshold.
- ADRs are allowed only when validated as active US-listed instruments.
- Private or non-US-unlisted companies are stored but cannot become `TRADEABLE`.
- LLM suggestions cannot create aliases or approve tickers.
- Mapping confidence below `0.90` can never become `TRADEABLE`.

---

## 11. Scoring, Direction, and Actionability

Do not use one signed score for both importance and direction. A strong bearish event is still a strong catalyst.

Store these separately:

- `direction`: bullish, bearish, neutral, unclear.
- `catalyst_score`: event and source importance.
- `actionability`: final alert level.
- Individual score components and modifiers.

### Base Catalyst Score

| Score | Event |
|---|---|
| `5` | Explicit government equity stake, major grant, loan, contract, cancellation, or direct capital flow |
| `4` | Official agency names a concrete company in a strategic, funding, enforcement, or regulatory context |
| `3` | Direct Trump company mention without concrete money flow |
| `3` | Delayed OGE purchase or sale disclosure, future scope |
| `2` | Company 8-K confirms relevant government award or contract |
| `1` | Sector-only mention with no specifically named public company |

### Modifiers

| Modifier | Condition |
|---|---|
| `+1` | Multiple independent official sources confirm the same event |
| `+1` | Direct official source adds materially concrete action or amount |
| `-1` | Mapping confidence below `0.90` |
| `-1` | Event is stale or likely recycled |
| `-2` | Source is secondary-only or fetch path has material delay |
| `-2` | Stock already moved more than `20%` intraday |
| `-3` | Stock already moved more than `40%` intraday |

Store market-move penalties as actionability modifiers. Do not reduce the historical record of the catalyst's inherent strength.

### Minimum `TRADEABLE` Gate

All conditions must be true:

- Validated active public ticker.
- Mapping confidence at least `0.90`.
- Classifier confidence at least `0.80`.
- Direct Truth Social post or Tier 1 official source.
- Direction is bullish or bearish.
- Catalyst score at least `5`.
- Market snapshot is available.
- Event is not stale or recycled.

### Alert-Level Rules

- `WATCH`: useful internal event, low actionability. Persist only.
- `REVIEW`: human inspection required; includes ambiguity, missing market data, or medium-strength events.
- `TRADEABLE`: strong event that passes every gate.
- `AVOID_CHASE`: strong event where the move appears substantially extended. Still send it.
- `IGNORE`: irrelevant, exact duplicate, stale without material change, or unsafe mapping.

Price context may downgrade `TRADEABLE` to `REVIEW` or `AVOID_CHASE`. It may never promote an event.

### Bearish-History Requirement

For every delivered bearish alert:

- Query every prior bullish canonical signal for the same validated company.
- Include source date, event type, alert level, and one-line rationale for each.
- Do not truncate history silently.
- If the Telegram message limit is exceeded, send numbered continuation messages linked to the same alert.

---

## 12. Dedupe, Cooldown, and Material Updates

### Exact Document Dedupe

Use `(source_name, external_id, content_hash)` as the immutable raw-version key.

### Canonical Event Fingerprint

Derive a deterministic fingerprint from:

- Sorted validated CIKs or tickers.
- Event type.
- Direction.
- Action status.
- Normalized award, grant, or contract IDs.
- Normalized material monetary amounts.
- Published-date bucket.

### Cooldown

Apply a six-hour cooldown per `(ticker, event_type, direction)`.

A materially new event overrides the cooldown when at least one is true:

- Direction changes.
- Action status changes, such as proposed to awarded or awarded to cancelled.
- A new official source confirms the event.
- A new amount, award ID, or contract ID appears.
- Catalyst score or alert level increases.
- A revised source adds materially different evidence.

Confirmations should attach to the canonical event instead of creating avoidable alert spam.

### Delivery Idempotency

Use a transactional outbox:

1. Create alert and outbox row in the same database transaction.
2. Worker claims the row using safe locking.
3. Send the deterministic message.
4. Store Telegram message ID and final delivery status.

Favor at-most-once delivery when Telegram response status is ambiguous. If a timeout occurs after request dispatch, mark delivery `unknown` and do not blindly resend. Provide an operator retry procedure.

---

## 13. Market Context and Paper Tracking

### Alpaca IEX Snapshot

Capture when available:

- Latest observed price.
- Previous close.
- Approximate intraday move.
- Premarket or after-hours context when exposed.
- Market open/closed state.
- IEX volume and any quality limitations.
- Provider, feed, request time, and observation time.

Every user-facing market block must state `IEX partial-market data`.

### Suggested Actionability Logic

```text
Strong event and move below +10%:
  retain TRADEABLE if every other gate passes

Strong event and move from +10% through +25%:
  downgrade to REVIEW

Strong event and move above +25%:
  downgrade to AVOID_CHASE, but still send

Missing or stale market data:
  downgrade to REVIEW
```

For bearish events, evaluate the corresponding negative price move and clearly describe the direction. Do not convert the alert into an executable short recommendation.

### Paper Entry Rules

| Alert level | Paper notional |
|---|---:|
| `WATCH` | EUR 0 |
| `REVIEW` | EUR 500 |
| `TRADEABLE` | EUR 1,000 |
| `AVOID_CHASE` | EUR 0 |
| `IGNORE` | EUR 0 |

Rules:

- Bullish events use directional long paper returns.
- Bearish events use inverse/short directional returns for analysis only.
- Use the first available IEX trade or bar at or after alert time.
- For out-of-hours alerts, retain observed price context but use the first eligible regular-session price as paper entry.
- Store the entry provider, feed, quality, and any delay.

### Performance Horizons

- `1h`: first eligible bar at or after 60 minutes.
- `1d`, `5d`, `20d`: close after the corresponding number of US trading sessions.
- Use `exchange_calendars`, not calendar-day arithmetic.
- Store return, maximum drawdown, maximum runup, missing-data state, and quality.

Weekly reports must group performance by source, event type, direction, and alert level, and clearly label all IEX-derived results as partial-market measurements.

---

## 14. Telegram and OpenClaw Interaction Design

### Responsibility Split

Python backend:

- Renders and sends exact alert messages.
- Owns event, alert, trade-decision, position, and performance state.
- Validates every side effect.

OpenClaw:

- Exclusively polls inbound Telegram updates.
- Handles natural-language conversation and button callbacks.
- Calls only the restricted internal GKTrader tools.
- Does not hold authoritative trading or signal state in memory.

### Dedicated Bot Setup

- Create a new Telegram bot token.
- Configure it as a separate OpenClaw Telegram account.
- Bind it to a dedicated `gktrader-agent`.
- Allowlist only the owner's numeric Telegram user ID.
- Disable group access.
- Give the agent a minimal tool allowlist.
- Do not grant shell, browser, broker, or general network tools.

### Alert Format

Every delivered alert must include:

- Alert level, ticker, and company.
- Event type and direction.
- Source name, source URL, and fetch path.
- Published time, detected time, and source latency.
- Catalyst score, confidence, and score components.
- Source-grounded rationale and short evidence.
- Risks and uncertainties.
- Market context labeled `IEX partial-market data`.
- Action framing.
- For bearish alerts, every earlier bullish signal.

### Alert Buttons

For bullish or unclear alerts:

- `Bought`
- `No trade`
- `Remind 30m`
- `Open source`

For bearish alerts:

- `Sold/Reduced`
- `Shorted`
- `No trade`
- `Remind 30m`
- `Open source`

Keep callback payloads below Telegram's 64-byte limit, for example:

```text
gkt:a:<short-id>:buy
gkt:a:<short-id>:skip
gkt:a:<short-id>:r30
```

### Trade Follow-Up Flow

When the user selects `Bought`, `Shorted`, or `Sold/Reduced`:

1. Ask for the EUR amount.
2. Accept an optional actual execution price.
3. If no price is supplied, use the latest available price as an estimate and mark its quality.
4. Record an immutable trade decision.
5. Append an immutable position event.
6. Recalculate the projected position.
7. Confirm what was recorded.

`No trade` closes the prompt. `Remind 30m` schedules one reminder and must be idempotent.

Supported natural-language examples:

- `Bought 1000 EUR RGTI at 4.25`
- `Reduced MU by 500 EUR`
- `Closed MU`
- `Show open positions`
- `Why did you alert QBTS?`

The backend must validate all parsed inputs before recording a side effect.

### Weekly Review

Every Sunday at 14:00 `Europe/Vienna`, including daylight-saving changes:

1. Send the weekly signal and paper-performance report.
2. List every projected open position.
3. Ask the user to confirm each position.
4. Provide `Keep open`, `Close`, and `Adjust` buttons.
5. Persist confirmations and changes as immutable position events.

### Restricted OpenClaw Tool Plugin

Create a custom TypeScript plugin named `openclaw-gktrader`. Its tools call only the loopback FastAPI service.

Required tools:

```text
gktrader_get_alert
gktrader_recent_alerts
gktrader_record_decision
gktrader_snooze_alert
gktrader_list_positions
gktrader_record_position_event
gktrader_company_history
gktrader_weekly_review
```

No tool may place orders, access broker accounts, edit classifier output, or execute arbitrary commands.

Side-effect tools must require idempotency keys and return a clear confirmation of the persisted state.

---

## 15. Internal API Contract

Expose only the endpoints needed by the restricted OpenClaw plugin and health checks.

```text
GET  /healthz
GET  /readyz

GET  /v1/alerts/{id}
GET  /v1/alerts/recent
POST /v1/alerts/{id}/decision
POST /v1/alerts/{id}/snooze

GET  /v1/events/{id}
GET  /v1/companies/{ticker}/history

GET  /v1/positions
POST /v1/positions/events

GET  /v1/reviews/weekly
POST /v1/reviews/positions/{id}/confirm
```

API requirements:

- Loopback binding only.
- Shared-secret authorization header.
- Strict request and response schemas.
- Idempotency key on every side-effecting request.
- Audit log for all mutations.
- No endpoint for orders, brokers, arbitrary SQL, classifier mutation, or source deletion.

---

## 16. Reliability, Security, and Operations

### Secrets

Required secrets:

- Dedicated Telegram bot token.
- Owner Telegram numeric user ID.
- OpenRouter API key.
- Alpaca paper API key and secret.
- Internal API shared secret.
- SEC identifying User-Agent/contact string.

Keep secrets in one host-only, permission-restricted source usable by Compose and OpenClaw. Do not commit or log them.

### Logging and Traceability

- Emit structured JSON logs.
- Generate a correlation ID at raw-document ingestion and propagate it through classification, event creation, market snapshot, alert, and delivery.
- Redact secrets and authorization headers.
- Store LLM prompts and responses only after secret and personal-data review.
- Record source fetch path and provider quality.

### Health Rules

- Mark a source degraded after three consecutive failures.
- Mark a source critical after ten minutes without a successful poll.
- Send one Telegram message on degradation transition and one on recovery.
- Do not send repeated failure spam.
- Expose source state through readiness and an operator command.

### Backups

- Nightly PostgreSQL dump.
- Retain at least 14 days.
- Document restore procedure.
- Perform and record a monthly restore test after production launch.

### Rate Limits

- SEC aggregate requests must remain below 10 requests/second.
- Use source-specific concurrency limits.
- Use conditional requests and caching.
- Apply backoff on `429`, `403`, and transient server errors.

---

## 17. Testing Strategy

### Unit Tests

Cover:

- Source parsers using fixed fixtures.
- Content normalization and hashes.
- Classifier schema validation, retry, and fail-closed behavior.
- Positive, negative, neutral, and unclear contexts.
- Ticker alias resolution and ambiguity.
- Catalyst scoring and actionability downgrades.
- Canonical event fingerprints.
- Six-hour cooldown and material-update overrides.
- Telegram escaping, callback length, and message splitting.
- Paper entry and horizon calculations.
- Position-event projection.

### Contract Tests

For every source adapter:

- Parse stored representative payloads.
- Verify stable external IDs.
- Verify changed-version handling.
- Verify source metadata and timestamps.
- Verify fallback-path normalization.

Live source smoke tests must be opt-in and must not run in normal CI.

### Integration Tests

Run against disposable PostgreSQL and Redis:

- Ingest to canonical event.
- Exact and semantic dedupe.
- Transactional outbox.
- Worker retry behavior.
- OpenClaw internal API idempotency.
- Position projection.
- Scheduled snapshot creation.

Mock all external HTTP interactions.

### Required End-to-End Golden Scenarios

1. New official funding announcement produces exactly one `TRADEABLE` or `REVIEW` alert based on market availability.
2. Positive Trump mention without concrete money produces `REVIEW`.
3. Negative attack, investigation, or cancellation produces a bearish alert containing all previous bullish signals.
4. Ambiguous ticker mapping never becomes `TRADEABLE`.
5. Strong event after a `+38%` move produces and sends `AVOID_CHASE`.
6. Duplicate and revised articles do not spam; a material revision overrides cooldown.
7. Truth Social direct failure falls back to CNN mirror and displays fetch path and latency.
8. Telegram button callback and amount follow-up update the position ledger through OpenClaw.
9. Sunday weekly report asks for confirmation of every open position.
10. Restart during processing loses no events and creates no duplicate alerts.
11. Telegram delivery timeout marked `unknown` is not blindly resent.
12. Missing Alpaca data downgrades a strong event to `REVIEW`.

### Acceptance Targets

- Accessible source items are detected within the 60-second poll interval plus processing time.
- Truth mirror alerts acknowledge the mirror's longer latency.
- No `WATCH` event reaches Telegram.
- No unvalidated ticker becomes `TRADEABLE`.
- Negative context is not classified as bullish.
- Every alert has source URL and rationale.
- Restart preserves all state and idempotency.
- Weekly report and position confirmation run at Sunday 14:00 `Europe/Vienna`.
- SEC requests comply with rate limits.

---

## 18. Parallel Implementation Plan for Cheaper Agents

Run at most two implementation agents concurrently. A stronger integration lead owns shared contracts, migrations, architecture decisions, and final merges.

### Shared Rules for Every Agent

- Read this complete plan before changing files.
- Work only in the assigned paths.
- Do not change shared domain contracts or migrations without integration-lead approval.
- Add tests for every behavior implemented.
- Do not add broker access, order execution, or general-purpose agent tools.
- Do not weaken fail-closed behavior.
- Document external assumptions and fixtures.
- End each package with tests passing and a concise handoff.

### Wave 0: Contracts and Skeleton

Owner: integration lead  
Parallelism: none

Deliverables:

- Repository skeleton and dependency configuration.
- Locked domain enums and Pydantic contracts.
- Initial Alembic migrations for all MVP tables.
- Source-adapter interface.
- Market-provider interface.
- Alert renderer and outbox interfaces.
- Internal API request/response contracts.
- Golden test fixtures and CI structure.
- Architecture decision records for Telegram/OpenClaw split and market-data downgrade policy.

Gate:

- All contracts reviewed.
- Empty service boots in tests.
- Migrations upgrade and downgrade on a disposable database.
- No business implementation begins before this gate.

### Wave 1A: Runtime and Persistence

Owner paths:

```text
src/gktrader/config/
src/gktrader/db/
src/gktrader/tasks/
migrations/ only through lead-approved additions
compose.yaml
```

Deliverables:

- Configuration and secret loading.
- SQLAlchemy repositories.
- Celery worker and scheduler setup.
- Redis locks and rate-limit primitives.
- Docker Compose service definitions.
- Health and readiness foundations.
- Structured logging and correlation IDs.

Must not implement source-specific parsing or classifier logic.

Acceptance:

- Compose configuration validates.
- Database repositories and locks pass integration tests.
- Scheduled no-op task and worker retry behavior pass.

### Wave 1B: MVP Source Adapters

Owner paths:

```text
src/gktrader/sources/
tests/fixtures/sources/
tests/contract/sources/
docs/sources/
```

Deliverables:

- White House adapter.
- Truth Social direct, Playwright, and CNN fallback adapter.
- Commerce HTTP and Playwright adapter.
- NIST adapter.
- SEC current 8-K and detail adapter.
- Stored fixtures and contract tests.
- Source-specific operational notes.

Must use the locked adapter contract and must not edit shared schemas.

Acceptance:

- All adapters parse fixtures deterministically.
- Fallback and versioning behavior pass contract tests.
- SEC rate-limit behavior is demonstrated.

### Wave 2A: Intelligence, Mapping, and Signal Decisions

Dependencies: Wave 0 and source fixtures  
Owner paths:

```text
src/gktrader/intelligence/
src/gktrader/domain/ only through lead-approved implementations
tests/unit/intelligence/
```

Deliverables:

- OpenRouter structured classifier client.
- Prompt versions and validation.
- Retry and fail-closed handling.
- SEC master and deterministic alias resolution.
- Canonical event fingerprinting.
- Catalyst score and actionability logic.
- Cooldown and material-update rules.
- Bearish-history query behavior.

Acceptance:

- Ambiguous mappings never become `TRADEABLE`.
- Negative context golden cases pass.
- All score and cooldown golden cases pass.

### Wave 2B: Market Data, Paper Ledger, and Reporting

Dependencies: Wave 0  
Owner paths:

```text
src/gktrader/marketdata/
src/gktrader/reporting/
tests/unit/marketdata/
tests/unit/reporting/
```

Deliverables:

- Alpaca IEX provider adapter.
- Quality labels and missing-data behavior.
- Market snapshot persistence.
- Paper-entry rules and scheduled horizons.
- Weekly performance report generation.
- Sunday `Europe/Vienna` schedule behavior.

Acceptance:

- Market context can only downgrade.
- Every output is labeled as IEX partial data.
- Trading-session horizon tests pass across weekends, holidays, and daylight-saving changes.

### Wave 3A: Alert Rendering and Telegram Delivery

Dependencies: Waves 1 and 2  
Owner paths:

```text
src/gktrader/alerts/
tests/unit/alerts/
tests/integration/alerts/
```

Deliverables:

- Deterministic English alert templates.
- Bullish, bearish, review, tradeable, and avoid-chase variants.
- Full prior-bullish-history rendering and continuation messages.
- Contextual inline keyboards.
- Transactional outbox sender.
- Delivery-idempotency and unknown-status behavior.
- Health transition notifications.

Acceptance:

- No `WATCH` delivery path exists.
- Telegram limits, escaping, splitting, and callback sizes pass tests.
- Duplicate and ambiguous-delivery cases pass.

### Wave 3B: OpenClaw Plugin and Conversational Workflow

Dependencies: locked internal API contract  
Owner paths:

```text
integrations/openclaw-gktrader/
docs/operations/openclaw*
```

Deliverables:

- Restricted TypeScript GKTrader plugin.
- Dedicated-agent setup instructions.
- Tool schemas and shared-secret API client.
- Button callback workflows.
- Natural-language trade-decision and position workflows.
- Weekly confirmation workflow.
- Tool and integration tests.

Must not add shell, browser, broker, or order-execution tools.

Acceptance:

- Owner-only private bot setup is documented.
- Button and natural-language flows call only approved endpoints.
- Side effects are idempotent.

### Wave 4: Integration and Production Hardening

Owner: integration lead  
Parallelism: targeted fixes may be delegated, but only one lead merges shared changes.

Deliverables:

- Full pipeline wiring.
- All required end-to-end golden scenarios.
- Restart and failure-injection testing.
- Security review.
- Production Compose settings.
- Backup and restore runbook.
- Source degradation runbook.
- First-start baseline procedure.
- Deployment validation on the VPS.

Launch gate:

- All functional and operational acceptance targets pass.
- Dedicated bot is owner-only.
- No broker credentials or execution endpoint exists.
- Alert, position, and performance data survive restart.

---

## 19. Milestones

### M0: Foundation and Contracts

Complete Wave 0 and Wave 1A. No live alerts.

### M1: Source Ingestion

Complete all initial source adapters, baseline behavior, raw version storage, and health state.

### M2: Classification and Signal Decisions

Complete structured classification, ticker validation, canonical events, scoring, cooldown, and market downgrade logic. Validate decisions without Telegram delivery.

### M3: Telegram and Position Dialogue

Complete deterministic alert delivery, dedicated OpenClaw bot, buttons, trade follow-up, and position ledger.

### M4: Paper Performance and Weekly Review

Complete paper snapshots, weekly performance report, and Sunday position confirmation.

### M5: Production Hardening

Complete failure testing, security checks, backup/restore, VPS deployment, and launch gate.

Only after MVP metrics are available should the project prioritize DOE, Defense, OGE, USAspending, company IR, or licensed secondary sources.

---

## 20. Implementation Definition of Done

The MVP is done only when:

- The five MVP source families run on a 60-second schedule.
- Every raw source version and processing attempt is auditable.
- Tickers are deterministically validated.
- Alerts are scored, deduplicated, cooldown-aware, and source-grounded.
- `WATCH` events remain internal.
- Strong already-moved events still send as `AVOID_CHASE`.
- Bearish alerts include all earlier bullish signals.
- The dedicated private Telegram bot supports buttons and natural-language follow-up.
- The user is asked whether a deal was made and in what amount.
- Position state is confirmed weekly on Sunday at 14:00 `Europe/Vienna`.
- Alpaca IEX market context and paper results are clearly marked as partial.
- Docker Compose restart does not lose or duplicate state.
- All required tests and launch gates pass.
- There is no broker integration or order-execution capability.

---

## 21. References

### Primary Sources and APIs

- White House News: https://www.whitehouse.gov/news/
- White House RSS: https://www.whitehouse.gov/news/feed/
- Department of Commerce press releases: https://www.commerce.gov/news/press-releases
- NIST News: https://www.nist.gov/news-events/news
- NIST RSS: https://www.nist.gov/news-events/news/rss.xml
- SEC EDGAR APIs: https://www.sec.gov/search-filings/edgar-application-programming-interfaces
- SEC data APIs: https://data.sec.gov/
- SEC fair-access guidance: https://www.sec.gov/about/developer-resources
- Telegram Bot API: https://core.telegram.org/bots/api
- OpenRouter Structured Outputs: https://openrouter.ai/docs/features/structured-outputs
- Alpaca Market Data: https://docs.alpaca.markets/docs/about-market-data-api

### Later Sources

- DOE RSS: https://www.energy.gov/rss/energygov/2193718
- Defense/War contracts RSS: https://www.war.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=400&Site=945&Category=Contracts
- OGE Form 278-T guide: https://www.oge.gov/web/278eGuide.nsf/Form_278-T
- OGE disclosure API: https://extapps2.oge.gov/201/Presiden.nsf/API.xsp/v2/rest
- USAspending API: https://api.usaspending.gov/

### Design References, Not Blind Dependencies

- Trump-to-market architecture reference: https://github.com/maxbbraun/trump2cash
- Truth Social archive and CNN mirror reference: https://github.com/stiles/trump-truth-social-archive
- Truth Social watcher reference: https://github.com/darrenwatt/truthy
- EDGAR tooling: https://github.com/dgunning/edgartools
- SEC monitoring patterns: https://github.com/pancak3lullz/SECurityTr8Ker

### Local Reuse Reference

- Existing OpenClaw source and local documentation: `/home/openclaw/src/openclaw`

