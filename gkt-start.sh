#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILES=(-f compose.yaml -f compose.dev.yaml)
SERVICES=(worker scheduler)
STACK_SERVICES=(postgres redis migrate worker scheduler)
WAIT_SECONDS="${WAIT_SECONDS:-30}"
TAIL_LINES="${TAIL_LINES:-40}"

start_stack() {
  echo "Starting stack: ${STACK_SERVICES[*]}"
  docker compose "${COMPOSE_FILES[@]}" up -d "${STACK_SERVICES[@]}"
}

container_id_for() {
  local service="$1"
  docker compose "${COMPOSE_FILES[@]}" ps -q "$service"
}

container_status() {
  local container_id="$1"
  docker inspect --format '{{.State.Status}}' "$container_id"
}

wait_for_running() {
  local deadline
  deadline=$((SECONDS + WAIT_SECONDS))

  while (( SECONDS < deadline )); do
    local all_running="yes"

    for service in "${SERVICES[@]}"; do
      local container_id
      container_id="$(container_id_for "$service")"
      if [[ -z "$container_id" ]]; then
        all_running="no"
        continue
      fi

      local status
      status="$(container_status "$container_id")"
      if [[ "$status" != "running" ]]; then
        all_running="no"
      fi
    done

    if [[ "$all_running" == "yes" ]]; then
      return 0
    fi

    sleep 1
  done

  return 1
}

print_status() {
  echo
  docker compose "${COMPOSE_FILES[@]}" ps "${STACK_SERVICES[@]}"
}

tail_logs() {
  echo
  echo "Tailing logs (${TAIL_LINES} lines): ${SERVICES[*]}"
  docker compose "${COMPOSE_FILES[@]}" logs --tail "$TAIL_LINES" -f "${SERVICES[@]}"
}

start_stack

if ! wait_for_running; then
  echo
  echo "Services did not reach 'running' within ${WAIT_SECONDS}s."
  print_status
  exit 1
fi

echo
echo "Startup successful."
print_status
tail_logs
