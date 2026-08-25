"""Wikidata enrichment source (free).

Two calls per search: `wbsearchentities` (name → candidate QIDs) then `wbgetentities`
(labels/aliases/descriptions/claims), plus one batch `wbgetentities` to resolve entity-valued
claims (gender, country, …) to English labels. Maps the standalone performer fields we can derive;
absent claims stay null (only-populated-fields is enforced downstream). See docs/ENRICHMENT.md §3.
"""

import httpx

from bridge.app.providers.base import ProviderError
from bridge.app.providers.models import PerformerData

_API = "https://www.wikidata.org/w/api.php"
_HUMAN = "Q5"

# P21 gender item → label (avoids a lookup for the common cases).
_GENDER = {
    "Q6581097": "Male",
    "Q6581072": "Female",
    "Q1052281": "Transgender female",
    "Q2449503": "Transgender male",
    "Q1097630": "Intersex",
    "Q48270": "Non-binary",
}
# Claim → PerformerData field, for entity-valued claims resolved via label lookup.
_ENTITY_CLAIMS = {
    "P27": "country",  # country of citizenship
    "P172": "ethnicity",  # ethnic group
    "P1884": "hair_color",
}
_COMMONS_FILEPATH = "https://commons.wikimedia.org/wiki/Special:FilePath/"


def _first_snak(entity: dict, prop: str) -> dict | None:
    for stmt in entity.get("claims", {}).get(prop, []):
        snak = stmt.get("mainsnak", {})
        if snak.get("snaktype") == "value":
            return snak["datavalue"]["value"]
    return None


def _all_snaks(entity: dict, prop: str) -> list:
    out = []
    for stmt in entity.get("claims", {}).get(prop, []):
        snak = stmt.get("mainsnak", {})
        if snak.get("snaktype") == "value":
            out.append(snak["datavalue"]["value"])
    return out


def _date(value) -> str | None:
    # value like {"time": "+1966-04-15T00:00:00Z", "precision": 11}
    if not isinstance(value, dict):
        return None
    t = value.get("time", "")
    m = t.lstrip("+")[:10]
    return m if len(m) == 10 and not m.startswith("0000") else None


def _is_human(entity: dict) -> bool:
    return any(
        isinstance(v, dict) and v.get("id") == _HUMAN for v in _all_snaks(entity, "P31")
    )


class WikidataProvider:
    id = "wikidata"
    label = "Wikidata"
    metered = False

    def __init__(self, client: httpx.Client | None = None):
        self._client = client or httpx.Client(
            timeout=20.0,
            headers={"User-Agent": "stash-performer-id/0.1 (enrichment; local)"},
        )

    def _get(self, params: dict) -> dict:
        try:
            resp = self._client.get(_API, params={**params, "format": "json"})
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            raise ProviderError(f"wikidata: {e}") from e

    def _resolve_labels(self, qids: set[str]) -> dict[str, str]:
        if not qids:
            return {}
        out: dict[str, str] = {}
        ids = sorted(qids)
        for i in range(0, len(ids), 50):  # wbgetentities caps at 50 ids
            chunk = ids[i : i + 50]
            ents = self._get(
                {"action": "wbgetentities", "ids": "|".join(chunk),
                 "props": "labels", "languages": "en"}
            ).get("entities", {})
            for qid, e in ents.items():
                label = e.get("labels", {}).get("en", {}).get("value")
                if label:
                    out[qid] = label
        return out

    def search(self, term: str) -> list[PerformerData]:
        found = self._get(
            {
                "action": "wbsearchentities",
                "search": term,
                "language": "en",
                "uselang": "en",
                "type": "item",
                "limit": 7,
            }
        )
        qids = [s["id"] for s in found.get("search", [])]
        if not qids:
            return []
        ents = self._get(
            {
                "action": "wbgetentities",
                "ids": "|".join(qids),
                "props": "labels|descriptions|aliases|claims",
                "languages": "en",
            }
        ).get("entities", {})

        humans = {qid: e for qid, e in ents.items() if _is_human(e)}

        # Batch-resolve entity-valued claim labels (country / ethnicity / hair).
        refs: set[str] = set()
        for e in humans.values():
            for prop in _ENTITY_CLAIMS:
                v = _first_snak(e, prop)
                if isinstance(v, dict) and v.get("id"):
                    refs.add(v["id"])
        labels = self._resolve_labels(refs)

        out: list[PerformerData] = []
        for qid in qids:  # preserve search relevance order
            e = humans.get(qid)
            if e:
                out.append(_to_performer(qid, e, labels))
        return out

    def close(self) -> None:
        self._client.close()


def _to_performer(qid: str, e: dict, labels: dict[str, str]) -> PerformerData:
    name = e.get("labels", {}).get("en", {}).get("value") or qid
    disambiguation = e.get("descriptions", {}).get("en", {}).get("value")
    aliases = [a["value"] for a in e.get("aliases", {}).get("en", [])]

    gender = None
    g = _first_snak(e, "P21")
    if isinstance(g, dict):
        gender = _GENDER.get(g.get("id")) or labels.get(g.get("id"))

    entity_fields: dict[str, str | None] = {}
    for prop, fieldname in _ENTITY_CLAIMS.items():
        v = _first_snak(e, prop)
        entity_fields[fieldname] = labels.get(v["id"]) if isinstance(v, dict) else None

    height = None
    h = _first_snak(e, "P2048")  # height (quantity)
    if isinstance(h, dict) and h.get("amount"):
        amount = float(h["amount"].lstrip("+"))
        if h.get("unit", "").endswith("Q11573"):  # metres → cm
            amount *= 100
        height = str(round(amount))

    # URLs: official website + common socials.
    urls: list[str] = []
    for site in _all_snaks(e, "P856"):  # official website
        if isinstance(site, str):
            urls.append(site)
    for handle in _all_snaks(e, "P2002"):  # twitter/X
        if isinstance(handle, str):
            urls.append(f"https://twitter.com/{handle}")
    for handle in _all_snaks(e, "P2003"):  # instagram
        if isinstance(handle, str):
            urls.append(f"https://instagram.com/{handle}")

    images = []
    img = _first_snak(e, "P18")  # image (commons filename)
    if isinstance(img, str):
        images.append(_COMMONS_FILEPATH + img.replace(" ", "_"))

    return PerformerData(
        source="wikidata",
        source_entity_id=qid,
        name=name,
        disambiguation=disambiguation,
        aliases=aliases,
        gender=gender,
        birthdate=_date(_first_snak(e, "P569")),
        death_date=_date(_first_snak(e, "P570")),
        ethnicity=entity_fields.get("ethnicity"),
        country=entity_fields.get("country"),
        hair_color=entity_fields.get("hair_color"),
        height=height,
        urls=urls,
        images=images,
    )
