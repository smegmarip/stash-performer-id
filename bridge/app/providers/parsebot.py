"""The Handbook enrichment source via parse.bot (metered).

The Handbook is a talent-contact directory, not a bio database: `search_profiles` returns name,
profile URL, thumbnail image, type, and social reach — no birthdate/country/measurements. So this
source contributes the **image + profile URL** (type → disambiguation, social_reach → score) to
compose alongside Wikidata's bio. The contact/detail endpoints aren't performer fields, so we use
only `search_profiles` (one credit per search, cached). See docs/ENRICHMENT.md §3 / §9.
"""

import httpx

from bridge.app.providers.base import ProviderError
from bridge.app.providers.models import PerformerData

_BASE = "https://api.parse.bot/scraper/a327a0b6-ab56-4a97-8b96-60ae104eed57"

_TYPE_LABEL = {
    "thb_celebrity": "Celebrity",
    "thb_influencer": "Influencer",
    "thb_employee": "Industry professional",
}


class ParseBotProvider:
    id = "parsebot"
    label = "The Handbook"
    metered = True

    def __init__(self, api_key: str | None = None, client: httpx.Client | None = None):
        self._api_key = api_key
        self._client = client or httpx.Client(timeout=20.0)

    def search(
        self, term: str, disambiguation: str | None = None
    ) -> list[PerformerData]:
        if not self._api_key:
            raise ProviderError("parse.bot: no API key configured")
        try:
            resp = self._client.get(
                f"{_BASE}/search_profiles",
                params={"query": term, "limit": 7},
                headers={"X-API-Key": self._api_key},
            )
            resp.raise_for_status()
            body = resp.json()
        except httpx.HTTPError as e:
            raise ProviderError(f"parse.bot: {e}") from e

        profiles = (body.get("data") or {}).get("profiles", [])
        out: list[PerformerData] = []
        for p in profiles:
            thumb = p.get("thumbnail") or ""
            url = p.get("url") or ""
            reach = p.get("social_reach") or 0
            out.append(
                PerformerData(
                    source=self.id,
                    source_entity_id=str(p.get("id")),
                    name=p.get("name") or term,
                    disambiguation=_TYPE_LABEL.get(p.get("type")),
                    images=[thumb] if thumb else [],
                    urls=[url] if url else [],
                    score=float(reach) if reach else None,
                )
            )
        return out

    def close(self) -> None:
        self._client.close()
