"""
FastAPI backend for autodexx web scraper.
Provides /api/search endpoint that queries all retail domains.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, AsyncGenerator

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# Add server directory to path
SERVER_DIR = Path(__file__).parent
sys.path.insert(0, str(SERVER_DIR))

# Dynamically import the agent module (filename has dots which aren't valid for normal imports)
agent_module_path = SERVER_DIR / "agent_3.0_domain_configurable.py"
spec = importlib.util.spec_from_file_location("agent_module", agent_module_path)
agent_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent_module)

run_agent = agent_module.run_agent

load_dotenv(SERVER_DIR / ".env")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Autodexx Web Scraper API",
    description="Multi-retailer automotive parts search API",
    version="1.0.0",
)

# Enable CORS for frontend requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure this with your actual frontend URL in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class SearchRequest(BaseModel):
    """Frontend search request."""

    vehicle_query: str
    part_query: str


class SearchResponse(BaseModel):
    """Structured search response with results per domain."""

    status: str  # "success" or "error"
    vehicle_query: str
    part_query: str
    results: dict[str, Any]  # {domain: agent_output}
    errors: dict[str, str] | None = None
    timestamp: int | None = None


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok"}


@app.post("/api/search")
async def search(request: SearchRequest) -> StreamingResponse:
    """
    Search across all registered domains.

    Returns a streaming response that emits keepalive newlines every 15s to
    prevent Cloudflare 524 timeouts, followed by a final JSON line with the
    aggregated results.
    """
    vehicle_query = (request.vehicle_query or "").strip()
    part_query = (request.part_query or "").strip()

    if not vehicle_query or not part_query:
        raise HTTPException(
            status_code=400,
            detail="Both vehicle_query and part_query are required",
        )

    domains = ["oreillyauto.com", "ebay.com", "napaonline.com", "autozone.com", "rockauto.com"]
    logger.info(f"Starting multi-domain search: vehicle={vehicle_query}, part={part_query}")

    async def generate() -> AsyncGenerator[str, None]:
        # Run all domain searches; meanwhile yield keepalive newlines every 15s
        tasks = [
            run_agent(
                domain=domain,
                vehicle_query=vehicle_query,
                part_query=part_query,
            )
            for domain in domains
        ]

        gather_task = asyncio.ensure_future(asyncio.gather(*tasks, return_exceptions=True))

        try:
            while not gather_task.done():
                try:
                    await asyncio.wait_for(asyncio.shield(gather_task), timeout=15.0)
                except asyncio.TimeoutError:
                    yield "\n"  # keepalive byte — prevents Cloudflare 524
        except Exception:
            pass

        responses = gather_task.result()

        results: dict[str, Any] = {}
        errors: dict[str, str] = {}

        for domain, response in zip(domains, responses):
            if isinstance(response, Exception):
                logger.error(f"Error querying {domain}: {response}")
                errors[domain] = str(response)
                results[domain] = None
            else:
                logger.info(f"Success querying {domain}: {len(response.get('llm_tagged_products', []))} recommendations")
                results[domain] = response

        payload = SearchResponse(
            status="success" if not errors else "partial",
            vehicle_query=vehicle_query,
            part_query=part_query,
            results=results,
            errors=errors if errors else None,
            timestamp=int(time.time()),
        )
        yield payload.model_dump_json() + "\n"

    return StreamingResponse(generate(), media_type="application/x-ndjson")


@app.get("/api/domains")
async def get_domains():
    """List all supported retail domains."""
    domains = ["oreillyauto.com", "ebay.com", "napaonline.com", "autozone.com", "rockauto.com"]
    return {"domains": domains}


@app.on_event("startup")
async def startup():
    """Validate required environment variables on startup."""
    if not os.getenv("TAVILY_API_KEY"):
        logger.warning("TAVILY_API_KEY is not set. Web scraper may not work correctly.")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
