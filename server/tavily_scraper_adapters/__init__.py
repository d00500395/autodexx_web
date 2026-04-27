"""LLM-guided Tavily scraper adapter registry."""

from __future__ import annotations

from .autozone import AutoZoneLLMAdapter
from .ebay import EbayLLMAdapter
from .legacy.base import RetailerAdapter
from .napaonline import NapaLLMAdapter
from .oreillyauto import OReillyLLMAdapter
from .rockauto import RockAutoLLMAdapter

_ADAPTER_CLASSES: list[type[RetailerAdapter]] = [
    OReillyLLMAdapter,
    AutoZoneLLMAdapter,
    EbayLLMAdapter,
    NapaLLMAdapter,
    RockAutoLLMAdapter,
]

_REGISTRY: dict[str, RetailerAdapter] = {}


def get_adapter(domain: str, *, strict: bool = True) -> RetailerAdapter | None:
    if domain not in _REGISTRY:
        for adapter_class in _ADAPTER_CLASSES:
            if adapter_class.domain == domain:
                _REGISTRY[domain] = adapter_class()
                break

    adapter = _REGISTRY.get(domain)
    if adapter is None and strict:
        supported = ", ".join(sorted([cls.domain for cls in _ADAPTER_CLASSES]))
        raise KeyError(
            f"No adapter registered for '{domain}'. "
            f"Supported domains: {supported}"
        )
    return adapter


def list_domains() -> list[str]:
    return sorted([cls.domain for cls in _ADAPTER_CLASSES])
