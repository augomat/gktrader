#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
UNIT_DIR="${HOME}/.config/systemd/user"
CONFIG_DIR="${HOME}/.config/gktrader"
SECRET_FILE="${CONFIG_DIR}/opencode-web.env"

mkdir -p "${UNIT_DIR}" "${CONFIG_DIR}"

if [[ ! -f "${SECRET_FILE}" ]]; then
  PASSWORD="$(openssl rand -hex 24)"
  printf 'OPENCODE_SERVER_PASSWORD=%s\n' "${PASSWORD}" > "${SECRET_FILE}"
  chmod 600 "${SECRET_FILE}"
  printf 'Created %s\n' "${SECRET_FILE}"
  printf 'Opencode web password: %s\n' "${PASSWORD}"
fi

install -m 0644 "${SCRIPT_DIR}/gktrader-opencode-web.service" "${UNIT_DIR}/gktrader-opencode-web.service"

if systemctl --user list-unit-files | grep -q '^gktrader-opencode-tailscale-serve.service'; then
  systemctl --user disable --now gktrader-opencode-tailscale-serve.service || true
  rm -f "${UNIT_DIR}/gktrader-opencode-tailscale-serve.service"
fi

systemctl --user daemon-reload
systemctl --user enable --now gktrader-opencode-web.service

DNS_NAME="$(tailscale status --json | jq -r '.Self.DNSName')"
DNS_NAME="${DNS_NAME%.}"
printf 'Tailscale URL: http://%s:4096/\n' "${DNS_NAME}"
