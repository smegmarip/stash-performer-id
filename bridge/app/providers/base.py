"""Provider seam: a source-neutral search interface + a registry of enrichment sources.

Adding a source = implement `Provider.search` and `register()` it. The registry backs the viewer's
active-source selector. See docs/ENRICHMENT.md §3.
"""

from typing import Protocol, runtime_checkable

from bridge.app.providers.models import PerformerData


class ProviderError(RuntimeError):
    """A source lookup failed (network, parse, rate-limit)."""


@runtime_checkable
class Provider(Protocol):
    id: str
    label: str
    metered: bool

    # `disambiguation` is the name's qualifier (e.g. the school from a "<name> (School)"
    # folder) — a hint a source may use to narrow its search; most sources ignore it.
    def search(
        self, term: str, disambiguation: str | None = None
    ) -> list[PerformerData]: ...


_REGISTRY: dict[str, Provider] = {}


def register(provider: Provider) -> None:
    _REGISTRY[provider.id] = provider


def get_provider(source: str) -> Provider | None:
    return _REGISTRY.get(source)


def list_sources() -> list[dict]:
    return [{"id": p.id, "label": p.label, "metered": p.metered} for p in _REGISTRY.values()]
