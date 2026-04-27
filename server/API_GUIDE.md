# autodexx_web/server

Backend API server for AutoDEXX web scraper. Provides FastAPI endpoint for multi-domain automotive parts search.

## Files

- `agent_3.0_domain_configurable.py` - Domain-configurable agent that scrapes individual retailers
- `api.py` - FastAPI application with /api/search endpoint
- `tavily_scraper_adapters/` - Adapter package for all supported retailers
- `requirements.txt` - Python dependencies
- `scripts/run_api.sh` - Run the FastAPI server
- `scripts/bootstrap_server.sh` - Server environment setup
- `scripts/test_headful_xvfb.sh` - Test with virtual display
- `.env.example` - Environment template

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env
# Edit .env with TAVILY_API_KEY and OLLAMA settings
```

## Run API Server

```bash
bash scripts/run_api.sh
# or: python -m uvicorn api:app --host 0.0.0.0 --port 8000 --reload
```

Server runs at `http://localhost:8000`

## API Endpoints

### POST /api/search

Multi-domain search request.

**Request:**
```json
{
  "vehicle_query": "1999 Chevrolet Silverado 1500",
  "part_query": "fuel pump"
}
```

**Response:**
```json
{
  "status": "success | partial | error",
  "vehicle_query": "1999 Chevrolet Silverado 1500",
  "part_query": "fuel pump",
  "results": {
    "oreillyauto.com": { ... agent output ... },
    "ebay.com": { ... agent output ... },
    "napaonline.com": { ... agent output ... },
    "autozone.com": { ... agent output ... },
    "rockauto.com": { ... agent output ... }
  },
  "errors": {
    "domain_name": "error message"
  },
  "timestamp": 1234567890
}
```

Each domain result includes:
- `llm_tagged_products` - 3 recommendations: `lowest price`, `recommended`, `premium`
- `product_count` - Total products found
- `llm_matched_count` - Products matched by LLM
- Full agent output with all supplemental data

### GET /api/domains

List all supported retail domains.

**Response:**
```json
{
  "domains": [
    "oreillyauto.com",
    "ebay.com",
    "napaonline.com",
    "autozone.com",
    "rockauto.com"
  ]
}
```

### GET /health

Health check endpoint.

**Response:**
```json
{
  "status": "ok"
}
```

## Direct CLI Testing

Test individual domains from command line:

```bash
python agent_3.0_domain_configurable.py --list-domains
python agent_3.0_domain_configurable.py --domain oreillyauto.com --vehicle-query "1999 Chevrolet Silverado 1500" --part-query "fuel pump"
```

## Server Configuration

### Headful with Virtual Display (Recommended)

For browser-based retailers on servers without physical monitors:

```bash
bash scripts/test_headful_xvfb.sh
```

Environment:
- `ALLOW_HEADLESS_BROWSER_DOMAINS=0` (headed mode)
- `PLAYWRIGHT_BROWSER_CHANNEL=chromium`
- Requires: `xvfb`, `xauth`

### Optional Headless Mode

True headless testing (may not work with all retailers):

```bash
ALLOW_HEADLESS_BROWSER_DOMAINS=1 PLAYWRIGHT_BROWSER_CHANNEL=chromium python agent_3.0_domain_configurable.py ...
```

## Output Files

Agent execution outputs are saved to:

- `api_intercept_outputs/agent_3_0_<timestamp>.json` - Full run output
- `api_intercept_outputs/agent_3_0_latest.json` - Latest run (always updated)

## CORS Configuration

By default, CORS is enabled for all origins. For production, update `api.py` allow_origins:

```python
allow_origins=[
    "http://localhost:3000",  # your frontend URL
    "https://yourdomain.com",
]
```

## Frontend Integration

From your search page, call the API:

```javascript
const response = await fetch('http://localhost:8000/api/search', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    vehicle_query: "1999 Chevrolet Silverado 1500",
    part_query: "fuel pump"
  })
});

const data = await response.json();
// data.results contains results organized by domain
// Each domain entry has llm_tagged_products with 3 recommendations
```
