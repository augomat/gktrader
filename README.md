## GKTrader

Conservative, auditable event-signal system for Trump/admin/public-policy catalysts affecting US-listed companies.

### MVP scope

- Poll White House, Truth Social, Commerce, NIST, and SEC/8-K sources.
- Store immutable raw documents and processing audit trails.
- Classify events with strict structured outputs.
- Resolve company/ticker mappings deterministically.
- Score signals, apply cooldown/dedupe, and downgrade with IEX partial-market data.
- Send deterministic Telegram alerts.
- Record trade decisions and projected positions through a restricted internal API.
- Produce weekly paper-performance reviews.

### Safety boundaries

- No broker integration.
- No order placement.
- No second Telegram inbound poller.
- No LLM-only ticker approval.

### Repository layout

See `IMPLEMENTATION_PLAN.md` for the implementation contract.

### Local development

1. Copy `.env.example` to `.env` and fill required secrets.
2. Install dependencies with `uv sync` or `pip install -e .[dev]`.
3. Run tests with `pytest`.
4. Start the internal API with `uvicorn gktrader.api.app:create_app --factory`.

### Operations docs

- `docs/decisions/` for architecture decisions.
- `docs/operations/` for deployment and restore procedures.
- `docs/sources/` for source-specific notes.
