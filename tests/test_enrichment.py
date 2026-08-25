import pytest
from fastapi.testclient import TestClient

from bridge.app.api.deps import get_db
from bridge.app.cache.db import Database
from bridge.app.main import app
from bridge.app.providers import ParseBotProvider, PerformerData, ProviderError, register
from bridge.app.providers.wikidata import WikidataProvider


@pytest.fixture
def ctx(tmp_path):
    db = Database(str(tmp_path / "enrich.sqlite"))
    name = db.add_direct_name("Samantha Fox")
    app.dependency_overrides[get_db] = lambda: db
    yield db, TestClient(app), name["id"]
    app.dependency_overrides.clear()
    db.close()


# --- DB layer ---


def test_apply_profile_only_writes_given_fields(ctx):
    db, _client, nid = ctx
    prof = db.apply_enrichment_profile(
        nid,
        {
            "country": {"value": "United Kingdom", "source": "wikidata"},
            "aliases": {"value": ["Sam", "Samantha Karen Fox"], "source": "wikidata"},
        },
    )
    assert prof["country"] == "United Kingdom"
    assert prof["aliases"] == ["Sam", "Samantha Karen Fox"]  # list round-trips via JSON
    assert prof["gender"] is None  # untouched field stays null
    assert prof["field_sources"] == {"country": "wikidata", "aliases": "wikidata"}


def test_apply_profile_overrides_field_by_field(ctx):
    db, _client, nid = ctx
    db.apply_enrichment_profile(nid, {"country": {"value": "UK", "source": "wikidata"}})
    prof = db.apply_enrichment_profile(nid, {"gender": {"value": "Female", "source": "parsebot"}})
    assert prof["country"] == "UK"  # prior field preserved
    assert prof["gender"] == "Female"
    assert prof["field_sources"] == {"country": "wikidata", "gender": "parsebot"}


def test_candidate_cache_replace_and_list(ctx):
    db, _client, nid = ctx
    db.replace_candidates(
        nid, "wikidata", [{"source_entity_id": "Q1", "data": {"name": "A"}, "score": 1.0}]
    )
    db.replace_candidates(nid, "wikidata", [{"source_entity_id": "Q2", "data": {"name": "B"}}])
    got = db.list_candidates(nid, "wikidata")
    assert [c["source_entity_id"] for c in got] == ["Q2"]  # replace, not append
    assert got[0]["data"]["name"] == "B"


def test_credit_ledger(ctx):
    db, _client, _nid = ctx
    db.add_credit("parsebot", 1)
    db.add_credit("parsebot", 2)
    assert db.credits_spent("parsebot") == 3
    assert db.credits_spent("wikidata") == 0


# --- API cache-first ---


class FakeProvider:
    id = "faketest"
    label = "Fake"
    metered = False

    def __init__(self):
        self.calls = 0

    def search(self, term):
        self.calls += 1
        return [
            PerformerData(source=self.id, source_entity_id="x1", name=term, country="UK")
        ]


def test_candidates_cache_first(ctx):
    _db, client, nid = ctx
    fake = FakeProvider()
    register(fake)

    r1 = client.get("/enrichment/candidates", params={"name_id": nid, "source": "faketest"}).json()
    assert r1["cached"] is False and len(r1["candidates"]) == 1
    assert r1["candidates"][0]["data"]["country"] == "UK"

    r2 = client.get("/enrichment/candidates", params={"name_id": nid, "source": "faketest"}).json()
    assert r2["cached"] is True
    assert fake.calls == 1  # second call served from cache, provider not hit again

    r3 = client.get(
        "/enrichment/candidates", params={"name_id": nid, "source": "faketest", "refresh": True}
    ).json()
    assert r3["cached"] is False and fake.calls == 2


def test_unknown_source_400(ctx):
    _db, client, nid = ctx
    r = client.get("/enrichment/candidates", params={"name_id": nid, "source": "nope"})
    assert r.status_code == 400


def test_sources_lists_wikidata(ctx):
    _db, client, _nid = ctx
    ids = [s["id"] for s in client.get("/enrichment/sources").json()["sources"]]
    assert "wikidata" in ids


def test_apply_profile_endpoint(ctx):
    _db, client, nid = ctx
    body = {"name_id": nid, "fields": {"gender": {"value": "Female", "source": "wikidata"}}}
    prof = client.post("/enrichment/profile", json=body).json()["profile"]
    assert prof["gender"] == "Female"
    got = client.get("/enrichment/profile", params={"name_id": nid}).json()["profile"]
    assert got["gender"] == "Female"


# --- Wikidata mapping (mocked HTTP) ---


class _Resp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def _snak(value):
    return [{"mainsnak": {"snaktype": "value", "datavalue": {"value": value}}}]


_ENTITY = {
    "labels": {"en": {"value": "Samantha Fox"}},
    "descriptions": {"en": {"value": "English model and singer"}},
    "aliases": {"en": [{"value": "Samantha Karen Fox"}]},
    "claims": {
        "P31": _snak({"id": "Q5"}),  # instance of human
        "P21": _snak({"id": "Q6581072"}),  # gender female
        "P569": _snak({"time": "+1966-04-15T00:00:00Z"}),  # birthdate
        "P27": _snak({"id": "Q145"}),  # country → United Kingdom
        "P18": _snak("Samantha Fox.jpg"),  # image
        "P856": _snak("https://samfox.com"),  # official website
    },
}


class _FakeWikidataClient:
    def get(self, url, params):
        action = params.get("action")
        if action == "wbsearchentities":
            return _Resp({"search": [{"id": "Q123"}]})
        if action == "wbgetentities":
            if params.get("props") == "labels":
                uk = {"labels": {"en": {"value": "United Kingdom"}}}
                return _Resp({"entities": {"Q145": uk}})
            return _Resp({"entities": {"Q123": _ENTITY}})
        return _Resp({})


def test_wikidata_maps_claims():
    p = WikidataProvider(client=_FakeWikidataClient())
    results = p.search("Samantha Fox")
    assert len(results) == 1
    r = results[0]
    assert r.source == "wikidata" and r.source_entity_id == "Q123"
    assert r.name == "Samantha Fox"
    assert r.disambiguation == "English model and singer"
    assert r.aliases == ["Samantha Karen Fox"]
    assert r.gender == "Female"
    assert r.birthdate == "1966-04-15"
    assert r.country == "United Kingdom"
    assert r.urls == ["https://samfox.com"]
    assert r.images == ["https://commons.wikimedia.org/wiki/Special:FilePath/Samantha_Fox.jpg"]


# --- parse.bot (The Handbook) ---


class _FakeParseBotClient:
    def __init__(self):
        self.calls = 0

    def get(self, url, params=None, headers=None):
        self.calls += 1
        prof = {
            "id": 63414,
            "name": "Taylor Swift",
            "type": "thb_celebrity",
            "url": "https://www.thehandbook.com/celebrity/taylor-swift/",
            "thumbnail": "https://files.thehandbook.com/x.jpg",
            "social_handles": ["@taylorswift"],
            "social_reach": 432530798,
            "tags": [],
        }
        data = {"profiles": [prof], "total_found": 1, "page": 1}
        return _Resp({"status": "success", "data": data})


def test_parsebot_maps_search():
    r = ParseBotProvider(api_key="k", client=_FakeParseBotClient()).search("Taylor Swift")[0]
    assert r.source == "parsebot" and r.source_entity_id == "63414"
    assert r.name == "Taylor Swift"
    assert r.disambiguation == "Celebrity"  # type -> label
    assert r.images == ["https://files.thehandbook.com/x.jpg"]
    assert r.urls == ["https://www.thehandbook.com/celebrity/taylor-swift/"]
    assert r.score == 432530798.0  # social_reach -> score
    assert r.birthdate is None  # no bio from The Handbook


def test_parsebot_requires_key():
    with pytest.raises(ProviderError):
        ParseBotProvider(api_key=None).search("x")


def test_parsebot_records_credit_and_caches(ctx):
    db, client, nid = ctx
    register(ParseBotProvider(api_key="k", client=_FakeParseBotClient()))
    r1 = client.get("/enrichment/candidates", params={"name_id": nid, "source": "parsebot"}).json()
    assert r1["cached"] is False and len(r1["candidates"]) == 1
    assert db.credits_spent("parsebot") == 1
    r2 = client.get("/enrichment/candidates", params={"name_id": nid, "source": "parsebot"}).json()
    assert r2["cached"] is True and db.credits_spent("parsebot") == 1  # cache: no new credit


def test_parsebot_budget_guard(ctx):
    db, client, nid = ctx
    register(ParseBotProvider(api_key="k", client=_FakeParseBotClient()))
    db.add_credit("parsebot", 199)  # at the soft budget
    r = client.get("/enrichment/candidates", params={"name_id": nid, "source": "parsebot"}).json()
    assert "budget reached" in (r["error"] or "")
    assert db.credits_spent("parsebot") == 199  # not charged
    assert not db.has_enrichment_search(nid, "parsebot")  # no live call made


def test_wikidata_filters_non_humans():
    class NoHuman(_FakeWikidataClient):
        def get(self, url, params):
            if params.get("action") == "wbgetentities" and params.get("props") != "labels":
                claims = {**_ENTITY["claims"], "P31": _snak({"id": "Q515"})}  # instance of city
                return _Resp({"entities": {"Q123": {**_ENTITY, "claims": claims}}})
            return super().get(url, params)

    assert WikidataProvider(client=NoHuman()).search("x") == []
