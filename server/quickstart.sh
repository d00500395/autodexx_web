#!/usr/bin/env bash
set -euo pipefail

# Quick start script for autodexx_web/server API

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

echo "🚀 AutoDEXX Web Scraper API - Quick Start"
echo "=========================================="
echo ""

# Check if venv exists
if [[ ! -d .venv ]]; then
  echo "📦 Creating virtual environment..."
  python3 -m venv .venv
fi

echo "🔌 Activating virtual environment..."
source .venv/bin/activate

# Check if requirements installed
if ! python -c "import fastapi" 2>/dev/null; then
  echo "📥 Installing dependencies..."
  pip install --upgrade pip
  pip install -r requirements.txt
  
  echo "🎬 Installing Playwright Chromium..."
  python -m playwright install chromium
  python -m playwright install-deps chromium || true
fi

# Check if .env exists
if [[ ! -f .env ]]; then
  echo "📝 Creating .env from template..."
  cp .env.example .env
  echo ""
  echo "⚠️  IMPORTANT: Edit .env and set TAVILY_API_KEY and other values"
  echo "   Then run this script again."
  exit 1
fi

echo ""
echo "✅ Setup complete!"
echo ""
echo "Starting API server on http://localhost:8000"
echo "Press Ctrl+C to stop"
echo ""
echo "Endpoints:"
echo "  POST /api/search - Multi-domain search"
echo "  GET  /api/domains - List supported domains"
echo "  GET  /health - Health check"
echo ""
echo "API docs: http://localhost:8000/docs"
echo ""

# Run the API
python -m uvicorn api:app --host 0.0.0.0 --port 8000 --reload
