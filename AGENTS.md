# GKTrader — Project Overview & Agent Notes

## What This Is

An **event-signal agent** that monitors public, politically-driven capital-market catalysts (Trump mentions, US government contracts/grants/equity stakes, OGE filings) and delivers curated trade signals to a human via Telegram. It is a **decision-support system**, not a trading bot — it does not execute orders.

**Signal philosophy:** Event/policy-momentum, not value investing. Alpha comes from detecting government capital flows, presidential attention, and agency announcements before broad social media catches up.

## Architecture at a Glance

```
Sources (RSS/HTML/API/Playwright)
  → Raw Document Store (PostgreSQL)
  → LLM Classifier (OpenRouter, structured JSON)
  → Ticker Resolver (deterministic, SEC company-master)
  → Signal Scoring + Dedup + Cooldown
  → Market snapshot (Alpaca IEX)
  → Alert rendering + Transactional Outbox
  → Telegram Bot (at-most-once delivery)
  → Web UI (FastAPI + Jinja2, cookie auth)
```

**Services** (single Python 3.12 Docker image, dev bind-mounts `./src`):

| Service | Role |
|---------|------|
| `postgres` | Canonical database (SQLAlchemy 2.0 / Alembic) |
| `redis` | Celery broker/backend + distributed locks |
| `migrate` | Oneshot — `alembic upgrade head` |
| `api` | Internal REST API (`/v1/`) + Web UI (`/ui/`) |
| `worker` | Celery worker (`gktrader` queue) |
| `scheduler` | Celery beat — periodic polling, delivery, weekly reviews |

**Pipeline** (`src/gktrader/tasks/pipeline.py`, `src/gktrader/tasks/jobs.py`):
1. **Ingest** — source adapters fetch, normalize, dedupe by content hash
2. **Classify** — OpenRouter extracts events (tickers, type, direction, amounts, IDs)
3. **Signal** — deterministic ticker resolution, fingerprinted dedup, 6h cooldown, scoring
4. **Alert** — market snapshot, downgrade logic, English rendering, outbox enqueue
5. **Deliver** — outbox with CLAIM→SENT/UNKNOWN state machine, at-most-once

## Where to Look

| Concern | Key Files |
|---------|-----------|
| Source adapters | `src/gktrader/sources/` — WhiteHouse, NIST, SEC, Commerce, TruthSocial |
| LLM classification | `src/gktrader/intelligence/classifier.py`, `prompts.py` |
| Ticker resolution | `src/gktrader/intelligence/resolver.py` |
| Scoring & gating | `src/gktrader/intelligence/scoring.py`, `cooldown.py`, `fingerprint.py` |
| Alert rendering | `src/gktrader/alerts/renderer.py` |
| Telegram delivery | `src/gktrader/alerts/sender.py`, `outbox.py`, `keyboard.py` |
| Market data | `src/gktrader/marketdata/alpaca.py`, `downgrade.py` |
| Paper trading | `src/gktrader/reporting/paper.py`, `horizons.py`, `positions.py` |
| Pipeline orchestration | `src/gktrader/tasks/pipeline.py` |
| Celery tasks + beat | `src/gktrader/tasks/jobs.py`, `celery_app.py` |
| DB models | `src/gktrader/db/models.py` |
| Config | `src/gktrader/config/settings.py` (env: `GKTRADER_*`) |
| API endpoints | `src/gktrader/api/app.py`, `services.py` |
| Web UI | `src/gktrader/ui/routes.py`, `service.py`, `auth.py` |
| Telegram bridge | `integrations/openclaw-gktrader/` (TypeScript) |
| Tests | `tests/unit/`, `tests/integration/`, `tests/contract/` |
| Start/restart/stop | `./gkt-start.sh`, `./gkt-restart.sh`, `./gkt-stop.sh` |

## Key Design Decisions

- **Tickers never trusted from LLM** — always resolved deterministically via SEC company master + fuzzy matching (`rapidfuzz`)
- **First-poll baseline suppression** — prevents retrospectve alert floods on initial source replay
- **At-most-once delivery** — UNKNOWN (timeout) is never retried; no double-sends
- **Idempotency** — trade decisions, snoozes, position events all keyed
- **Content-hash dedup** — documents deduplicated before classification
- **Heat-check downgrade** — market snapshot after alert creation can downgrade TRADEABLE→REVIEW→AVOID_CHASE based on intraday move

## Restarting Local Services After Code Changes

- After every run, commit the changes made to the modified files before closing the task.
- For changes under `src/` in local dev, a full image rebuild is usually not required because `compose.dev.yaml` bind-mounts `./src` into the `api`, `worker`, `scheduler`, and `migrate` containers.
- Running services still need a restart to load updated Python code. A live bind mount alone is not enough for long-running Python processes.
- `./gkt-start.sh` runs `docker compose ... up -d` and is useful for starting the stack, but it is not the right assumption for refreshing already-running services. If containers are already up, `up -d` may leave them running without restarting the processes.
- For code-only changes, prefer `./gkt-restart.sh`. It restarts `api` and `worker` only, which keeps impact low for normal application-code reloads.

```bash
./gkt-restart.sh
```

- If the changed code affects scheduled polling or beat-side logic, include the scheduler too:

```bash
INCLUDE_SCHEDULER=1 ./gkt-restart.sh
```

- If container configuration, dependencies, or the image build changed, recreate instead:

```bash
docker compose -f compose.yaml -f compose.dev.yaml up -d --build api worker scheduler
```
