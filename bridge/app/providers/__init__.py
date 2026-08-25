"""Enrichment providers: source-neutral DTO, the Provider seam, and the source registry.

Sources register at import time; the registry backs the viewer's active-source selector.
Wikidata (free) is built; parse.bot (metered) lands in a later pass. See docs/ENRICHMENT.md §3.
"""

from bridge.app.providers.base import (
    Provider,
    ProviderError,
    get_provider,
    list_sources,
    register,
)
from bridge.app.providers.models import LIST_FIELDS, PROFILE_FIELDS, PerformerData
from bridge.app.providers.wikidata import WikidataProvider

register(WikidataProvider())

__all__ = [
    "Provider",
    "ProviderError",
    "PerformerData",
    "PROFILE_FIELDS",
    "LIST_FIELDS",
    "WikidataProvider",
    "get_provider",
    "list_sources",
    "register",
]
