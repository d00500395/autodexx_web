"""
AutoZone adapter.

DOM extraction:
  AutoZone renders products inside shelf/product-card components.
  The script targets anchors whose href contains '/parts/' (the canonical
  AutoZone PDP path) and walks up to the nearest card container to pull
  title, brand, part number, and price.

Supplemental data:
  No internal AutoZone APIs have been reverse-engineered yet.
  fetch_supplemental_data returns {} so the orchestrator skips that phase.
  Add endpoint probes here once they are discovered.

Tavily scoring:
  Boosts category listing pages (/brakes-and-traction-control/, /parts/)
  and penalises blog / how-to pages.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
from playwright.async_api import Page

from .base import RetailerAdapter

# Default store number used for the price API; close enough for list-price purposes.
_DEFAULT_STORE = "868"


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
    const headers = { "accept": "application/json, text/plain, */*" };
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


class AutoZoneAdapter(RetailerAdapter):
    domain = "autozone.com"

    # ------------------------------------------------------------------ #
    # DOM extraction                                                       #
    # ------------------------------------------------------------------ #

    @property
    def dom_extract_script(self) -> str:
        return r"""
() => {
    const cards = [];
    const seen = new Set();

    const maybePush = (card) => {
        if (!card) return;
        const key = `${card.href || ''}|${card.partNum || ''}|${card.title || ''}`.toLowerCase();
        if (!key.trim() || seen.has(key)) return;
        seen.add(key);
        cards.push(card);
    };

    const looksLikeProductHref = (href) => {
        if (!href) return false;
        // AutoZone PDP format: /p/{slug}/{numericId}  e.g. /p/duralast-disc-brake-rotor-55097dl/838115
        // Exclude anchor-only variants like /p/.../838115#customer-reviews
        return /\/p\/[^/]+\/[0-9]+$/.test(href.split('?')[0]);
    };

    const findContainer = (anchor) => {
        let container = anchor.parentElement;
        for (let i = 0; i < 14; i++) {
            if (!container || !container.parentElement) break;
            const cls = (container.className || '').toLowerCase();
            const testId = (container.getAttribute('data-testid') || '').toLowerCase();
            if (
                cls.includes('product') || cls.includes('shelf') ||
                cls.includes('card') || cls.includes('item') ||
                cls.includes('result') ||
                testId.includes('product') ||
                testId.includes('item') ||
                container.tagName === 'LI' ||
                container.tagName === 'ARTICLE'
            ) {
                break;
            }
            container = container.parentElement;
        }
        return container || anchor.parentElement;
    };

    const getPriceText = (container) => {
        if (!container) return null;
        const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
        let node;
        while ((node = walker.nextNode())) {
            const text = (node.textContent || '').trim();
            if (/^\$[0-9,]+(\.[0-9]{2})?$/.test(text)) {
                return text.replace('$', '').replace(/,/g, '');
            }
        }
        return null;
    };

    const extractSkuId = (href) => {
        // Primary: oProductId query param
        try {
            const u = new URL(href, window.location.origin);
            const fromQuery = u.searchParams.get('oProductId');
            if (fromQuery) return fromQuery;
            // Fallback: /p/{slug}/{numericId} — last path segment
            const parts = u.pathname.replace(/\/$/, '').split('/');
            const last = parts[parts.length - 1];
            if (/^[0-9]+$/.test(last)) return last;
        } catch (_) {}
        return null;
    };

    const extractFromAnchor = (anchor) => {
        const href = anchor.getAttribute('href') || '';
        if (!looksLikeProductHref(href)) return null;

        const container = findContainer(anchor);
        const getText = (selector) => {
            const el = container.querySelector(selector);
            return el ? el.textContent.trim() : null;
        };

        const title =
            getText('[data-testid*="product-title"]') ||
            null;

        const partNum =
            getText('[data-testid*="product-part-number"]') ||
            null;

        const brand =
            getText('[data-testid*="brand"]') ||
            null;

        const price = getPriceText(container);
        const skuId = extractSkuId(href);
        if (!title && !partNum && !price) return null;
        return { title, brand, partNum, skuId, price, href };
    };

    // Pass 1: anchor-based extraction
    document.querySelectorAll('a[href]').forEach(anchor => {
        maybePush(extractFromAnchor(anchor));
    });

    // Pass 2: JSON-LD fallback (many retail pages expose ItemList/Product)
    document.querySelectorAll('script[type="application/ld+json"]').forEach(node => {
        let data = null;
        try {
            data = JSON.parse(node.textContent || 'null');
        } catch (_) {
            return;
        }

        const pushProduct = (p) => {
            if (!p || typeof p !== 'object') return;
            const offers = Array.isArray(p.offers) ? p.offers[0] : p.offers;
            const href = p.url || p['@id'] || null;
            const title = p.name || null;
            const brand = (p.brand && (p.brand.name || p.brand)) || null;
            const partNum = p.sku || p.mpn || null;
            const price = offers && offers.price != null ? String(offers.price) : null;
            maybePush({ title, brand, partNum, price, href });
        };

        if (Array.isArray(data)) {
            data.forEach(item => {
                if (item && item['@type'] === 'Product') pushProduct(item);
                if (item && item['@type'] === 'ItemList' && Array.isArray(item.itemListElement)) {
                    item.itemListElement.forEach(el => pushProduct(el && (el.item || el)));
                }
            });
            return;
        }

        if (data && data['@type'] === 'Product') {
            pushProduct(data);
            return;
        }

        if (data && data['@type'] === 'ItemList' && Array.isArray(data.itemListElement)) {
            data.itemListElement.forEach(el => pushProduct(el && (el.item || el)));
        }
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
        raw_url = str(result.get("url") or "")
        parsed = urlparse(raw_url)
        path = parsed.path.lower()
        query = parse_qs(parsed.query)

        # AutoZone canonical fitment listing pages follow the pattern:
        # /{category}/{subcategory}/{make}/{model}/{year}
        # Boost pages that have a year segment — works for any part category.
        year_match = re.search(r"/(19|20)\d{2}(/|$)", path)
        if year_match:
            score += 0.20
            # Extra boost when the year is the final path segment (canonical listing,
            # not a brand/filter sub-page under the year).
            if path.rstrip("/").endswith(year_match.group(0).rstrip("/")):
                score += 0.10

        # Penalize heavily filtered URLs that reduce page generality.
        if "/b/brand/" in path:
            score -= 0.60
        if "srsltid" in query:
            score -= 0.20

        # Penalize non-shopping pages.
        if "/repair-help/" in path or "/diy/" in path or "/how-to/" in path or "/p/" in path:
            score -= 0.60

        # Slightly penalize deep paths since category pages are typically shorter.
        depth = len([p for p in path.split("/") if p])
        if depth > 9:
            score -= 0.15

        return score

    # ------------------------------------------------------------------ #
    # Supplemental API calls (stub – no internal APIs mapped yet)        #
    # ------------------------------------------------------------------ #

    async def fetch_supplemental_data(
        self,
        page: Page,
        products: list[dict[str, Any]],
        search_term: str,
        httpx_client: httpx.AsyncClient,
    ) -> dict[str, Any]:
        _ = search_term, httpx_client

        # ── DOM probe (bot-detection diagnostics) ───────────────────── #
        diagnostics = await page.evaluate(
            """
() => {
    const title = document.title || '';
    const bodyText = (document.body && document.body.innerText) ? document.body.innerText : '';
    const hrefs = Array.from(document.querySelectorAll('a[href]'))
        .map(a => a.getAttribute('href') || '');
    const productTestIds = document.querySelectorAll('[data-testid*="product"]').length;

    // Only flag structural signals, not script-tag strings that appear on
    // every AutoZone page (pcapredict, sensor.js).
    const visibleMarkers = [
        'captcha',
        'verify you are human',
        'access denied',
        'forbidden',
    ].filter(n => title.toLowerCase().includes(n) || bodyText.toLowerCase().includes(n));

    const blocked = (hrefs.length < 10 && productTestIds < 3) || visibleMarkers.length > 0;

    return {
        title,
        url: location.href,
        ready_state: document.readyState,
        anchor_count: hrefs.length,
        parts_anchor_count: hrefs.filter(h => h.includes('/parts/')).length,
        brakes_anchor_count: hrefs.filter(h => h.includes('/brakes-and-traction-control/')).length,
        product_testid_count: productTestIds,
        challenge_markers: visibleMarkers,
        blocked_by_challenge: blocked,
    };
}
"""
        )

        page_fetch: dict[str, Any] = {
            "dom_probe": {
                "status": 200,
                "content_type": "application/json",
                "body": diagnostics,
            }
        }

        # ── Internal price + deal APIs (in-session fetch) ────────────── #
        sku_ids = [
            p["skuId"]
            for p in products
            if p.get("skuId")
        ]

        if sku_ids:
            ids_str = ",".join(sku_ids)
            page_fetch["sku_prices"] = await _call_page_json(
                page,
                f"/ecomm/b2c/v1/browse/skus/price/{ids_str}",
                query={"storeNumber": _DEFAULT_STORE},
            )
            page_fetch["sku_deals"] = await _call_page_json(
                page,
                f"/ecomm/b2c/browse/v4/deal/details/{ids_str}",
            )

        return {
            "page_fetch": page_fetch,
            "blocked_by_challenge": bool(diagnostics.get("blocked_by_challenge")),
        }

    # ------------------------------------------------------------------ #
    # Product enrichment (pass-through until APIs are mapped)            #
    # ------------------------------------------------------------------ #

    def enrich_products(
        self,
        products: list[dict[str, Any]],
        supplemental: dict[str, Any],
    ) -> list[dict[str, Any]]:
        page_fetch = supplemental.get("page_fetch", {})

        # Build price index: skuId (str) → price record
        price_index: dict[str, Any] = {}
        prices_resp = page_fetch.get("sku_prices", {})
        prices_body = prices_resp.get("body") if isinstance(prices_resp, dict) else None
        if isinstance(prices_body, list):
            for item in prices_body:
                sid = str(item.get("skuId", ""))
                if sid:
                    price_index[sid] = item

        # Build deal index: skuId (str) → deal record
        deal_index: dict[str, Any] = {}
        deals_resp = page_fetch.get("sku_deals", {})
        deals_body = deals_resp.get("body") if isinstance(deals_resp, dict) else None
        if isinstance(deals_body, list):
            for item in deals_body:
                sid = str(item.get("skuId", ""))
                if sid:
                    deal_index[sid] = item

        enriched = []
        for product in products:
            sid = str(product.get("skuId") or "")
            price_data = price_index.get(sid)
            deal_data = deal_index.get(sid)
            # Prefer API retail price over DOM-scraped price when available.
            api_price = (
                str(price_data["retailPrice"])
                if price_data and price_data.get("retailPrice") is not None
                else None
            )
            enriched.append(
                {
                    **product,
                    "price": api_price or product.get("price"),
                    "price_data": price_data,
                    "deal_data": deal_data,
                }
            )
        return enriched
