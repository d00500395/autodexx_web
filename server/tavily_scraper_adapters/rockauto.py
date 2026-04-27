"""LLM-guided RockAuto adapter for Agent 3.0."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

from .legacy.base import RetailerAdapter
from .llm_guided import LLMGuidedAdapterMixin


class RockAutoLLMAdapter(LLMGuidedAdapterMixin, RetailerAdapter):
    domain = "rockauto.com"

    _FUEL_PUMP_POSITIVE_MARKERS = (
        "fuel pump",
        "fuel pump module",
        "fuel pump assembly",
        "pump module",
        "module assembly",
    )
    _FUEL_PUMP_NEGATIVE_MARKERS = (
        "relay",
        "circuit opening relay",
        "electrical-switch",
        "electrical switch",
        "switch & relay",
    )

    @property
    def dom_extract_script(self) -> str:
        return r"""
() => {
    const parts = [];
    const seen = new Set();

    const categoryHint = ((document.title || '').match(/\b(Rotor(?:\s*&\s*Brake\s*Pad\s*Kit)?|Brake\s*Pad|Spark\s*Plug|Oil\s*Filter)\b/i) || [])[0] || 'Part';

    // RockAuto reliably marks product identity with these classes.
    const partNodes = Array.from(document.querySelectorAll('.listing-final-partnumber'));
    for (const node of partNodes) {
        const row = node.closest('tr');
        if (!row) continue;

        const partNum = (node.textContent || '').replace(/\s+/g, ' ').trim() || null;
        if (!partNum) continue;

        const manufacturerNode = row.querySelector('.listing-final-manufacturer');
        const brand = (manufacturerNode?.textContent || '').replace(/\s+/g, ' ').trim() || null;
        if (!brand) continue;

        const infoLink = row.querySelector('a.ra-btn-moreinfo[href*="moreinfo.php?pk="]');
        const rawHref = infoLink?.getAttribute('href') || '';
        const href = rawHref
            ? (rawHref.startsWith('http') ? rawHref : new URL(rawHref, window.location.origin).toString())
            : null;
        if (!href) continue;

        const cluster = row.parentElement || row;
        const clusterText = (cluster.textContent || '').replace(/\s+/g, ' ').trim();
        const priceMatch = clusterText.match(/\$\s*([0-9]+(?:\.[0-9]{1,2})?)/);
        if (!priceMatch) continue;

        const fitmentNote = (row.querySelector('.listing-text-row')?.textContent || '')
            .replace(/\s+/g, ' ')
            .trim();
        const shortFitment = fitmentNote && fitmentNote.length <= 120 ? fitmentNote : null;
        const title = [brand, partNum, categoryHint].filter(Boolean).join(' ');
        const key = `${partNum}|${href}|${priceMatch[1]}`.toLowerCase();
        if (seen.has(key)) continue;
        seen.add(key);

        parts.push({
            title,
            brand,
            partNum,
            price: priceMatch[1],
            currency: 'USD',
            href,
            availability: 'Check Website',
            description: shortFitment || title,
        });
    }

    return parts;
}
"""

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

        if "/en/catalog/" in path:
            score += 0.25
        if "/catalog/" in path:
            score += 0.12
        if "/search/" in path:
            score += 0.06
        if "partnum" in query:
            score -= 0.15
        if any(k in path for k in ["/forum", "/news", "/about", "/contact"]):
            score -= 0.45

        query_text = (part_query or "").strip().lower()
        if "fuel" in query_text and "pump" in query_text:
            if "relay" in path or "circuit+opening+relay" in path:
                score -= 1.2
            if "fuel+pump" in path and "relay" not in path:
                score += 0.18
        return score

    async def qualify_page_for_query(
        self,
        page,
        *,
        target_query: str,
        products: list[dict[str, Any]],
    ) -> dict[str, Any]:
        qualification = await super().qualify_page_for_query(
            page,
            target_query=target_query,
            products=products,
        )

        query_text = (target_query or "").strip().lower()
        if not ("fuel" in query_text and "pump" in query_text):
            return qualification

        title = str(qualification.get("title") or "").lower()
        url = str(qualification.get("url") or "").lower()
        preview_blob = " ".join(
            str(t).lower() for t in qualification.get("preview_titles", [])
        )
        haystack = " ".join([title, url, preview_blob])

        if any(marker in haystack for marker in self._FUEL_PUMP_NEGATIVE_MARKERS):
            qualification["accepted"] = False
            qualification["reason"] = "wrong_category_relay"
            return qualification

        if products and not any(
            marker in haystack for marker in self._FUEL_PUMP_POSITIVE_MARKERS
        ):
            qualification["accepted"] = False
            qualification["reason"] = "wrong_category_non_pump"

        return qualification
