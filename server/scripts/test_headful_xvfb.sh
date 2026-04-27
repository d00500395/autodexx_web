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

# Force headed mode for browser-based domains.
export ALLOW_HEADLESS_BROWSER_DOMAINS=0
export PLAYWRIGHT_BROWSER_CHANNEL="${PLAYWRIGHT_BROWSER_CHANNEL:-chromium}"

# Simulated X display for servers without physical monitor.
xvfb-run -a -s "-screen 0 1920x1080x24" \
  python agent_3.0_domain_configurable.py \
    --domain oreillyauto.com \
    --vehicle-query "1999 Chevrolet Silverado 1500" \
    --part-query "fuel pump"
