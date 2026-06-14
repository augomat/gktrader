#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILES=(-f compose.yaml -f compose.dev.yaml)
SERVICES=(api worker)

if [[ "${INCLUDE_SCHEDULER:-0}" == "1" ]]; then
  SERVICES+=(scheduler)
fi

echo "Restarting: ${SERVICES[*]}"
docker compose "${COMPOSE_FILES[@]}" restart "${SERVICES[@]}"

echo
docker compose "${COMPOSE_FILES[@]}" ps "${SERVICES[@]}"
