#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILES=(-f compose.yaml -f compose.dev.yaml)
SERVICES=(worker scheduler)
TAIL_LINES="${TAIL_LINES:-40}"

echo "Tailing logs (${TAIL_LINES} lines): ${SERVICES[*]}"
docker compose "${COMPOSE_FILES[@]}" logs --tail "$TAIL_LINES" -f "${SERVICES[@]}"
