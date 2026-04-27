"""
Retailer adapter registry.

Usage:
    from retailer_adapters import get_adapter

    adapter = get_adapter("autozone.com")   # raises KeyError if unknown
    adapter = get_adapter("autozone.com", strict=False)  # returns None

Adding a new retailer:
    1. Create retailer_adapters/<retailer>.py and subclass RetailerAdapter.
    2. Import and register it below.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .autozone import AutoZoneAdapter
from .base import RetailerAdapter
from .ebay import EbayAdapter
from .oreillyauto import OReillyAdapter
from .napaonline import NapaAdapter
if TYPE_CHECKING:
    pass

# Adapter classes (for lazy initialization)
_ADAPTER_CLASSES: list[type[RetailerAdapter]] = [
    OReillyAdapter,
    AutoZoneAdapter,
    EbayAdapter,
    NapaAdapter,
]

# Map normalised domain string → adapter instance (lazy-loaded)
_REGISTRY: dict[str, RetailerAdapter] = {}


def get_adapter(domain: str, *, strict: bool = True) -> RetailerAdapter | None:
    """
    Return the adapter for the given normalised domain (no www. prefix).

    Parameters
    ----------
    domain : str
        Normalised domain, e.g. "oreillyauto.com" or "autozone.com".
    strict : bool
        If True (default), raise KeyError for unknown domains.
        If False, return None instead.
    """
    # Lazy-load adapter if not yet initialized
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
    """Return sorted list of all registered domain strings."""
    return sorted([cls.domain for cls in _ADAPTER_CLASSES])
