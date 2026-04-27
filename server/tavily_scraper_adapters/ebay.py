"""eBay adapter registration in tavily_scraper_adapters."""

from __future__ import annotations

from .legacy.ebay import EbayAdapter


class EbayLLMAdapter(EbayAdapter):
    """eBay remains API-based and already uses LLM post-review in Agent 3.0."""
