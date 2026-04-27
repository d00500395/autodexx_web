"""
O'Reilly Auto Parts adapter.

Supplemental data:
  - /header/search        (POST) – session / cart / vehicle info
  - /type-ahead/search    (GET)  – autocomplete suggestions
  - /shipping-estimate/line-code-item-numbers (POST) – per-product shipping
  - BazaarVoice statistics API   – review counts / ratings

Enrichment:
  - Attaches shipping_estimate and review_statistics to each product.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import httpx
from playwright.async_api import Page

from .base import RetailerAdapter


def _chunked(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


async def _call_page_json(
    page: Page,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    query: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await page.evaluate(
        """
async ({ path, method, payload, query }) => {
    const url = new URL(path, window.location.origin);
    if (query) {
        for (const [key, value] of Object.entries(query)) {
            url.searchParams.set(key, String(value));
        }
    }
    const csrf =
        document.querySelector('meta[name="_csrf"]')?.getAttribute('content')
        || document.querySelector('meta[name="csrf-token"]')?.getAttribute('content')
        || null;
    const headers = { "accept": "application/json, text/plain, */*" };
    if (csrf) headers["x-csrf-token"] = csrf;
    if (payload !== null) headers["content-type"] = "application/json";
    try {
        const response = await fetch(url.toString(), {
            method,
            credentials: "include",
            headers,
            body: payload !== null ? JSON.stringify(payload) : undefined,
        });
        const contentType = response.headers.get("content-type") || "";
        const body = contentType.includes("json") ? await response.json() : await response.text();
        return { status: response.status, content_type: contentType, body };
    } catch (error) {
        return { status: 0, content_type: "", body: String(error) };
    }
}
""",
        {"path": path, "method": method, "payload": payload, "query": query},
    )


# BazaarVoice passkey for O'Reilly
_BAZAARVOICE_PASSKEY = "caEX46NTQNS9YOVbNveDHk302IMbGxcKuPDPjKnt925z8"
_BAZAARVOICE_URL = "https://api.bazaarvoice.com/data/statistics.json"


class OReillyAdapter(RetailerAdapter):
    domain = "oreillyauto.com"

    # ------------------------------------------------------------------ #
    # DOM extraction                                                       #
    # ------------------------------------------------------------------ #

    @property
    def dom_extract_script(self) -> str:
        return r"""
() => {
    const cards = [];
    const seen = new Set();

    document.querySelectorAll('a[href*="/detail/"]').forEach(anchor => {
        const rawHref = anchor.getAttribute('href') || '';
        const href = rawHref ? new URL(rawHref, window.location.origin).toString() : '';
        const parts = href.split('/');

        let vehicleMarkerIndex = -1;
        for (let i = 1; i < parts.length - 2; i++) {
            if (parts[i] === 'v' && parts[i + 1] === 'a' && /^[0-9]+$/.test(parts[i + 2] || '')) {
                vehicleMarkerIndex = i;
                break;
            }
        }
        if (vehicleMarkerIndex < 2) return;

        const rawLineCodeVariant = parts[vehicleMarkerIndex - 2] || '';
        const itemNumber = (parts[vehicleMarkerIndex - 1] || '').split('?')[0].toUpperCase();
        const lineCode = rawLineCodeVariant.replace(/\d+$/, '').toUpperCase();
        if (!lineCode || !itemNumber || !rawLineCodeVariant) return;

        const key = lineCode + '|' + itemNumber;
        if (seen.has(key)) return;
        seen.add(key);

        let container = anchor.parentElement;
        for (let i = 0; i < 10; i++) {
            if (!container || !container.parentElement) break;
            const cls = (container.className || '').toLowerCase();
            if (
                cls.includes('card') || cls.includes('result') ||
                cls.includes('product') || cls.includes('item') ||
                container.tagName === 'LI'
            ) break;
            container = container.parentElement;
        }
        if (!container) container = anchor.parentElement;

        const getText = (selector) => {
            const el = container.querySelector(selector);
            return el ? el.textContent.trim() : null;
        };

        let price = null;
        const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
        let node;
        while ((node = walker.nextNode()) && !price) {
            const text = node.textContent.trim();
            if (/^\$[0-9]+\.[0-9]{2}$/.test(text)) price = text.replace('$', '');
        }

        cards.push({
            title:            anchor.textContent.trim() || null,
            brand:            getText('[class*="brand"]') || getText('[class*="Brand"]') || null,
            partNum:          getText('[class*="part-number"]') || getText('[class*="partNumber"]') || null,
            lineCode,
            lineCodeVariant:  rawLineCodeVariant.toUpperCase(),
            itemNumber,
            price,
            href,
        });
    });

    return cards;
}
"""

    # ------------------------------------------------------------------ #
    # Tavily scoring                                                       #
    # ------------------------------------------------------------------ #

    def score_tavily_result(
        self,
        result: dict[str, Any],
        *,
        part_query: str | None = None,
        vehicle_query: str | None = None,
    ) -> float:
        score = super().score_tavily_result(
            result,
            part_query=part_query,
            vehicle_query=vehicle_query,
        )
        url = str(result.get("url") or "")
        if "/shop/b/" in url:
            score += 0.12
        if "/v/a/" in url:
            score += 0.08
        if "/detail/" in url:
            score -= 0.5
        if "/how-to-hub/" in url:
            score -= 0.5
        return score

    # ------------------------------------------------------------------ #
    # Supplemental API calls                                              #
    # ------------------------------------------------------------------ #

    async def fetch_supplemental_data(
        self,
        page: Page,
        products: list[dict[str, Any]],
        search_term: str,
        httpx_client: httpx.AsyncClient,
    ) -> dict[str, Any]:
        shipping_payload = {
            "lineCodeItemNumbers": [
                {"lineCode": p["lineCode"], "itemNumber": p["itemNumber"]}
                for p in products
                if p.get("lineCode") and p.get("itemNumber")
            ],
            "currentPage": "plp",
        }

        page_fetch = {
            "header_search": await _call_page_json(
                page,
                "/header/search",
                method="POST",
                payload={"q": search_term, "searchTerm": search_term},
            ),
            "type_ahead": await _call_page_json(
                page,
                "/type-ahead/search",
                query={"q": search_term},
            ),
            "shipping_estimate": await _call_page_json(
                page,
                "/shipping-estimate/line-code-item-numbers",
                method="POST",
                payload=shipping_payload,
            ),
        }

        product_ids = [
            f"{p['lineCodeVariant']}-{p['itemNumber']}"
            for p in products
            if p.get("lineCodeVariant") and p.get("itemNumber")
        ]
        bazaarvoice: list[dict[str, Any]] = []
        for chunk in _chunked(product_ids, 6):
            bv_query = urlencode(
                [
                    ("apiversion", "5.4"),
                    ("passkey", _BAZAARVOICE_PASSKEY),
                    ("stats", "Reviews"),
                    ("filter", "ContentLocale:en_US,en_US"),
                    ("filter", f"ProductId:{','.join(pid.lower() for pid in chunk)}"),
                ]
            )
            resp = await httpx_client.get(f"{_BAZAARVOICE_URL}?{bv_query}")
            try:
                bv_body = resp.json()
            except Exception:
                bv_body = resp.text
            bazaarvoice.append(
                {"url": str(resp.request.url), "status": resp.status_code, "body": bv_body}
            )

        return {"page_fetch": page_fetch, "bazaarvoice": bazaarvoice}

    # ------------------------------------------------------------------ #
    # Product enrichment                                                  #
    # ------------------------------------------------------------------ #

    def enrich_products(
        self,
        products: list[dict[str, Any]],
        supplemental: dict[str, Any],
    ) -> list[dict[str, Any]]:
        page_fetch = supplemental.get("page_fetch", {})
        bazaarvoice = supplemental.get("bazaarvoice", [])

        shipping_body = page_fetch.get("shipping_estimate", {}).get("body")
        shipping_estimates = (
            shipping_body.get("estimates", []) if isinstance(shipping_body, dict) else []
        )
        estimate_index = {
            (str(item.get("line", "")).upper(), str(item.get("item", "")).upper()): item
            for item in shipping_estimates
        }

        review_index: dict[str, Any] = {}
        for bv in bazaarvoice:
            body = bv.get("body")
            if not isinstance(body, dict):
                continue
            for item in body.get("Results", []):
                ps = item.get("ProductStatistics", {})
                pid = str(ps.get("ProductId", "")).upper()
                if pid:
                    review_index[pid] = ps.get("ReviewStatistics", {})

        enriched = []
        for product in products:
            pid = f"{product['lineCodeVariant']}-{product['itemNumber']}".upper()
            enriched.append(
                {
                    **product,
                    "shipping_estimate": estimate_index.get(
                        (product["lineCode"].upper(), product["itemNumber"].upper())
                    ),
                    "review_statistics": review_index.get(pid),
                }
            )
        return enriched
