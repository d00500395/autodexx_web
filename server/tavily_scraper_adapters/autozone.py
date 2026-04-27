"""LLM-guided AutoZone adapter."""

from __future__ import annotations

from .legacy.autozone import AutoZoneAdapter as LegacyAutoZoneAdapter
from .llm_guided import LLMGuidedAdapterMixin


class AutoZoneLLMAdapter(LLMGuidedAdapterMixin, LegacyAutoZoneAdapter):
    """AutoZone adapter with LLM-guided in-page navigation fallback."""

    _FUEL_PUMP_POSITIVE_MARKERS = (
        "fuel pump",
        "fuel pump module",
        "module assembly",
        "electric fuel pump",
        "mechanical fuel pump",
    )
    _FUEL_PUMP_NEGATIVE_MARKERS = (
        "fuel filter",
        "air filter",
        "fuel injector",
        "relay",
    )

    async def qualify_page_for_query(self, page, *, target_query, products):
        qualification = await super().qualify_page_for_query(
            page,
            target_query=target_query,
            products=products,
        )

        query_text = str(target_query or "").strip().lower()
        if not ("fuel" in query_text and "pump" in query_text):
            return qualification

        preview_blob = " ".join(
            str(t).lower() for t in qualification.get("preview_titles", [])
        )
        haystack = " ".join(
            [
                str(qualification.get("title") or "").lower(),
                str(qualification.get("url") or "").lower(),
                preview_blob,
            ]
        )

        if any(marker in haystack for marker in self._FUEL_PUMP_NEGATIVE_MARKERS):
            qualification["accepted"] = False
            qualification["reason"] = "wrong_category_non_pump"
            return qualification

        if products and not any(
            marker in haystack for marker in self._FUEL_PUMP_POSITIVE_MARKERS
        ):
            qualification["accepted"] = False
            qualification["reason"] = "wrong_category_non_pump"

        return qualification
