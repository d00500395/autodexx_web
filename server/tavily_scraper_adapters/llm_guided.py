"""Shared LLM-guided navigation helpers for Tavily-based scraper adapters."""

from __future__ import annotations

import json
import os
import re
from typing import Any
from urllib.parse import urlparse

import httpx
from playwright.async_api import Page


class LLMGuidedAdapterMixin:
    """Adds stepwise LLM-ranked navigation with branch backtracking."""

    MAX_NAV_DEPTH = 5
    MAX_NAV_OPTIONS = 10
    _VEHICLE_STOP_WORDS = {"with", "and", "the", "truck", "pickup", "sedan", "coupe"}

    def set_runtime_context(
        self,
        *,
        vehicle_query: str | None = None,
        part_query: str | None = None,
    ) -> None:
        self._runtime_vehicle_query = str(vehicle_query or "").strip()
        self._runtime_part_query = str(part_query or "").strip()

    @staticmethod
    def _preview_titles(products: list[dict[str, Any]], *, limit: int = 5) -> list[str]:
        titles: list[str] = []
        for product in products[:limit]:
            if not isinstance(product, dict):
                continue
            title = str(product.get("title") or "").strip()
            if title:
                titles.append(title)
        return titles

    @staticmethod
    def _extract_json_object(text: str) -> dict[str, Any]:
        payload = (text or "").strip()
        if not payload:
            raise ValueError("Empty LLM response")

        if payload.startswith("```"):
            payload = payload.strip("`")
            if payload.lower().startswith("json"):
                payload = payload[4:].strip()

        try:
            parsed = json.loads(payload)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

        start = payload.find("{")
        end = payload.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("No JSON object in LLM response")

        parsed = json.loads(payload[start : end + 1])
        if not isinstance(parsed, dict):
            raise ValueError("LLM JSON is not an object")
        return parsed

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        return set(re.findall(r"[a-z0-9]+", (text or "").lower()))

    def _vehicle_tokens(self) -> tuple[str | None, set[str]]:
        raw = str(getattr(self, "_runtime_vehicle_query", "") or "").strip().lower()
        if not raw:
            return None, set()

        year_match = re.search(r"\b(19|20)\d{2}\b", raw)
        year = year_match.group(0) if year_match else None

        tokens = self._tokenize(raw)
        filtered: set[str] = set()
        for token in tokens:
            if token.isdigit():
                continue
            if token in self._VEHICLE_STOP_WORDS:
                continue
            if len(token) < 3:
                continue
            filtered.add(token)
        return year, filtered

    def _vehicle_context_ok(self, haystack: str) -> tuple[bool, dict[str, Any]]:
        year, tokens = self._vehicle_tokens()
        if not year and not tokens:
            return True, {
                "required": False,
                "year_match": None,
                "token_hits": 0,
                "token_total": 0,
                "passed": True,
            }

        blob = (haystack or "").lower()
        year_match = bool(year and year in blob)
        token_hits = len([t for t in tokens if t in blob])
        token_total = len(tokens)

        # Pass if year is found OR if name/make/model tokens appear (year may not
        # appear in category-level URLs like AutoZone's /fuel-pump/chevrolet/silverado-1500).
        passed = False
        if token_total >= 2:
            # Most vehicle tokens present → accept even without year
            passed = token_hits >= max(1, token_total - 1)
        elif year:
            passed = year_match or token_hits >= 1
        else:
            passed = token_hits >= 1

        return passed, {
            "required": True,
            "year": year,
            "year_match": year_match,
            "token_hits": token_hits,
            "token_total": token_total,
            "passed": passed,
        }

    async def _rank_navigation_options_with_llm(
        self,
        *,
        target_query: str,
        options: list[dict[str, Any]],
        trail: list[str],
    ) -> list[int]:
        if not options:
            return []

        base_url = os.getenv("OLLAMA_BASE_URL", "http://golem:11434").rstrip("/")
        model = os.getenv("OLLAMA_MODEL", "deepseek-r1:latest")
        timeout_seconds = float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "45"))

        prompt = {
            "target_query": target_query,
            "current_trail": trail,
            "options": [
                {
                    "index": i,
                    "text": o.get("text"),
                    "href": o.get("href"),
                    "action_type": o.get("action_type"),
                    "source_type": o.get("source_type"),
                }
                for i, o in enumerate(options)
            ],
            "instructions": (
                "Rank option indices from best to worst path toward the target part query. "
                "Prioritize direct product/category routes and avoid generic navigation. "
                "Return JSON only: {\"ranked_indices\": [int, ...]}"
            ),
        }

        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                response = await client.post(
                    f"{base_url}/api/chat",
                    json={
                        "model": model,
                        "stream": False,
                        "messages": [
                            {
                                "role": "system",
                                "content": "You are a deterministic web navigation planner. Return JSON only.",
                            },
                            {"role": "user", "content": json.dumps(prompt, ensure_ascii=True)},
                        ],
                        "options": {"temperature": 0.0},
                    },
                )
                response.raise_for_status()
                content = str(response.json().get("message", {}).get("content") or "")
                parsed = self._extract_json_object(content)
                ranked = parsed.get("ranked_indices")
                if isinstance(ranked, list):
                    clean = [
                        int(i)
                        for i in ranked
                        if isinstance(i, int) and 0 <= i < len(options)
                    ]
                    if clean:
                        return clean
        except Exception:
            pass

        query_tokens = self._tokenize(target_query)
        scored: list[tuple[float, int]] = []
        for i, option in enumerate(options):
            text = str(option.get("text") or "").lower()
            href = str(option.get("href") or "").lower()
            action_type = str(option.get("action_type") or "").lower()
            source_type = str(option.get("source_type") or "").lower()
            overlap = len(query_tokens & self._tokenize(text))
            score = float(overlap * 4)
            if any(t in text for t in query_tokens):
                score += 1.0
            if any(x in href for x in ["catalog", "parts", "brake", "filter", "engine", "search"]):
                score += 0.5
            if action_type in {"breadcrumb", "category", "facet"}:
                score += 0.4
            if source_type in {"breadcrumbs", "category", "filters", "tiles"}:
                score += 0.3
            if any(x in text for x in ["home", "forum", "contact", "help", "policy"]):
                score -= 2.0
            scored.append((score, i))
        scored.sort(reverse=True)
        return [i for _, i in scored]

    async def _collect_navigation_options(self, page: Page) -> list[dict[str, Any]]:
        options = await page.evaluate(
            r"""
() => {
    const out = [];
    const seen = new Set();

    const push = (item) => {
        const text = (item.text || '').replace(/\s+/g, ' ').trim();
        const href = (item.href || '').trim();
        if (!text || !href) return;
        if (text.length < 2 || text.length > 120) return;
        const key = `${text}||${href}||${item.action_type || ''}||${item.source_type || ''}`.toLowerCase();
        if (seen.has(key)) return;
        seen.add(key);
        out.push({
            text,
            href,
            action_type: item.action_type || 'link',
            source_type: item.source_type || 'links',
        });
    };

    const absHref = (href) => {
        if (!href) return '';
        return href.startsWith('http') ? href : new URL(href, window.location.origin).toString();
    };

    for (const a of Array.from(document.querySelectorAll('a[href]'))) {
        const href = absHref(a.getAttribute('href') || '');
        const text = (a.textContent || '').replace(/\s+/g, ' ').trim();
        const inBreadcrumb = Boolean(a.closest('nav[aria-label*="breadcrumb" i], .breadcrumb, [class*="breadcrumb"]'));
        const inFilter = Boolean(a.closest('[class*="facet" i], [class*="filter" i], [data-testid*="filter" i]'));
        const inCategoryTile = Boolean(a.closest('[class*="category" i], [class*="tile" i], [class*="department" i]'));
        push({
            text,
            href,
            action_type: inBreadcrumb ? 'breadcrumb' : (inFilter ? 'facet' : (inCategoryTile ? 'category' : 'link')),
            source_type: inBreadcrumb ? 'breadcrumbs' : (inFilter ? 'filters' : (inCategoryTile ? 'tiles' : 'links')),
        });
    }

    for (const btn of Array.from(document.querySelectorAll('button, [role="button"]'))) {
        const text = (btn.textContent || '').replace(/\s+/g, ' ').trim();
        if (!text) continue;
        const target = btn.closest('a[href]');
        if (!target) continue;
        const href = absHref(target.getAttribute('href') || '');
        push({ text, href, action_type: 'button_link', source_type: 'buttons' });
    }

    for (const input of Array.from(document.querySelectorAll('input[type="checkbox"], input[type="radio"]'))) {
        const id = input.getAttribute('id') || '';
        const labelNode = id ? document.querySelector(`label[for="${id}"]`) : null;
        const text = ((labelNode && labelNode.textContent) || input.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim();
        if (!text) continue;
        const hostLink = input.closest('a[href]') || (labelNode && labelNode.closest('a[href]'));
        if (!hostLink) continue;
        const href = absHref(hostLink.getAttribute('href') || '');
        push({ text, href, action_type: 'facet', source_type: 'filters' });
    }

    return out;
}
"""
        )
        if not isinstance(options, list):
            return []

        filtered: list[dict[str, Any]] = []
        for item in options:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            href = str(item.get("href") or "").strip()
            if not text or not href:
                continue
            path = urlparse(href).path.lower()
            if any(k in path for k in ["/catalog", "/parts", "/search", "/auto-parts", "/en/"]):
                filtered.append(
                    {
                        "text": text,
                        "href": href,
                        "action_type": str(item.get("action_type") or "link"),
                        "source_type": str(item.get("source_type") or "links"),
                    }
                )
        return filtered[:80]

    async def _extract_products_via_dom_script(self, page: Page) -> list[dict[str, Any]]:
        products = await page.evaluate(self.dom_extract_script)
        return products if isinstance(products, list) else []

    async def qualify_page_for_query(
        self,
        page: Page,
        *,
        target_query: str,
        products: list[dict[str, Any]],
    ) -> dict[str, Any]:
        title = await page.title()
        preview_titles = self._preview_titles(products)
        haystack = " ".join([title or "", page.url or "", " ".join(preview_titles)])
        vehicle_ok, vehicle_meta = self._vehicle_context_ok(haystack)
        accepted = bool(products)
        reason = "products_found" if accepted else "no_products"
        if accepted and not vehicle_ok:
            accepted = False
            reason = "wrong_vehicle_context"
        return {
            "accepted": accepted,
            "reason": reason,
            "title": title,
            "url": page.url,
            "product_count": len(products),
            "preview_titles": preview_titles,
            "target_query": target_query,
            "vehicle_query": str(getattr(self, "_runtime_vehicle_query", "") or ""),
            "vehicle_context": vehicle_meta,
        }

    async def _navigate_with_llm_and_backtrack(
        self,
        page: Page,
        *,
        target_query: str,
        seed_hint: str,
    ) -> dict[str, Any]:
        visited: set[str] = {page.url}
        trace: list[dict[str, Any]] = []
        last_qualification: dict[str, Any] = {
            "accepted": False,
            "reason": "no_pages_visited",
            "title": None,
            "url": page.url,
            "product_count": 0,
            "preview_titles": [],
            "target_query": target_query,
        }

        async def dfs(depth: int, trail: list[str]) -> list[dict[str, Any]]:
            nonlocal last_qualification
            products = await self._extract_products_via_dom_script(page)
            qualification = await self.qualify_page_for_query(
                page,
                target_query=target_query,
                products=products,
            )
            last_qualification = qualification
            trace.append(
                {
                    "depth": depth,
                    "trail": list(trail),
                    "url": page.url,
                    "title": qualification.get("title"),
                    "product_count": len(products),
                    "accepted": bool(qualification.get("accepted")),
                    "reason": qualification.get("reason"),
                    "preview_titles": qualification.get("preview_titles", []),
                }
            )

            if products and bool(qualification.get("accepted")):
                return products
            if depth >= self.MAX_NAV_DEPTH:
                return []

            options = await self._collect_navigation_options(page)
            if not options:
                return []

            ranked = await self._rank_navigation_options_with_llm(
                target_query=f"{seed_hint} {target_query}".strip(),
                options=options,
                trail=trail,
            )
            if not ranked:
                return []

            current_url = page.url
            for idx in ranked[: self.MAX_NAV_OPTIONS]:
                option = options[idx]
                next_url = option.get("href") or ""
                if not next_url or next_url in visited:
                    continue
                visited.add(next_url)
                next_text = str(option.get("text") or "")
                try:
                    await page.goto(next_url, wait_until="domcontentloaded", timeout=45_000)
                    await page.wait_for_timeout(1200)
                except Exception:
                    try:
                        await page.goto(current_url, wait_until="domcontentloaded", timeout=45_000)
                    except Exception:
                        pass
                    continue

                found = await dfs(depth + 1, trail + [next_text])
                if found:
                    return found

                try:
                    await page.goto(current_url, wait_until="domcontentloaded", timeout=45_000)
                    await page.wait_for_timeout(800)
                except Exception:
                    pass
            return []

        return {
            "products": await dfs(0, []),
            "trace": trace,
            "qualification": last_qualification,
        }

    async def fetch_supplemental_data(self, page, products, search_term, httpx_client):
        """Run legacy supplemental flow first, then LLM-guided nav fallback when needed."""
        super_fetch = getattr(super(), "fetch_supplemental_data", None)
        if callable(super_fetch):
            supplemental = await super_fetch(page, products, search_term, httpx_client)
        else:
            supplemental = {}
        if supplemental is None:
            supplemental = {}
        qualification = await self.qualify_page_for_query(
            page,
            target_query=search_term,
            products=products,
        )
        supplemental = dict(supplemental or {})
        supplemental["page_qualification"] = qualification
        if products and bool(qualification.get("accepted")):
            return supplemental

        navigation_result = await self._navigate_with_llm_and_backtrack(
            page,
            target_query=search_term,
            seed_hint=search_term,
        )
        llm_guided_products = navigation_result.get("products")
        supplemental["llm_guided_products"] = llm_guided_products
        supplemental["llm_guided_used"] = True
        supplemental["llm_guided_trace"] = navigation_result.get("trace", [])
        supplemental["llm_guided_final_qualification"] = navigation_result.get(
            "qualification"
        )
        return supplemental

    def enrich_products(self, products, supplemental):
        llm_guided_products = (supplemental or {}).get("llm_guided_products")
        if isinstance(llm_guided_products, list) and llm_guided_products:
            return llm_guided_products

        page_qualification = (supplemental or {}).get("page_qualification")
        if isinstance(page_qualification, dict) and not bool(page_qualification.get("accepted")):
            # Explicitly reject wrong-category pages instead of silently passing
            # through non-empty but invalid product sets.
            return []

        super_enrich = getattr(super(), "enrich_products", None)
        if callable(super_enrich):
            enriched = super_enrich(products, supplemental)
            if enriched is not None:
                return enriched
        return products
