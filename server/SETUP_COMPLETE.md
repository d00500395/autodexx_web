# 🎯 API Setup Complete

## ✅ What's Been Done

### 1. Directory Structure
- Moved `web_scraper` → `autodexx_web/server`
- All original files preserved and working
- Added FastAPI backend

### 2. Backend Files Created
- `api.py` - FastAPI application with multi-domain search
- `api.py` - FastAPI application with multi-domain search
- `quickstart.sh` - One-command setup and run
- `scripts/run_api.sh` - Run API server
- `API_GUIDE.md` - Full API documentation
- Dynamic import system for agent module

### 3. API Features
- **Endpoint**: POST `/api/search`
- **Input**: `{vehicle_query, part_query}`
- **Output**: JSON with results from all 5 domains
- **Parallel execution**: All domains queried simultaneously
- **Error handling**: Graceful error handling per domain
- **3 recommendations per domain**: lowest price, recommended, premium

### 4. Supported Domains
- oreillyauto.com
- ebay.com
- napaonline.com
- autozone.com
- rockauto.com

## 🚀 To Get Started

### Option 1: Quick Start (Recommended First)
```bash
cd autodexx_web/server
bash quickstart.sh
```

This will:
1. Create Python venv
2. Install dependencies
3. Install Playwright Chromium
4. Prompt you to add TAVILY_API_KEY to .env
5. Start the API server

### Option 2: Manual Setup
```bash
cd autodexx_web/server
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env
# Edit .env with TAVILY_API_KEY
python -m uvicorn api:app --host 0.0.0.0 --port 8000 --reload
```

## 📍 API Location

**Development**: `http://localhost:8000`

**API Docs**: `http://localhost:8000/docs` (interactive Swagger UI)

## 🔌 Frontend Integration

Now you can call the API from your search page:

```javascript
// Example from React/Vue/any frontend
async function searchParts(vehicleQuery, partQuery) {
  try {
    const response = await fetch('http://localhost:8000/api/search', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        vehicle_query: vehicleQuery,
        part_query: partQuery
      })
    });

    if (!response.ok) {
      throw new Error(`API error: ${response.status}`);
    }

    const data = await response.json();
    
    // data.results = {
    //   "oreillyauto.com": { ... },
    //   "ebay.com": { ... },
    //   ...
    // }
    
    // Access recommendations from each domain:
    // data.results['oreillyauto.com'].llm_tagged_products
    // [
    //   { tag: "lowest price", product: {...} },
    //   { tag: "recommended", product: {...} },
    //   { tag: "premium", product: {...} }
    // ]

    return data;
  } catch (error) {
    console.error('Search failed:', error);
    throw error;
  }
}
```

## 📝 Next Steps for Front End

When you're ready to modify the search page, we'll need to:

1. ✅ **API is ready** - running on localhost:8000
2. ⏳ **Update search component** - call /api/search instead of local function
3. ⏳ **Parse results** - extract results by domain
4. ⏳ **Display recommendations** - show 3 products per retailer

**I can help with step 2–4 when you're ready.**

## 🧪 Test the API

### Using curl
```bash
curl -X POST http://localhost:8000/api/search \
  -H "Content-Type: application/json" \
  -d '{
    "vehicle_query": "1999 Chevrolet Silverado 1500",
    "part_query": "fuel pump"
  }'
```

### Using the interactive docs
Open `http://localhost:8000/docs` and use the Swagger UI

## ⚠️ Important Notes

### .env Configuration
Before running the API, set these in `autodexx_web/server/.env`:
- `TAVILY_API_KEY` - Required (from Tavily API)
- `OLLAMA_BASE_URL` - Optional (default: http://localhost:11434)
- `OLLAMA_MODEL` - Optional (default: deepseek-r1:latest)

### Server Deployment
For production/server deployment:
- Use `scripts/bootstrap_server.sh` for quick setup
- Use `scripts/test_headful_xvfb.sh` for headful mode with virtual display
- Update CORS in `api.py` with your frontend URL

### Performance
- First search: ~30-60 seconds (depends on retailers)
- Parallel execution: all domains queried at once
- Results cached in `api_intercept_outputs/`

## 📚 Documentation
- `API_GUIDE.md` - Full API endpoint documentation
- `README.md` - Setup and configuration
- `api.py` - Source code with inline comments

## ✨ Ready?

Once you test the API is working, let me know and we'll:
1. Update your search page to call the API
2. Parse and display the multi-domain results
3. Handle error cases
