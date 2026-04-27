#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f .venv/bin/activate ]]; then
  source .venv/bin/activate
fi

if [[ -f .env ]]; then
  # shellcheck disable=SC2046
  export $(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' .env | xargs)
fi

export ALLOW_HEADLESS_BROWSER_DOMAINS="${ALLOW_HEADLESS_BROWSER_DOMAINS:-1}"
export PLAYWRIGHT_BROWSER_CHANNEL="${PLAYWRIGHT_BROWSER_CHANNEL:-chromium}"

python agent_3.0_domain_configurable.py \
  --domain oreillyauto.com \
  --vehicle-query "1999 Chevrolet Silverado 1500" \
  --part-query "fuel pump" \
  --headless
