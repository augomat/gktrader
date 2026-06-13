#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILES=(-f compose.yaml -f compose.dev.yaml)
STACK_SERVICES=(worker scheduler postgres redis)

echo "Stopping: ${STACK_SERVICES[*]}"
docker compose "${COMPOSE_FILES[@]}" stop "${STACK_SERVICES[@]}"

echo
docker compose "${COMPOSE_FILES[@]}" ps "${STACK_SERVICES[@]}"
