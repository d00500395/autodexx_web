#!/usr/bin/env bash
set -euo pipefail

# Ubuntu/Debian bootstrap for running Playwright in headless mode.
# Run this once on a fresh server.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y \
    python3 \
    python3-venv \
    python3-pip \
    xvfb \
    xauth \
    ca-certificates \
    curl \
    git
fi

python3 -m venv "$ROOT_DIR/.venv"
source "$ROOT_DIR/.venv/bin/activate"
python -m pip install --upgrade pip
python -m pip install -r "$ROOT_DIR/requirements.txt"

# Install Playwright browser and Linux dependencies.
python -m playwright install chromium
python -m playwright install-deps chromium || true

echo "Bootstrap complete. Next steps:"
echo "1) cp $ROOT_DIR/.env.example $ROOT_DIR/.env"
echo "2) edit $ROOT_DIR/.env"
echo "3) run scripts/test_headful_xvfb.sh"
