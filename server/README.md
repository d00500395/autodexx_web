# web_scraper

Standalone copy of Agent 3.0 domain-configurable scraper plus original Tavily scraper adapters.

## Included

- agent_3.0_domain_configurable.py
- tavily_scraper_adapters/ (original adapter package copied from 2.1_Agent)
- requirements.txt
- scripts/bootstrap_server.sh
- scripts/test_headful_xvfb.sh
- scripts/test_headless.sh
- .env.example

## Quick start (local or server)

1. Create and activate a virtual environment.
2. Install dependencies from requirements.txt.
3. Install Playwright Chromium.
4. Copy .env.example to .env and fill values.
5. Run a domain test.

Example:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env
python agent_3.0_domain_configurable.py --list-domains
# Server-safe headed mode using simulated display:
xvfb-run -a python agent_3.0_domain_configurable.py --domain oreillyauto.com --vehicle-query "1999 Chevrolet Silverado 1500" --part-query "fuel pump"
```

## Headful server configuration (recommended)

For browser-based retailers, use headed mode plus a simulated X display:

- ALLOW_HEADLESS_BROWSER_DOMAINS=0
- PLAYWRIGHT_BROWSER_CHANNEL=chromium

Run with xvfb-run:

```bash
xvfb-run -a -s "-screen 0 1920x1080x24" python agent_3.0_domain_configurable.py --domain oreillyauto.com --vehicle-query "1999 Chevrolet Silverado 1500" --part-query "fuel pump"
```

Why this works:

- Browser is headed (more compatible for anti-bot/front-end edge cases).
- xvfb provides a simulated monitor for servers with no physical display.

## Optional headless mode

If you want to test true headless anyway:

- ALLOW_HEADLESS_BROWSER_DOMAINS=1
- PLAYWRIGHT_BROWSER_CHANNEL=chromium

Why this matters:

- The agent defaults to headed mode for browser domains unless ALLOW_HEADLESS_BROWSER_DOMAINS is true.
- On servers, system Chrome is often absent. The copied agent now falls back to Playwright Chromium when a requested channel is unavailable.

Helper scripts:

- scripts/test_headful_xvfb.sh
- scripts/test_headless.sh

## Output files

Run summaries and full payloads are written to:

- api_intercept_outputs/agent_3_0_<timestamp>.json
- api_intercept_outputs/agent_3_0_latest.json
