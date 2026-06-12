# ADR 002: Market data may only downgrade actionability

## Status

Accepted.

## Decision

IEX partial-market data is contextual only. It may downgrade a strong signal to
`REVIEW` or `AVOID_CHASE`, but it may never promote a weak signal.

## Rationale

- IEX is incomplete market coverage.
- Promoting signals on weak or missing data would reduce precision.
- The product objective prioritizes conservative, fail-closed behavior.
