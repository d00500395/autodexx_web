"""
Base adapter interface for retailer scrapers.

Every adapter must provide:
  - domain          class-level str, e.g. "oreillyauto.com"
  - dom_extract_script   JS string evaluated in the page context; must return a
                         list of product dicts, each containing at minimum:
                           title, price, href
                         plus any retailer-specific fields.
  - score_tavily_result  optional URL scoring tweak for Tavily candidate ranking
  - fetch_supplemental_data  called while the browser page is still open;
                              returns a plain dict that is stored verbatim in
                              output["attempts"].
  - enrich_products       joins the supplemental dict back onto each product.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import re
from typing import Any

import httpx
from playwright.async_api import Page


class RetailerAdapter(ABC):
    # Subclasses must set this at class level.
    domain: str

    # ------------------------------------------------------------------ #
    # DOM extraction                                                       #
    # ------------------------------------------------------------------ #

    @property
    @abstractmethod
    def dom_extract_script(self) -> str:
        """
        JavaScript expression (arrow function) that runs in the page context
        and returns a list of product dicts.
        Minimum required keys per product: title, price, href.
        """

    # ------------------------------------------------------------------ #
    # Tavily scoring                                                       #
    # ------------------------------------------------------------------ #

    _PART_TOKEN_MIN_LEN = 2
    _PART_STOP_WORDS = {
        "a",
        "an",
        "and",
        "for",
        "with",
        "the",
        "to",
        "of",
        "by",
        "in",
        "on",
    }

    def _tokenize_part_query(self, part_query: str | None) -> list[str]:
        raw = str(part_query or "").lower()
        if not raw:
            return []

        tokens = re.findall(r"[a-z0-9]+", raw)
        return [
            t
            for t in tokens
            if len(t) >= self._PART_TOKEN_MIN_LEN and t not in self._PART_STOP_WORDS
        ]

    def _part_term_bonus(
        self,
        result: dict[str, Any],
        *,
        part_query: str | None = None,
        per_word_bonus: float = 0.04,
    ) -> float:
        tokens = self._tokenize_part_query(part_query)
        if not tokens:
            return 0.0

        url = str(result.get("url") or "").lower()
        title = str(result.get("title") or "").lower()
        haystack = f"{url} {title}"

        bonus = 0.0
        for token in tokens:
            if token in haystack:
                bonus += per_word_bonus
        return bonus

    def score_tavily_result(
        self,
        result: dict[str, Any],
        *,
        part_query: str | None = None,
        vehicle_query: str | None = None,
    ) -> float:
        """
        Score a Tavily search result for this retailer.
        Higher scores are preferred.  The base implementation just uses the
        raw Tavily score; override to add URL-pattern bonuses/penalties.
        """
        _ = vehicle_query
        score = float(result.get("score") or 0.0)
        score += self._part_term_bonus(result, part_query=part_query)
        return score

    # ------------------------------------------------------------------ #
    # Supplemental API calls                                              #
    # ------------------------------------------------------------------ #

    @abstractmethod
    async def fetch_supplemental_data(
        self,
        page: Page,
        products: list[dict[str, Any]],
        search_term: str,
        httpx_client: httpx.AsyncClient,
    ) -> dict[str, Any]:
        """
        Make any additional API/network calls specific to this retailer while
        the browser page is still open and cookies/session are live.

        Return a plain dict; whatever is returned here goes into
        output["attempts"] verbatim and is also passed to enrich_products().

        Return {} if the retailer has no known supplemental APIs yet.
        """

    # ------------------------------------------------------------------ #
    # Product enrichment                                                  #
    # ------------------------------------------------------------------ #

    @abstractmethod
    def enrich_products(
        self,
        products: list[dict[str, Any]],
        supplemental: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """
        Join supplemental data back onto the raw product list.
        Return the enriched list.  The base implementation just returns
        products unchanged; override once you know what fields to attach.
        """
