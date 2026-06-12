# ADR 001: OpenClaw owns inbound Telegram polling

## Status

Accepted.

## Decision

OpenClaw is the exclusive inbound Telegram poller for the dedicated GKTrader bot token.
The Python backend may send outbound Bot API messages but must never call `getUpdates`
or configure a webhook.

## Rationale

- Prevents Telegram polling conflicts.
- Reuses existing OpenClaw callback and conversation handling.
- Keeps deterministic alert state in Python while conversational flows stay constrained.
