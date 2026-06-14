#!/usr/bin/env bash
set -euo pipefail

mapfile -t TAILSCALE_IPS < <(/usr/bin/tailscale ip -4)
TAILSCALE_IP="${TAILSCALE_IPS[0]:-}"

if [[ -z "${TAILSCALE_IP}" ]]; then
  printf 'no Tailscale IPv4 address found\n' >&2
  exit 1
fi

exec "${HOME}/.opencode/bin/opencode" web \
  --hostname "${TAILSCALE_IP}" \
  --port 4096 \
  --print-logs
