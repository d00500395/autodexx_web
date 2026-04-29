"""
Agent 3.0 – multi-retailer scraper.

Run:
    python agent_3.0_domain_configurable.py --domain oreillyauto.com \
        --vehicle-query "1999 Chevrolet Silverado 1500" --part-query "brake rotor"

    python agent_3.0_domain_configurable.py --list-domains

To add a new retailer, create retailer_adapters/<name>.py, subclass
RetailerAdapter, and register it in retailer_adapters/__init__.py.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

logger = logging.getLogger(__name__)

import httpx
from dotenv import load_dotenv
from playwright.async_api import BrowserContext, Page, async_playwright
from tavily import AsyncTavilyClient

from retailer_adapters import get_adapter, list_domains

load_dotenv()

OUTPUT_DIR = Path(__file__).with_name("api_intercept_outputs")
USER_DATA_DIR = OUTPUT_DIR / "chrome_profile"
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
TAVILY_RESULTS = int(os.getenv("TAVILY_RESULTS", "8"))
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://golem:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gpt-oss:20b")
OLLAMA_TIMEOUT_SECONDS = float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "120"))

DEFAULT_DOMAIN = "oreillyauto.com"
DEFAULT_SEARCH_TERM = "brake rotor"
DEFAULT_VEHICLE_QUERY = "1999 Chevrolet Silverado 1500"
# Hard cap: each retailer gets at most 2 processing runs (initial + 1 rerun).
MAX_RUNS_PER_RETAILER = 2
MAX_LLM_RERUNS = MAX_RUNS_PER_RETAILER - 1
MAX_BROWSER_ATTEMPTS = 3
BROWSER_BACKOFF_SECONDS = 2.5

_MONEY_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)")
_COMPACT_FIELDS = [
    "title",
    "brand",
    "partNum",
    "price",
    "currency",
    "href",
    "availability",
]
_PART_TYPE_STOP_WORDS = {"for", "and", "the", "a", "an", "of", "with"}
_PART_TYPE_OPTIONAL_QUERY_TOKENS = {
    "front",
    "rear",
    "left",
    "right",
    "driver",
    "passenger",
    "side",
}


def _env_true(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


async def _launch_browser_context(
    pw,
    *,
    user_data_dir: str,
    headless: bool,
) -> tuple[BrowserContext, str]:
    launch_kwargs = {
        "user_data_dir": user_data_dir,
        "headless": headless,
        "locale": "en-US",
        "viewport": {"width": 1600, "height": 900},
        "args": ["--disable-blink-features=AutomationControlled"],
    }

    preferred_channel = (os.getenv("PLAYWRIGHT_BROWSER_CHANNEL", "chrome") or "").strip().lower()
    if preferred_channel in {"", "none", "chromium"}:
        context = await pw.chromium.launch_persistent_context(**launch_kwargs)
        return context, "chromium"

    try:
        context = await pw.chromium.launch_persistent_context(
            channel=preferred_channel,
            **launch_kwargs,
        )
        return context, preferred_channel
    except Exception:
        if _env_true("REQUIRE_BROWSER_CHANNEL"):
            raise
        context = await pw.chromium.launch_persistent_context(**launch_kwargs)
        return context, "chromium"


def _resolve_browser_headless_mode(*, requested_headless: bool, is_api_based: bool) -> bool:
    if is_api_based:
        return requested_headless
    if os.getenv("ALLOW_HEADLESS_BROWSER_DOMAINS", "").strip().lower() in {"1", "true", "yes"}:
        return requested_headless
    return False


def _reset_browser_profile_dir(user_data_dir: str | None) -> None:
    if not user_data_dir:
        return
    path = Path(user_data_dir)
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def _browser_attempt_blocked(
    *,
    title: str | None,
    products: list[dict[str, Any]],
    supplemental: dict[str, Any] | None,
) -> bool:
    title_text = str(title or "").lower()
    if "access denied" in title_text or "attention required" in title_text:
        return True

    supplemental = supplemental or {}
    if bool(supplemental.get("blocked_by_challenge")):
        return True

    dom_probe = supplemental.get("page_fetch", {}).get("dom_probe", {}).get("body")
    if isinstance(dom_probe, dict) and bool(dom_probe.get("blocked_by_challenge")):
        return True

    return len(products) == 0


def _browser_attempt_error_result(exc: Exception) -> dict[str, Any]:
    return {
        "status": 0,
        "content_type": "",
        "body": f"{exc.__class__.__name__}: {exc}",
    }


def _compose_tavily_search_query(vehicle_query: str, part_query: str) -> str:
    vehicle_text = (vehicle_query or "").strip()
    part_text = (part_query or "").strip()
    return " ".join(x for x in [vehicle_text, part_text] if x).strip()


def _compact_product(product: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": product.get("title"),
        "brand": product.get("brand") or product.get("seller"),
        "partNum": product.get("partNum") or product.get("itemId"),
        "price": product.get("price"),
        "currency": product.get("currency", "USD"),
        "href": product.get("href"),
        "availability": product.get("availability"),
    }


def _default_base_url_for_domain(domain: str | None) -> str:
    normalized = _normalize_domain(str(domain or "")) if domain else ""
    return f"https://www.{normalized}/" if normalized else ""


def _normalize_href(href: Any, *, base_url: str | None = None, domain: str | None = None) -> Any:
    raw = str(href or "").strip()
    if not raw:
        return href
    if raw.startswith(("http://", "https://")):
        return raw

    resolved_base = str(base_url or "").strip() or _default_base_url_for_domain(domain)
    if not resolved_base:
        return raw

    try:
        return urljoin(resolved_base, raw)
    except Exception:
        return raw


def _normalize_product_urls(
    products: list[dict[str, Any]],
    *,
    base_url: str | None = None,
    domain: str | None = None,
) -> list[dict[str, Any]]:
    normalized_products: list[dict[str, Any]] = []
    for product in products:
        if not isinstance(product, dict):
            continue
        normalized_product = dict(product)
        normalized_product["href"] = _normalize_href(
            normalized_product.get("href"),
            base_url=base_url,
            domain=domain,
        )
        normalized_products.append(normalized_product)
    return normalized_products


def _normalize_llm_review_urls(
    review: dict[str, Any],
    *,
    base_url: str | None = None,
    domain: str | None = None,
) -> dict[str, Any]:
    normalized_review = dict(review)
    normalized_review["matched_products"] = _normalize_product_urls(
        list(review.get("matched_products") or []),
        base_url=base_url,
        domain=domain,
    )

    normalized_tagged: list[dict[str, Any]] = []
    for item in list(review.get("tagged_products") or []):
        if not isinstance(item, dict):
            continue
        normalized_item = dict(item)
        product = item.get("product")
        if isinstance(product, dict):
            normalized_item["product"] = _normalize_product_urls(
                [product],
                base_url=base_url,
                domain=domain,
            )[0]
        normalized_tagged.append(normalized_item)
    normalized_review["tagged_products"] = normalized_tagged
    return normalized_review


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


def _to_price_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    match = _MONEY_RE.search(str(value).replace(",", ""))
    if not match:
        return None
    try:
        return float(match.group(1))
    except Exception:
        return None


def _fallback_llm_decision(compact_products: list[dict[str, Any]]) -> dict[str, Any]:
    if not compact_products:
        return {
            "matched_products": [],
            "tagged_products": [],
            "should_rerun": False,
            "updated_part_query": "",
        }

    matched = compact_products
    recommended = matched[0]

    priced = [(p, _to_price_number(p.get("price"))) for p in matched]
    priced = [(p, v) for p, v in priced if v is not None]

    if priced:
        lowest_price_product = min(priced, key=lambda x: x[1])[0]
        premium_product = max(priced, key=lambda x: x[1])[0]
    else:
        lowest_price_product = recommended
        premium_product = matched[-1]

    return {
        "matched_products": matched,
        "tagged_products": [
            {"tag": "lowest price", "product": lowest_price_product},
            {"tag": "recommended", "product": recommended},
            {"tag": "premium", "product": premium_product},
        ],
        "should_rerun": False,
        "updated_part_query": "",
    }


def _ensure_required_tags(
    decision: dict[str, Any],
    *,
    fallback_products: list[dict[str, Any]],
) -> dict[str, Any]:
    required_tags = ["lowest price", "recommended", "premium"]

    matched_products = decision.get("matched_products")
    if not isinstance(matched_products, list):
        matched_products = []

    candidates = [p for p in matched_products if isinstance(p, dict)]
    if not candidates:
        candidates = [p for p in fallback_products if isinstance(p, dict)]
    if not candidates:
        decision["tagged_products"] = []
        return decision

    tagged_products = decision.get("tagged_products")
    if not isinstance(tagged_products, list):
        tagged_products = []

    by_tag: dict[str, dict[str, Any]] = {}
    for item in tagged_products:
        if not isinstance(item, dict):
            continue
        tag = str(item.get("tag") or "").strip().lower()
        product = item.get("product")
        if tag in required_tags and isinstance(product, dict):
            by_tag[tag] = {k: product.get(k) for k in _COMPACT_FIELDS}

    priced: list[tuple[dict[str, Any], float]] = []
    for product in candidates:
        price_val = _to_price_number(product.get("price"))
        if price_val is not None:
            priced.append((product, price_val))

    if "recommended" not in by_tag:
        by_tag["recommended"] = {k: candidates[0].get(k) for k in _COMPACT_FIELDS}
    if "lowest price" not in by_tag:
        low_src = min(priced, key=lambda x: x[1])[0] if priced else candidates[0]
        by_tag["lowest price"] = {k: low_src.get(k) for k in _COMPACT_FIELDS}
    if "premium" not in by_tag:
        high_src = max(priced, key=lambda x: x[1])[0] if priced else candidates[-1]
        by_tag["premium"] = {k: high_src.get(k) for k in _COMPACT_FIELDS}

    decision["tagged_products"] = [
        {"tag": tag, "product": by_tag[tag]} for tag in required_tags
    ]
    return decision


async def _review_products_with_ollama(
    httpx_client: httpx.AsyncClient,
    *,
    vehicle_query: str,
    part_query: str,
    compact_products: list[dict[str, Any]],
) -> dict[str, Any]:
    system_prompt = (
        "You are an automotive parts validator. Return JSON only. "
        "Filter products so only entries exactly matching the vehicle query with the vehicle fitment for the product"
        "AND matching the part query with the part title. "
        "From remaining entries, return exactly these tags when possible: "
        "lowest price, recommended, premium. "
        "Use recommended for the best overall practical choice (often early list item). "
        "If zero entries match, or if the part query does not match the product category or the product itself, set should_rerun=true"
        "and propose updated_part_query that better targets the user's requested part."
    )

    user_prompt = {
        "vehicle_query": vehicle_query,
        "part_query": part_query,
        "products": compact_products,
        "required_output_schema": {
            "matched_products": [
                {
                    "title": "string|null",
                    "brand": "string|null",
                    "partNum": "string|null",
                    "price": "string|number|null",
                    "currency": "string|null",
                    "href": "string|null",
                    "availability": "string|null",
                }
            ],
            "tagged_products": [
                {
                    "tag": "lowest price|recommended|premium",
                    "product": {
                        "title": "string|null",
                        "brand": "string|null",
                        "partNum": "string|null",
                        "price": "string|number|null",
                        "currency": "string|null",
                        "href": "string|null",
                        "availability": "string|null",
                    },
                }
            ],
            "should_rerun": "boolean",
            "updated_part_query": "string",
            "notes": "string",
        },
    }

    payload = {
        "model": OLLAMA_MODEL,
        "stream": False,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_prompt, ensure_ascii=True)},
        ],
        "options": {"temperature": 0.0},
    }

    response = await httpx_client.post(
        f"{OLLAMA_BASE_URL}/api/chat",
        json=payload,
        timeout=OLLAMA_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    raw = response.json()
    content = str(raw.get("message", {}).get("content") or "")
    decision = _extract_json_object(content)

    matched = decision.get("matched_products")
    tagged = decision.get("tagged_products")
    should_rerun = bool(decision.get("should_rerun"))
    updated_part_query = str(decision.get("updated_part_query") or "").strip()

    if not isinstance(matched, list):
        matched = []
    if not isinstance(tagged, list):
        tagged = []

    normalized_matched = []
    for item in matched:
        if isinstance(item, dict):
            normalized_matched.append({k: item.get(k) for k in _COMPACT_FIELDS})

    normalized_tagged = []
    for item in tagged:
        if not isinstance(item, dict):
            continue
        tag = str(item.get("tag") or "").strip().lower()
        product = item.get("product")
        if tag not in {"lowest price", "recommended", "premium"}:
            continue
        if not isinstance(product, dict):
            continue
        normalized_tagged.append(
            {
                "tag": tag,
                "product": {k: product.get(k) for k in _COMPACT_FIELDS},
            }
        )

    return {
        "matched_products": normalized_matched,
        "tagged_products": normalized_tagged,
        "should_rerun": should_rerun,
        "updated_part_query": updated_part_query,
        "raw": decision,
    }


# --------------------------------------------------------------------------- #
# Domain normalisation                                                         #
# --------------------------------------------------------------------------- #

def _normalize_domain(domain: str) -> str:
    raw = (domain or "").strip()
    if not raw:
        raise ValueError("Domain cannot be empty")
    if "://" in raw:
        host = urlparse(raw).netloc
    else:
        host = raw
    host = host.strip().lower().strip("/")
    if host.startswith("www."):
        host = host[4:]
    if not host:
        raise ValueError("Invalid domain")
    return host


# --------------------------------------------------------------------------- #
# Tavily page discovery                                                        #
# --------------------------------------------------------------------------- #

async def _tavily_find_target_page(
    vehicle_query: str,
    part_query: str,
    domain: str,
    score_fn,
) -> dict[str, Any]:
    if not TAVILY_API_KEY:
        raise RuntimeError("Missing TAVILY_API_KEY")

    tavily_query = _compose_tavily_search_query(vehicle_query, part_query)

    client = AsyncTavilyClient(api_key=TAVILY_API_KEY)
    data = await client.search(
        query=tavily_query,
        search_depth="advanced",
        include_domains=[domain],
        max_results=TAVILY_RESULTS,
    )
    results = data.get("results", [])

    if not results:
        raise RuntimeError(f"Tavily returned no results for domain '{domain}'")

    def _score(item: dict[str, Any]) -> float:
        return float(
            score_fn(
                item,
                part_query=part_query,
                vehicle_query=vehicle_query,
            )
        )

    ranked = sorted(results, key=_score, reverse=True)
    best = ranked[0]

    return {
        "vehicle_query": vehicle_query,
        "part_query": part_query,
        "selected_url": best.get("url"),
        "selected_title": best.get("title"),
        "candidates": [
            {
                "url": item.get("url"),
                "title": item.get("title"),
                "score": item.get("score"),
                "rank_score": _score(item),
            }
            for item in ranked[:8]
        ],
    }


# --------------------------------------------------------------------------- #
# Browser bootstrap (shared across all retailers)                             #
# --------------------------------------------------------------------------- #

async def _bootstrap_page(
    page: Page,
    target_url: str,
    dom_extract_script: str,
    *,
    domain: str | None = None,
    part_query: str | None = None,
) -> dict[str, Any]:
    parsed = urlparse(target_url)
    target_origin = (
        f"{parsed.scheme}://{parsed.netloc}"
        if parsed.scheme and parsed.netloc
        else target_url
    )

    await page.goto(target_origin, wait_until="load", timeout=90_000)
    await page.wait_for_timeout(4_000)
    await page.goto(target_url, wait_until="domcontentloaded", timeout=90_000)
    await page.wait_for_timeout(8_000)

    part_type_filter = None
    if (domain or "").strip().lower() == "napaonline.com":
        part_type_filter = await _navigate_napa_category_page(page, part_query or "")
        facet_result = await _apply_napa_part_type_filter(page, part_query or "")
        if isinstance(part_type_filter, dict):
            part_type_filter["facet_filter"] = facet_result
        else:
            part_type_filter = {"facet_filter": facet_result}
        if part_type_filter.get("applied"):
            try:
                await page.wait_for_load_state("networkidle", timeout=10_000)
            except Exception:
                pass
            await page.wait_for_timeout(2_000)

    for _ in range(4):
        await page.mouse.wheel(0, 2200)
        await page.wait_for_timeout(1_400)

    csrf = await page.evaluate(
        """
() =>
    document.querySelector('meta[name="_csrf"]')?.getAttribute('content')
    || document.querySelector('meta[name="csrf-token"]')?.getAttribute('content')
    || null
"""
    )
    user_agent = await page.evaluate("() => navigator.userAgent")
    products = await page.evaluate(dom_extract_script)

    return {
        "csrf": csrf,
        "user_agent": user_agent,
        "products": products if isinstance(products, list) else [],
        "landing_url": target_origin,
        "final_url": page.url,
        "title": await page.title(),
        "part_type_filter": part_type_filter,
    }


def _normalize_part_type_token(token: str) -> str:
    t = token.strip().lower()
    if len(t) > 3 and t.endswith("s"):
        t = t[:-1]
    return t


def _tokenize_part_type_text(text: str) -> set[str]:
    raw_tokens = re.findall(r"[a-z0-9]+", (text or "").lower())
    tokens: set[str] = set()
    for token in raw_tokens:
        if token in _PART_TYPE_STOP_WORDS:
            continue
        tokens.add(_normalize_part_type_token(token))
    return {t for t in tokens if t}


def _score_part_type_option(part_query: str, option_text: str) -> float:
    q = (part_query or "").strip().lower()
    o = (option_text or "").strip().lower()
    if not q or not o:
        return 0.0

    q_tokens = _tokenize_part_type_text(q)
    o_tokens = _tokenize_part_type_text(o)
    if not q_tokens or not o_tokens:
        return 0.0

    required_tokens = {
        t for t in q_tokens if t not in _PART_TYPE_OPTIONAL_QUERY_TOKENS
    } or set(q_tokens)

    overlap = required_tokens & o_tokens
    missing_required = required_tokens - o_tokens
    extra_option_terms = o_tokens - required_tokens

    score = 0.0

    # Strongly prefer options that contain all core query terms.
    if not missing_required:
        score += 30.0
    else:
        score -= float(len(missing_required) * 8)

    # Reward overlap of core terms.
    score += float(len(overlap) * 6)

    # Penalize option-specific terms not requested by the query
    # (e.g., "cover" in "brake caliper cover").
    score -= float(len(extra_option_terms) * 4)

    if o in q or q in o:
        score += 4.0

    return score


def _simplify_napa_category_query(part_query: str) -> str:
    raw = (part_query or "").strip().lower()
    if not raw:
        return ""

    split_markers = [" for ", " fits ", " fit "]
    simplified = raw
    for marker in split_markers:
        if marker in simplified:
            simplified = simplified.split(marker, 1)[0].strip()
            break

    simplified = re.sub(r"\b(19|20)\d{2}\b", " ", simplified)
    simplified = re.sub(r"\s+", " ", simplified).strip()
    return simplified or raw


async def _collect_napa_category_links(page: Page) -> list[dict[str, str]]:
    links = await page.evaluate(
        r'''
() => {
    const out = [];
    const seen = new Set();
    const current = new URL(window.location.href);

    const push = (text, href) => {
        const cleanText = (text || '').replace(/\s+/g, ' ').trim();
        if (!cleanText) return;
        let absHref = '';
        try {
            absHref = href.startsWith('http') ? href : new URL(href, window.location.origin).toString();
        } catch (_) {
            return;
        }
        if (!absHref || absHref === current.toString()) return;
        const url = new URL(absHref);
        const path = url.pathname.toLowerCase();
        if (!path.includes('/napaonline.com') && url.hostname !== current.hostname) return;
        if (!path.includes('/en/') && !path.includes('/shop/')) return;
        if (path.includes('/p/')) return;
        const key = `${cleanText}||${url.pathname}`.toLowerCase();
        if (seen.has(key)) return;
        seen.add(key);
        out.push({ text: cleanText, href: url.toString() });
    };

    for (const a of Array.from(document.querySelectorAll('a[href]'))) {
        const href = a.getAttribute('href') || '';
        const text = a.textContent || a.getAttribute('aria-label') || '';
        push(text, href);
    }

    return out;
}
'''
    )

    if not isinstance(links, list):
        return []

    filtered: list[dict[str, str]] = []
    for item in links:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        href = str(item.get("href") or "").strip()
        if not text or not href:
            continue
        path = urlparse(href).path.lower()
        if any(token in path for token in ["/replacement-parts/", "/fuel", "/tank", "/pump", "/engine/"]):
            filtered.append({"text": text, "href": href})

    return filtered[:60]


async def _rank_napa_category_links_with_ollama(
    *,
    part_query: str,
    options: list[dict[str, str]],
) -> dict[str, Any] | None:
    if not options:
        return None

    simplified_query = _simplify_napa_category_query(part_query)
    prompt = {
        "part_query": simplified_query,
        "options": [
            {"index": index, "text": option.get("text"), "href": option.get("href")}
            for index, option in enumerate(options)
        ],
        "instructions": (
            "Choose the single best category page for the requested automotive part. "
            "Prefer direct category pages for the part itself and avoid generic vehicle hubs or unrelated categories. "
            "Return JSON only: {\"selected_index\": int, \"reason\": string}."
        ),
    }

    try:
        async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT_SECONDS) as httpx_client:
            response = await httpx_client.post(
                f"{OLLAMA_BASE_URL}/api/chat",
                json={
                    "model": OLLAMA_MODEL,
                    "stream": False,
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are a deterministic retail category selector. Return JSON only.",
                        },
                        {"role": "user", "content": json.dumps(prompt, ensure_ascii=True)},
                    ],
                    "options": {"temperature": 0.0},
                },
            )
            response.raise_for_status()
            content = str(response.json().get("message", {}).get("content") or "")
            parsed = _extract_json_object(content)
            selected_index = parsed.get("selected_index")
            if isinstance(selected_index, int) and 0 <= selected_index < len(options):
                return {
                    "selected": options[selected_index],
                    "selected_index": selected_index,
                    "reason": str(parsed.get("reason") or ""),
                    "query": simplified_query,
                    "option_count": len(options),
                }
    except Exception:
        pass

    best_option = None
    best_index = -1
    best_score = 0.0
    for index, option in enumerate(options):
        score = _score_part_type_option(simplified_query, str(option.get("text") or ""))
        if score > best_score:
            best_score = score
            best_option = option
            best_index = index

    if best_option is None or best_score <= 0:
        return None

    return {
        "selected": best_option,
        "selected_index": best_index,
        "reason": "fallback_overlap",
        "query": simplified_query,
        "score": round(best_score, 3),
        "option_count": len(options),
    }


async def _navigate_napa_category_page(page: Page, part_query: str) -> dict[str, Any]:
    path = urlparse(page.url).path.lower()
    if "/replacement-parts/" in path or "/shop/" in path:
        return {"attempted": False, "navigated": False, "reason": "already_deep_category"}

    options = await _collect_napa_category_links(page)
    if not options:
        return {"attempted": True, "navigated": False, "reason": "no_category_links", "option_count": 0}

    decision = await _rank_napa_category_links_with_ollama(
        part_query=part_query,
        options=options,
    )
    if not decision:
        return {
            "attempted": True,
            "navigated": False,
            "reason": "no_category_decision",
            "option_count": len(options),
        }

    selected = decision.get("selected") or {}
    href = str(selected.get("href") or "").strip()
    if not href:
        return {
            "attempted": True,
            "navigated": False,
            "reason": "missing_selected_href",
            "option_count": len(options),
        }

    await page.goto(href, wait_until="domcontentloaded", timeout=90_000)
    try:
        await page.wait_for_load_state("networkidle", timeout=10_000)
    except Exception:
        pass
    await page.wait_for_timeout(3_000)

    return {
        "attempted": True,
        "navigated": True,
        "selected": selected,
        "selected_index": decision.get("selected_index"),
        "reason": decision.get("reason"),
        "query": decision.get("query"),
        "option_count": decision.get("option_count", len(options)),
        "final_url": page.url,
    }


async def _apply_napa_part_type_filter(page: Page, part_query: str) -> dict[str, Any]:
    options = await page.evaluate(
        """
() => {
    const nodes = Array.from(
        document.querySelectorAll('input.geo-facet-field[data-facet-group*="application_part_type"]')
    );
    return nodes
        .map((input) => {
            const id = input.getAttribute('id') || '';
            const value = input.getAttribute('value') || '';
            const label = id ? document.querySelector(`label[for="${id}"]`) : null;
            const labelText = (label?.textContent || value || '').trim();
            return {
                id,
                value,
                label: labelText,
                checked: Boolean(input.checked),
            };
        })
        .filter((item) => item.id && item.label);
}
"""
    )

    if not isinstance(options, list) or not options:
        return {"found": False, "applied": False, "selected": None, "options_count": 0}

    best_option = None
    best_score = 0.0
    for option in options:
        if not isinstance(option, dict):
            continue
        text = str(option.get("label") or option.get("value") or "")
        score = _score_part_type_option(part_query, text)
        if score > best_score:
            best_score = score
            best_option = option

    if not best_option or best_score <= 0:
        return {
            "found": True,
            "applied": False,
            "selected": None,
            "options_count": len(options),
        }

    selected_id = str(best_option.get("id") or "")
    applied = await page.evaluate(
        """
({ selectedId }) => {
    const input = document.getElementById(selectedId);
    if (!input) return false;

    const checkField = input.closest('label.geo-facet-check-field');
    try {
        if (checkField && typeof checkField.click === 'function') {
            checkField.click();
        } else if (typeof input.click === 'function') {
            input.click();
        }
    } catch (_) {}

    if (!input.checked) {
        input.checked = true;
    }
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    return true;
}
""",
        {"selectedId": selected_id},
    )

    return {
        "found": True,
        "applied": bool(applied),
        "selected": {
            "id": selected_id,
            "label": str(best_option.get("label") or ""),
            "score": round(best_score, 3),
        },
        "options_count": len(options),
    }


async def _clear_napa_site_storage(context: BrowserContext, page: Page) -> None:
    """Clear NAPA-only persisted browser storage to avoid stale fitment carryover."""
    napa_host = "www.napaonline.com"
    try:
        cookies = await context.cookies()
        napa_cookies = [
            c
            for c in cookies
            if napa_host in (c.get("domain") or "").lstrip(".").lower()
        ]
        if napa_cookies:
            await context.clear_cookies()
            non_napa_cookies = [
                c
                for c in cookies
                if napa_host not in (c.get("domain") or "").lstrip(".").lower()
            ]
            if non_napa_cookies:
                await context.add_cookies(non_napa_cookies)
    except Exception:
        # Storage reset should not fail the run.
        pass

    try:
        await page.goto("https://www.napaonline.com", wait_until="domcontentloaded", timeout=60_000)
        await page.evaluate(
            """
async () => {
    try { localStorage.clear(); } catch (_) {}
    try { sessionStorage.clear(); } catch (_) {}

    try {
        if (window.caches && caches.keys) {
            const keys = await caches.keys();
            await Promise.all(keys.map((k) => caches.delete(k)));
        }
    } catch (_) {}

    try {
        if (indexedDB && indexedDB.databases) {
            const dbs = await indexedDB.databases();
            await Promise.all(
                (dbs || [])
                    .map((d) => d && d.name)
                    .filter(Boolean)
                    .map(
                        (name) =>
                            new Promise((resolve) => {
                                const req = indexedDB.deleteDatabase(name);
                                req.onsuccess = () => resolve(null);
                                req.onerror = () => resolve(null);
                                req.onblocked = () => resolve(null);
                            })
                    )
            );
        }
    } catch (_) {}
}
"""
        )
    except Exception:
        # Storage reset should not fail the run.
        pass


# --------------------------------------------------------------------------- #
# Main orchestrator                                                            #
# --------------------------------------------------------------------------- #

async def run_agent(
    *,
    domain: str,
    vehicle_query: str,
    part_query: str,
    target_url: str | None = None,
    headless: bool = False,
    user_data_dir: str | None = None,
) -> dict[str, Any]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    normalized_domain = _normalize_domain(domain)
    adapter = get_adapter(normalized_domain)
    if adapter is None:
        raise RuntimeError(f"No adapter available for domain '{normalized_domain}'")

    set_runtime_context = getattr(adapter, "set_runtime_context", None)
    if callable(set_runtime_context):
        set_runtime_context(vehicle_query=vehicle_query, part_query=part_query)

    # Check if adapter is API-based (no browser needed)
    is_api_based = getattr(adapter, "is_api_based", False)
    effective_headless = _resolve_browser_headless_mode(
        requested_headless=headless,
        is_api_based=is_api_based,
    )

    current_part_query = part_query
    rerun_used = 0
    processing_attempts: list[dict[str, Any]] = []
    final_result: dict[str, Any] | None = None
    final_meta: dict[str, Any] = {}

    for run_index in range(MAX_RUNS_PER_RETAILER):
        # 1. Discover target URL via Tavily (or use the one provided)
        tavily_selection = None
        resolved_target_url = target_url
        if not resolved_target_url:
            tavily_selection = await _tavily_find_target_page(
                vehicle_query,
                current_part_query,
                normalized_domain,
                adapter.score_tavily_result,
            )
            resolved_target_url = str(tavily_selection["selected_url"])

        if is_api_based:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(OLLAMA_TIMEOUT_SECONDS, connect=15.0),
                follow_redirects=True,
            ) as httpx_client:
                products: list[dict[str, Any]] = []
                if hasattr(adapter, "call_browse_api"):
                    products = await adapter.call_browse_api(
                        _compose_tavily_search_query(vehicle_query, current_part_query),
                        httpx_client,
                    )
                supplemental = await adapter.fetch_supplemental_data(
                    page=None,
                    products=products,
                    search_term=current_part_query,
                    httpx_client=httpx_client,
                )
                enriched_products = _normalize_product_urls(
                    adapter.enrich_products(products, supplemental),
                    base_url=resolved_target_url,
                    domain=normalized_domain,
                )

                compact_products = [_compact_product(p) for p in enriched_products]
                strict_matching_only = normalized_domain == "rockauto.com"
                try:
                    _llm_t0 = time.monotonic()
                    logger.info("[%s] LLM review starting (%d products)", normalized_domain, len(compact_products))
                    llm_review = await _review_products_with_ollama(
                        httpx_client,
                        vehicle_query=vehicle_query,
                        part_query=current_part_query,
                        compact_products=compact_products,
                    )
                    logger.info("[%s] LLM review completed in %.1fs", normalized_domain, time.monotonic() - _llm_t0)
                except Exception as exc:
                    logger.error("[%s] LLM review failed after %.1fs: %s", normalized_domain, time.monotonic() - _llm_t0, exc)
                    if strict_matching_only:
                        llm_review = {
                            "matched_products": [],
                            "tagged_products": [],
                            "should_rerun": True,
                            "updated_part_query": current_part_query,
                        }
                    else:
                        llm_review = _fallback_llm_decision(compact_products)
                    llm_review["error"] = str(exc)
                llm_review = _normalize_llm_review_urls(
                    llm_review,
                    base_url=resolved_target_url,
                    domain=normalized_domain,
                )
                if strict_matching_only and not llm_review.get("matched_products"):
                    llm_review["tagged_products"] = []
                elif not strict_matching_only:
                    llm_review = _ensure_required_tags(
                        llm_review,
                        fallback_products=compact_products,
                    )

            timestamp = int(time.time())
            result = {
                "agent_version": "3.0",
                "adapter_type": "api_based",
                "domain": normalized_domain,
                "tavily": tavily_selection,
                "vehicle_query": vehicle_query,
                "part_query": current_part_query,
                "search_term": current_part_query,
                "timestamp": timestamp,
                "product_count": len(enriched_products),
                "attempts": supplemental,
                "products": enriched_products,
                "products_for_llm": compact_products,
                "llm_review": llm_review,
                "ollama": {
                    "base_url": OLLAMA_BASE_URL,
                    "model": OLLAMA_MODEL,
                },
            }
            meta = {
                "adapter_type": "api_based",
                "target_url": resolved_target_url,
                "title": None,
                "api_source": supplemental.get("source"),
                "page_fetch_statuses": {},
            }
        else:
            browser_user_data_dir = user_data_dir or str(USER_DATA_DIR)
            browser_attempt_history: list[dict[str, Any]] = []
            last_browser_error: Exception | None = None
            async with async_playwright() as pw:
                for browser_attempt in range(1, MAX_BROWSER_ATTEMPTS + 1):
                    context: BrowserContext | None = None
                    browser_channel = "chromium"
                    try:
                        context, browser_channel = await _launch_browser_context(
                            pw,
                            user_data_dir=browser_user_data_dir,
                            headless=effective_headless,
                        )
                        page: Page = context.pages[0] if context.pages else await context.new_page()
                        if normalized_domain == "napaonline.com":
                            await _clear_napa_site_storage(context, page)
                        bootstrap = await _bootstrap_page(
                            page,
                            resolved_target_url,
                            adapter.dom_extract_script,
                            domain=normalized_domain,
                            part_query=current_part_query,
                        )
                        products = bootstrap["products"]

                        async with httpx.AsyncClient(
                            timeout=httpx.Timeout(OLLAMA_TIMEOUT_SECONDS, connect=15.0),
                            follow_redirects=True,
                        ) as httpx_client:
                            supplemental = await adapter.fetch_supplemental_data(
                                page, products, current_part_query, httpx_client
                            )
                            enriched_products = _normalize_product_urls(
                                adapter.enrich_products(products, supplemental),
                                base_url=resolved_target_url,
                                domain=normalized_domain,
                            )
                            compact_products = [_compact_product(p) for p in enriched_products]
                            strict_matching_only = normalized_domain == "rockauto.com"
                            try:
                                _llm_t0 = time.monotonic()
                                logger.info("[%s] LLM review starting (%d products)", normalized_domain, len(compact_products))
                                llm_review = await _review_products_with_ollama(
                                    httpx_client,
                                    vehicle_query=vehicle_query,
                                    part_query=current_part_query,
                                    compact_products=compact_products,
                                )
                                logger.info("[%s] LLM review completed in %.1fs", normalized_domain, time.monotonic() - _llm_t0)
                            except Exception as exc:
                                logger.error("[%s] LLM review failed after %.1fs: %s", normalized_domain, time.monotonic() - _llm_t0, exc)
                                if strict_matching_only:
                                    llm_review = {
                                        "matched_products": [],
                                        "tagged_products": [],
                                        "should_rerun": True,
                                        "updated_part_query": current_part_query,
                                    }
                                else:
                                    llm_review = _fallback_llm_decision(compact_products)
                                llm_review["error"] = str(exc)
                            llm_review = _normalize_llm_review_urls(
                                llm_review,
                                base_url=resolved_target_url,
                                domain=normalized_domain,
                            )
                            if strict_matching_only and not llm_review.get("matched_products"):
                                llm_review["tagged_products"] = []
                            elif not strict_matching_only:
                                llm_review = _ensure_required_tags(
                                    llm_review,
                                    fallback_products=compact_products,
                                )

                        blocked = _browser_attempt_blocked(
                            title=bootstrap.get("title"),
                            products=enriched_products,
                            supplemental=supplemental,
                        )
                        browser_attempt_history.append(
                            {
                                "attempt": browser_attempt,
                                "browser_mode": "headless" if effective_headless else "headed",
                                "browser_channel": browser_channel,
                                "title": bootstrap.get("title"),
                                "product_count": len(enriched_products),
                                "blocked": blocked,
                            }
                        )

                        timestamp = int(time.time())
                        result = {
                            "agent_version": "3.0",
                            "adapter_type": "browser_based",
                            "domain": normalized_domain,
                            "tavily": tavily_selection,
                            "target_url": resolved_target_url,
                            "landing_url": bootstrap.get("landing_url"),
                            "vehicle_query": vehicle_query,
                            "part_query": current_part_query,
                            "search_term": current_part_query,
                            "timestamp": timestamp,
                            "title": bootstrap["title"],
                            "final_url": bootstrap["final_url"],
                            "csrf_found": bool(bootstrap["csrf"]),
                            "part_type_filter": bootstrap.get("part_type_filter"),
                            "product_count": len(enriched_products),
                            "attempts": supplemental,
                            "products": enriched_products,
                            "products_for_llm": compact_products,
                            "llm_review": llm_review,
                            "ollama": {
                                "base_url": OLLAMA_BASE_URL,
                                "model": OLLAMA_MODEL,
                            },
                            "browser_mode": "headless" if effective_headless else "headed",
                            "browser_channel": browser_channel,
                            "headless_requested": bool(headless),
                            "browser_attempts": browser_attempt_history,
                        }
                        meta = {
                            "adapter_type": "browser_based",
                            "target_url": resolved_target_url,
                            "title": bootstrap["title"],
                            "api_source": None,
                            "browser_mode": "headless" if effective_headless else "headed",
                            "browser_channel": browser_channel,
                            "page_fetch_statuses": {
                                k: v.get("status")
                                for k, v in supplemental.get("page_fetch", {}).items()
                            },
                        }

                        if not blocked or browser_attempt >= MAX_BROWSER_ATTEMPTS:
                            break

                        _reset_browser_profile_dir(browser_user_data_dir)
                        await asyncio.sleep(BROWSER_BACKOFF_SECONDS * browser_attempt)
                    except Exception as exc:
                        last_browser_error = exc
                        browser_attempt_history.append(
                            {
                                "attempt": browser_attempt,
                                "browser_mode": "headless" if effective_headless else "headed",
                                "browser_channel": browser_channel,
                                "title": None,
                                "product_count": 0,
                                "blocked": True,
                                "error": f"{exc.__class__.__name__}: {exc}",
                            }
                        )
                        if browser_attempt >= MAX_BROWSER_ATTEMPTS:
                            timestamp = int(time.time())
                            result = {
                                "agent_version": "3.0",
                                "adapter_type": "browser_based",
                                "domain": normalized_domain,
                                "tavily": tavily_selection,
                                "target_url": resolved_target_url,
                                "landing_url": None,
                                "vehicle_query": vehicle_query,
                                "part_query": current_part_query,
                                "search_term": current_part_query,
                                "timestamp": timestamp,
                                "title": None,
                                "final_url": None,
                                "csrf_found": False,
                                "part_type_filter": None,
                                "product_count": 0,
                                "attempts": {
                                    "browser_error": _browser_attempt_error_result(exc),
                                    "page_fetch": {},
                                },
                                "products": [],
                                "products_for_llm": [],
                                "llm_review": _fallback_llm_decision([]),
                                "ollama": {
                                    "base_url": OLLAMA_BASE_URL,
                                    "model": OLLAMA_MODEL,
                                },
                                "browser_mode": "headless" if effective_headless else "headed",
                                "browser_channel": browser_channel,
                                "headless_requested": bool(headless),
                                "browser_attempts": browser_attempt_history,
                                "browser_error": f"{exc.__class__.__name__}: {exc}",
                            }
                            meta = {
                                "adapter_type": "browser_based",
                                "target_url": resolved_target_url,
                                "title": None,
                                "api_source": None,
                                "browser_mode": "headless" if effective_headless else "headed",
                                "browser_channel": browser_channel,
                                "page_fetch_statuses": {},
                            }
                            break

                        _reset_browser_profile_dir(browser_user_data_dir)
                        await asyncio.sleep(BROWSER_BACKOFF_SECONDS * browser_attempt)
                    finally:
                        if context is not None:
                            await context.close()

        processing_attempts.append(
            {
                "run_index": run_index,
                "part_query": current_part_query,
                "matched_count": len(result.get("llm_review", {}).get("matched_products", [])),
                "should_rerun": bool(result.get("llm_review", {}).get("should_rerun")),
                "updated_part_query": str(result.get("llm_review", {}).get("updated_part_query") or ""),
            }
        )

        proposed_part_query = str(result.get("llm_review", {}).get("updated_part_query") or "").strip()
        should_rerun = bool(result.get("llm_review", {}).get("should_rerun"))
        if (
            run_index < (MAX_RUNS_PER_RETAILER - 1)
            and should_rerun
            and proposed_part_query
            and proposed_part_query.lower() != current_part_query.lower()
        ):
            current_part_query = proposed_part_query
            rerun_used += 1
            continue

        final_result = result
        final_meta = meta
        break

    if final_result is None:
        raise RuntimeError("No result produced")

    final_result["rerun_count"] = rerun_used
    final_result["processing_attempts"] = processing_attempts

    timestamp = int(final_result.get("timestamp") or time.time())
    out_path = OUTPUT_DIR / f"agent_3_0_{timestamp}.json"
    latest_path = OUTPUT_DIR / "agent_3_0_latest.json"
    for path in (out_path, latest_path):
        path.write_text(json.dumps(final_result, indent=2, default=str), encoding="utf-8")

    summary = {
        "summary_path": str(out_path),
        "latest_path": str(latest_path),
        "domain": normalized_domain,
        "adapter_type": final_meta.get("adapter_type"),
        "target_url": final_meta.get("target_url"),
        "title": final_meta.get("title"),
        "product_count": int(final_result.get("product_count") or 0),
        "page_fetch_statuses": final_meta.get("page_fetch_statuses", {}),
        "rerun_count": rerun_used,
        "vehicle_query": vehicle_query,
        "part_query": str(final_result.get("part_query") or current_part_query),
        "llm_matched_count": len(final_result.get("llm_review", {}).get("matched_products", [])),
        "llm_tagged_products": final_result.get("llm_review", {}).get("tagged_products", []),
        "llm_should_rerun": bool(final_result.get("llm_review", {}).get("should_rerun")),
        "llm_updated_part_query": str(final_result.get("llm_review", {}).get("updated_part_query") or ""),
        "browser_mode": final_meta.get("browser_mode"),
        "llm_guided_used": bool(final_result.get("attempts", {}).get("llm_guided_used")),
        "page_qualification": final_result.get("attempts", {}).get("page_qualification"),
        "llm_guided_final_qualification": final_result.get("attempts", {}).get(
            "llm_guided_final_qualification"
        ),
        "llm_guided_trace_steps": len(final_result.get("attempts", {}).get("llm_guided_trace", []) or []),
        "browser_channel": final_meta.get("browser_channel"),
    }
    if final_meta.get("api_source"):
        summary["api_source"] = final_meta["api_source"]
    return summary


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Agent 3.0: Tavily page discovery + per-retailer in-page extraction.\n"
            f"Supported domains: {', '.join(list_domains())}"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--domain",
        default=DEFAULT_DOMAIN,
        help=(
            f"Retailer domain to scrape (default: {DEFAULT_DOMAIN}). "
            f"Supported: {', '.join(list_domains())}"
        ),
    )
    parser.add_argument(
        "--vehicle-query",
        default=DEFAULT_VEHICLE_QUERY,
        help="Vehicle descriptor (year/make/model and optional trim/engine).",
    )
    parser.add_argument(
        "--target-url",
        default=None,
        help="Explicit retailer page URL. If set, Tavily discovery is skipped.",
    )
    parser.add_argument(
        "--part-query",
        default=DEFAULT_SEARCH_TERM,
        help="Part request text (e.g. 'brake rotor').",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browser headless. Headed mode is the default (required for most retailers).",
    )
    parser.add_argument(
        "--list-domains",
        action="store_true",
        help="Print all registered retailer domains and exit.",
    )
    return parser.parse_args()


async def main() -> None:
    args = _parse_args()

    if args.list_domains:
        print("Registered domains:")
        for d in list_domains():
            print(f"  {d}")
        return

    vehicle_query = (args.vehicle_query or "").strip()
    part_query = (args.part_query or "").strip()
    if not vehicle_query or not part_query:
        raise SystemExit("Both --vehicle-query and --part-query are required")

    result = await run_agent(
        domain=args.domain,
        vehicle_query=vehicle_query,
        part_query=part_query,
        target_url=args.target_url,
        headless=args.headless,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
