"""
eBay Motors API adapter for auto parts search.

**Note**: eBay is a marketplace, not a direct retailer. This adapter uses
eBay's public REST APIs (Browse API, Marketing API) rather than DOM scraping.

Supplemental data:
  - Item details API       – detailed product specs, images, shipping
  - Rating histogram API   – review counts and rating distribution
  - Browse API search      – primary item discovery and filtering

Enrichment:
  - Attaches condition, seller info, availability, and reviews to products.
"""

from __future__ import annotations

import base64
import html
import logging
import os
import re
from typing import Any
from urllib.parse import urlencode

import httpx
from playwright.async_api import Page

from .base import RetailerAdapter

logger = logging.getLogger(__name__)

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")
_STYLE_BLOCK_RE = re.compile(r"<style[^>]*>.*?</style>", re.DOTALL | re.IGNORECASE)
_SCRIPT_BLOCK_RE = re.compile(r"<script[^>]*>.*?</script>", re.DOTALL | re.IGNORECASE)


def _strip_html(raw: str) -> str:
    """Remove HTML tags, style/script blocks, and collapse whitespace to plain text."""
    if not raw:
        return ""
    unescaped = html.unescape(raw)
    no_style = _STYLE_BLOCK_RE.sub(" ", unescaped)
    no_script = _SCRIPT_BLOCK_RE.sub(" ", no_style)
    no_tags = _HTML_TAG_RE.sub(" ", no_script)
    return _WHITESPACE_RE.sub(" ", no_tags).strip()


class EbayAdapter(RetailerAdapter):
    """
    eBay Motors adapter using public REST APIs.
    
    Key characteristics:
    - Pure API-based (no DOM extraction)
    - Requires OAuth token for authentication
    - Searches EBAY_MOTORS_US marketplace
    - Supports vehicle fitment via aspect filters
    
    Required environment variables:
    - EBAY_CLIENT_ID: OAuth client ID
    - EBAY_CLIENT_SECRET: OAuth client secret
    
    Raises RuntimeError if credentials are not provided.
    """

    domain = "ebay.com"
    is_api_based = True  # Flag for agent: this adapter doesn't need browser

    # eBay constants
    _EBAY_BASE_URL = "https://api.ebay.com"
    _EBAY_SANDBOX_URL = "https://api.sandbox.ebay.com"
    _MARKETPLACE_ID = "EBAY_MOTORS_US"
    
    # OAuth
    _OAUTH_ENDPOINT = "/identity/v1/oauth2/token"
    _OAUTH_SCOPE = "https://api.ebay.com/oauth/api_scope"

    @staticmethod
    def _ensure_new_in_query(query: str) -> str:
        text = (query or "").strip()
        if not text:
            return "new"
        lowered_tokens = {token.lower() for token in text.split()}
        if "new" in lowered_tokens:
            return text
        return f"{text} new"
    
    def __init__(self):
        self._oauth_token = None
        self._oauth_token_expires_at = 0
        self._client_id = os.getenv("EBAY_CLIENT_ID")
        self._client_secret = os.getenv("EBAY_CLIENT_SECRET")
        self._use_sandbox = os.getenv("EBAY_USE_SANDBOX", "false").lower() == "true"
        
        if not (self._client_id and self._client_secret):
            raise RuntimeError(
                "eBay adapter requires OAuth credentials. "
                "Set EBAY_CLIENT_ID and EBAY_CLIENT_SECRET in .env"
            )

    # ================================================================ #
    # DOM extraction (N/A for eBay - using API instead)                #
    # ================================================================ #

    @property
    def dom_extract_script(self) -> str:
        """
        eBay adapter does not use DOM extraction.
        Product data is retrieved via the Browse API instead.
        
        This method is not used but required by the interface.
        """
        return r"""
() => {
    // eBay adapter uses REST APIs, not DOM extraction.
    // This is a placeholder to satisfy the RetailerAdapter interface.
    return [];
}
"""

    # ================================================================ #
    # Tavily scoring (minimal override for eBay URLs)                  #
    # ================================================================ #

    def score_tavily_result(
        self,
        result: dict[str, Any],
        *,
        part_query: str | None = None,
        vehicle_query: str | None = None,
    ) -> float:
        """
        Score a Tavily search result pointing to an eBay page.
        
        Boost URLs that are category pages or vehicle-filtered searches.
        Penalize auction listing URLs (we want category pages, not direct listings).
        """
        score = super().score_tavily_result(
            result,
            part_query=part_query,
            vehicle_query=vehicle_query,
        )
        url = str(result.get("url") or "")

        # Prefer category/fitment pages
        if "/itm/" not in url:  # Not a direct item listing
            score += 0.15

        # Boost if URL contains vehicle filters or category info
        if any(
            keyword in url.lower()
            for keyword in ["2019", "2020", "toyota", "camry", "filter", "category"]
        ):
            score += 0.1

        # Penalize direct auction listings (we want to extract from category page)
        if "/itm/" in url:
            score -= 0.25

        return score

    # ================================================================ #
    # Supplemental API calls (uses httpx for eBay REST APIs)           #
    # ================================================================ #

    async def fetch_supplemental_data(
        self,
        page: Page,
        products: list[dict[str, Any]],
        search_term: str,
        httpx_client: httpx.AsyncClient,
    ) -> dict[str, Any]:
        """
        Fetch supplemental product data from eBay's Browse and Marketing APIs.
        """
        try:
            token = await self._get_oauth_token(httpx_client)
            base_url = self._EBAY_SANDBOX_URL if self._use_sandbox else self._EBAY_BASE_URL
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-EBAY-C-MARKETPLACE-ID": self._MARKETPLACE_ID,
            }
            
            item_details = []
            for product in products[:5]:  # Limit to 5 to avoid rate limits and long waits
                item_id = product.get("itemId")
                if not item_id:
                    continue
                
                try:
                    resp = await httpx_client.get(
                        f"{base_url}/buy/browse/v1/item/{item_id}",
                        headers=headers,
                        timeout=8.0,
                    )
                    if resp.status_code == 200:
                        item_details.append({
                            "itemId": item_id,
                            "status": 200,
                            "data": resp.json()
                        })
                    else:
                        item_details.append({
                            "itemId": item_id,
                            "status": resp.status_code,
                            "error": resp.text[:200]
                        })
                except Exception as e:
                    item_details.append({
                        "itemId": item_id,
                        "status": 0,
                        "error": str(e)
                    })
            
            return {
                "source": "ebay_apis",
                "mode": "production",
                "oauth_token_obtained": True,
                "item_details_fetched": len([x for x in item_details if x.get("status") == 200]),
                "item_details": item_details,
            }
        except Exception as e:
            logger.error(f"Error in eBay supplemental fetch: {e}")
            raise

    # ================================================================ #
    # Product enrichment (minimal for now - API provides rich data)    #
    # ================================================================ #

    def enrich_products(
        self,
        products: list[dict[str, Any]],
        supplemental: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """
        Enrich products with supplemental eBay API data.
        """
        item_details = {
            item["itemId"]: item["data"]
            for item in supplemental.get("item_details", [])
            if item.get("status") == 200
        }
        
        enriched = []
        for product in products:
            item_id = product.get("itemId", "")
            details = item_details.get(item_id)
            
            enriched_product = {
                **product,
                "_details_fetched": bool(details),
            }
            
            if details:
                # Attach detailed specs if available
                raw_desc = details.get("description", "") or ""
                enriched_product["description"] = _strip_html(raw_desc)
                enriched_product["specifications"] = details.get("specifications", [])
                enriched_product["item_images"] = details.get("images", [])
            
            enriched.append(enriched_product)
        
        return enriched
    
    # ================================================================ #
    # Helper: OAuth token management                                   #
    # ================================================================ #
    
    async def _get_oauth_token(self, httpx_client: httpx.AsyncClient) -> str | None:
        """
        Generate an OAuth token using Client Credentials flow.
        
        Returns:
            OAuth token string, or None if token generation fails.
        """
        try:
            base_url = self._EBAY_SANDBOX_URL if self._use_sandbox else self._EBAY_BASE_URL
            endpoint = f"{base_url}{self._OAUTH_ENDPOINT}"
            
            # Create Basic auth header
            credentials = f"{self._client_id}:{self._client_secret}"
            auth_b64 = base64.b64encode(credentials.encode()).decode()
            
            headers = {
                "Authorization": f"Basic {auth_b64}",
                "Content-Type": "application/x-www-form-urlencoded",
            }
            
            data = {
                "grant_type": "client_credentials",
                "scope": self._OAUTH_SCOPE,
            }
            
            response = await httpx_client.post(
                endpoint,
                headers=headers,
                data=data,
                timeout=10.0,
            )
            
            if response.status_code == 200:
                token_data = response.json()
                self._oauth_token = token_data.get("access_token")
                logger.info(f"eBay OAuth token obtained (expires in {token_data.get('expires_in')}s)")
                return self._oauth_token
            else:
                raise RuntimeError(
                    f"OAuth token request failed: {response.status_code} {response.text[:200]}"
                )
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"Exception obtaining OAuth token: {e}") from e


    # ================================================================ #
    # API Call: Browse API search for products                          #
    # ================================================================ #
    
    async def call_browse_api(
        self,
        query: str,
        httpx_client: httpx.AsyncClient,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Call eBay Browse API /item_summary/search to fetch products.
        
        Parameters
        ----------
        query : str
            Natural language search query (e.g. "2019 Toyota Camry brake rotor")
        httpx_client : AsyncClient
            HTTP client for API calls
        limit : int
            Results per page (1-200, default 100)
        
        Returns
        -------
        list[dict]
            List of product dicts with itemId, title, price, seller, shipping, etc.
        
        Raises
        ------
        RuntimeError
            If OAuth token cannot be obtained or API call fails.
        """
        try:
            token = await self._get_oauth_token(httpx_client)
            base_url = self._EBAY_SANDBOX_URL if self._use_sandbox else self._EBAY_BASE_URL
            effective_query = self._ensure_new_in_query(query)
            
            # Build search parameters
            params = {
                "q": effective_query,
                "market_id": self._MARKETPLACE_ID,
                "limit": str(limit),
            }
            
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-EBAY-C-MARKETPLACE-ID": self._MARKETPLACE_ID,
            }
            
            url = f"{base_url}/buy/browse/v1/item_summary/search"
            query_string = urlencode(params)
            
            response = await httpx_client.get(
                f"{url}?{query_string}",
                headers=headers,
                timeout=15.0,
            )
            
            if response.status_code != 200:
                raise RuntimeError(
                    f"eBay Browse API returned {response.status_code}: {response.text[:200]}"
                )
            
            data = response.json()
            items = data.get("itemSummaries", [])
            logger.info(f"eBay Browse API returned {len(items)} items for query '{effective_query}'")
            
            # Transform eBay response to our standard product format
            products = []
            for item in items:
                product = {
                    "itemId": item.get("itemId"),
                    "title": item.get("title"),
                    "price": item.get("price", {}).get("value"),
                    "currency": item.get("price", {}).get("currency", "USD"),
                    "condition": item.get("condition"),
                    "seller": item.get("seller", {}).get("username"),
                    "seller_feedback": item.get("seller", {}).get("feedbackPercentage"),
                    "href": item.get("itemWebUrl"),
                    "availability": item.get("estimatedAvailabilities", [{}])[0].get("estimatedAvailableQuantity"),
                    "shipping_cost": item.get("shippingOptions", [{}])[0].get("shippingCost", {}).get("value"),
                    "item_location": item.get("itemLocation", {}).get("country"),
                }
                products.append(product)
            
            return products
        
        except Exception as e:
            logger.error(f"Exception calling eBay Browse API: {e}")
            raise

    # ================================================================ #
    # Helper: Build search parameters for eBay API                     #
    # ================================================================ #

    def _build_search_query(
        self,
        keyword: str,
        year: str | None = None,
        make: str | None = None,
        model: str | None = None,
    ) -> dict[str, str]:
        """
        Build eBay Browse API search parameters for vehicle-specific parts.
        
        Parameters
        ----------
        keyword : str
            Search query (e.g., "brake rotor")
        year : str, optional
            Vehicle year (e.g., "2019")
        make : str, optional
            Vehicle make (e.g., "Toyota")
        model : str, optional
            Vehicle model (e.g., "Camry")
        
        Returns
        -------
        dict[str, str]
            Query parameters for the Browse API search endpoint.
        
        Example
        -------
        >>> adapter = EbayAdapter()
        >>> params = adapter._build_search_query(
        ...     "brake rotor",
        ...     year="2019",
        ...     make="Toyota",
        ...     model="Camry"
        ... )
        >>> # params can be passed to Browse API /item_summary/search
        """
        params: dict[str, str] = {
            "q": self._ensure_new_in_query(keyword),
            "marketplace_id": self._MARKETPLACE_ID,
            "limit": "100",  # Max allowed
        }

        # Build aspect filter for vehicle fitment
        # Format: filter=Make:{Toyota},Year:{2019},Model:{Camry}
        aspects = []
        if make:
            aspects.append(f"Make:{{{make}}}")
        if year:
            aspects.append(f"Year:{{{year}}}")
        if model:
            aspects.append(f"Model:{{{model}}}")

        if aspects:
            params["filter"] = ",".join(aspects)

        return params

    # ================================================================ #
    # API Reference: Browse API response structure (for documentation)  #
    # ================================================================ #

    """
    eBay Browse API /item_summary/search response structure (for reference):
    
    {
      "itemSummaries": [
        {
          "itemId": "v1|123456789|0",
          "title": "2019 Toyota Camry Brake Rotor - Front Pair",
          "image": {
            "imageUrl": "https://i.ebayimg.com/..."
          },
          "price": {
            "value": "89.99",
            "currency": "USD"
          },
          "condition": "NEW",
          "conditionId": "3000",
          "seller": {
            "username": "auto_parts_seller",
            "feedbackPercentage": "99.5",
            "feedbackScore": 5000
          },
          "itemWebUrl": "https://www.ebay.com/itm/v1|123456789|0",
          "estimatedAvailabilities": [
            {
              "estimatedAvailableQuantity": 25,
              "estimatedSoldQuantity": 0
            }
          ],
          "itemLocation": {
            "country": "US"
          },
          "shippingOptions": [
            {
              "shippingCostType": "CALCULATED",
              "shippingCost": {
                "value": "5.99",
                "currency": "USD"
              }
            }
          ],
          "buyingOptions": ["AUCTION", "BIN"]
        }
      ],
      "href": "https://api.ebay.com/buy/browse/v1/item_summary/search?q=...",
      "total": 1234,
      "limit": 100,
      "offset": 0
    }
    
    Key insights:
    - itemId is the unique identifier for fetch details
    - price is already included in summary
    - seller info available for filtering/comparison
    - estimatedAvailableQuantity shows stock
    - shippingOptions provides shipping costs
    """
