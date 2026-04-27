"""
NAPA Auto Parts adapter.

DOM extraction:
  NAPA renders product cards as anchor elements whose href follows the pattern:
    /en/p/{part-number}
  or with vehicle context:
    /en/auto-parts/{category}/{subcategory}/{year}/{make}/{model}/{part-number}

  The script targets those anchors, walks up to the nearest card container,
  and extracts title, brand, part number, and price.

  A JSON-LD fallback covers pages that expose Product or ItemList structured data.

Supplemental data:
  NAPA exposes internal JSON APIs reachable while the browser session is live:
    - /api/search/v2/products  – product search with pricing and availability
    - /api/product/v1/detail   – individual product detail (specs, fitment)

  Both are called via page.evaluate() fetch so cookies and session tokens
  are forwarded automatically.

Tavily scoring:
  Boosts category/fitment pages (/auto-parts/, /brakes/, year/make/model paths).
  Penalises PDP and blog pages.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
from playwright.async_api import Page

from .base import RetailerAdapter

_NAPA_BASE = "https://www.napaonline.com"


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


class NapaAdapter(RetailerAdapter):
    domain = "napaonline.com"

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

    // NAPA PDP path: /en/p/{partNumber}  or  deep auto-parts URL ending in a part number.
    // Part numbers must contain at least one letter (exclude bare numbers like years).
    // Exclude high-level category/navigation paths (≤ 5 segments after /en/).
    const looksLikeProductHref = (href) => {
        if (!href) return false;
        const clean = href.split('?')[0].replace(/\/$/, '');
        const segments = clean.split('/').filter(Boolean);
        const last = segments[segments.length - 1] || '';
        // Must contain at least one letter and one digit (real part numbers)
        const isPartNum = /[A-Z]/i.test(last) && /[0-9]/.test(last);
        if (/\/en\/p\/[A-Z0-9-]+$/i.test(clean)) return isPartNum;
        // Deep auto-parts URL: needs at least 7 path segments to be a product page
        if (/\/en\/auto-parts\//i.test(clean) && segments.length >= 7) return isPartNum;
        return false;
    };

    const extractPartNum = (href) => {
        try {
            const parts = href.split('?')[0].replace(/\/$/, '').split('/');
            return parts[parts.length - 1] || null;
        } catch (_) {
            return null;
        }
    };

    const findContainer = (anchor) => {
        let el = anchor.parentElement;
        for (let i = 0; i < 14; i++) {
            if (!el || !el.parentElement) break;
            const cls = (el.className || '').toLowerCase();
            const testId = (el.getAttribute('data-testid') || '').toLowerCase();
            if (
                cls.includes('product') || cls.includes('card') ||
                cls.includes('item') || cls.includes('result') ||
                cls.includes('tile') || cls.includes('shelf') ||
                testId.includes('product') || testId.includes('item') ||
                el.tagName === 'LI' || el.tagName === 'ARTICLE'
            ) break;
            el = el.parentElement;
        }
        return el || anchor.parentElement;
    };

    const getPriceText = (container) => {
        if (!container) return null;
        // Try explicit price elements first
        const priceEl = (
            container.querySelector('[class*="price"]') ||
            container.querySelector('[data-testid*="price"]') ||
            container.querySelector('[class*="Price"]')
        );
        if (priceEl) {
            const text = priceEl.textContent.trim();
            const m = text.match(/\$?([0-9,]+\.[0-9]{2})/);
            if (m) return m[1].replace(/,/g, '');
        }
        // Text-node walk fallback
        const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
        let node;
        while ((node = walker.nextNode())) {
            const text = (node.textContent || '').trim();
            if (/^\$[0-9,]+\.[0-9]{2}$/.test(text)) {
                return text.replace('$', '').replace(/,/g, '');
            }
        }
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

        const title = (
            getText('[class*="product-title"]') ||
            getText('[class*="productTitle"]') ||
            getText('[data-testid*="product-name"]') ||
            getText('[data-testid*="title"]') ||
            anchor.getAttribute('aria-label') ||
            anchor.textContent.trim() ||
            null
        );

        const brand = (
            getText('[class*="brand"]') ||
            getText('[class*="Brand"]') ||
            getText('[data-testid*="brand"]') ||
            null
        );

        const partNumFromDOM = (
            getText('[class*="part-number"]') ||
            getText('[class*="partNumber"]') ||
            getText('[data-testid*="part-number"]') ||
            getText('[class*="sku"]') ||
            null
        );
        const partNum = partNumFromDOM || extractPartNum(href);

        const price = getPriceText(container);

        if (!title && !partNum) return null;
        // Skip cards with no price AND no title (likely nav promos)
        if (!price && !title) return null;

        // Make href absolute
        const absHref = href.startsWith('http') ? href
            : (window.location.origin + (href.startsWith('/') ? href : '/' + href));

        return { title, brand, partNum, price, href: absHref };
    };

    // Pass 1: anchor-based extraction
    document.querySelectorAll('a[href]').forEach(anchor => {
        maybePush(extractFromAnchor(anchor));
    });

    // Pass 2: JSON-LD fallback
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
            const absHref = href && !href.startsWith('http')
                ? (window.location.origin + (href.startsWith('/') ? href : '/' + href))
                : href;
            maybePush({ title, brand, partNum, price, href: absHref });
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
        if (data && data['@type'] === 'Product') { pushProduct(data); return; }
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

        # Boost category / fitment listing pages
        if "/auto-parts/" in path:
            score += 0.12
        if re.search(r"/20[0-9]{2}/", path):  # year segment
            score += 0.08
        if "/brakes" in path or "/engine" in path or "/filters" in path:
            score += 0.05


        # Penalise PDPs and content pages
        if re.search(r"/en/p/[A-Z0-9-]+$", path, re.IGNORECASE):
            score -= 0.2
        if "/blog/" in path or "/advice/" in path or "/how-to" in path:
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
        """
        Probe NAPA's internal JSON endpoints while the browser session is live.

        Endpoints attempted:
          /api/search/v2/products  – keyword search with pricing + availability
          /api/product/v1/detail   – per-product detail (first 10 products)
        """
        part_numbers = [
            p["partNum"]
            for p in products[:10]
            if p.get("partNum")
        ]

        # Fetch product page titles via the live browser session (httpx blocked by NAPA)
        title_map: dict[str, str] = {}
        if page is not None:
            for product in products[:10]:
                href = product.get("href") or ""
                part_num = product.get("partNum") or ""
                if not href or not part_num or product.get("title"):
                    if product.get("title"):
                        title_map[part_num] = product["title"]
                    continue
                url = href if href.startswith("http") else f"https://www.napaonline.com{href}"
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
                    # Try og:title first, fall back to <title> tag
                    fetched = await page.evaluate("""
() => {
    const og = document.querySelector('meta[property="og:title"]');
    if (og) return og.getAttribute('content') || null;
    const t = document.title || '';
    return t.split('|')[0].trim() || null;
}
""")
                    if fetched:
                        title_map[part_num] = fetched.strip()
                except Exception:
                    pass

        # Keyword search probe
        search_result = await _call_page_json(
            page,
            "/api/search/v2/products",
            method="GET",
            query={"q": search_term, "pageSize": "20"},
        )

        # Per-product detail probes
        detail_results = []
        for part_num in part_numbers:
            detail = await _call_page_json(
                page,
                "/api/product/v1/detail",
                method="GET",
                query={"partNumber": part_num},
            )
            detail_results.append({"partNum": part_num, **detail})

        return {
            "source": "napa_internal_apis",
            "title_map": title_map,
            "search_probe": search_result,
            "detail_probes": detail_results,
        }

    # ------------------------------------------------------------------ #
    # Product enrichment                                                  #
    # ------------------------------------------------------------------ #

    def enrich_products(
        self,
        products: list[dict[str, Any]],
        supplemental: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """
        Merge detail probe data back onto products matched by part number.
        """
        title_map: dict[str, str] = supplemental.get("title_map", {})

        detail_map: dict[str, Any] = {}
        for probe in supplemental.get("detail_probes", []):
            if probe.get("status") == 200 and isinstance(probe.get("body"), dict):
                detail_map[probe["partNum"]] = probe["body"]

        enriched = []
        for product in products:
            part_num = product.get("partNum", "")
            detail = detail_map.get(part_num)
            enriched_product = {
                **product,
                "_detail_fetched": bool(detail),
            }
            # Apply title fetched from PDP page if DOM extraction missed it
            if not enriched_product.get("title") and part_num in title_map:
                enriched_product["title"] = title_map[part_num]
            if detail:
                enriched_product["description"] = detail.get("description", "")
                enriched_product["specifications"] = detail.get("specifications", [])
                enriched_product["fitment"] = detail.get("fitment", [])
                enriched_product["availability"] = detail.get("availability")
            enriched.append(enriched_product)

        return enriched
