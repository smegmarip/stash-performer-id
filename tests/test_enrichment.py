import pytest
from fastapi.testclient import TestClient

from bridge.app.api.deps import get_db
from bridge.app.cache.db import Database
from bridge.app.main import app
from bridge.app.providers import (
    BabepediaProvider,
    ParseBotProvider,
    PerformerData,
    ProviderError,
    register,
)
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

    r1 = client.get("/enrichment/search", params={"name_id": nid, "source": "faketest"}).json()
    assert r1["cached"] is False and len(r1["candidates"]) == 1
    assert r1["candidates"][0]["data"]["country"] == "UK"

    r2 = client.get("/enrichment/search", params={"name_id": nid, "source": "faketest"}).json()
    assert r2["cached"] is True
    assert fake.calls == 1  # second call served from cache, provider not hit again

    r3 = client.get(
        "/enrichment/search", params={"name_id": nid, "source": "faketest", "refresh": True}
    ).json()
    assert r3["cached"] is False and fake.calls == 2


class _ErrorProvider:
    id = "errtest"
    label = "Err"
    metered = False

    def __init__(self):
        self.calls = 0

    def search(self, term):
        self.calls += 1
        raise ProviderError("boom")


class _EmptyProvider:
    id = "emptytest"
    label = "Empty"
    metered = False

    def __init__(self):
        self.calls = 0

    def search(self, term):
        self.calls += 1
        return []


def test_errors_not_cached(ctx):
    db, client, nid = ctx
    prov = _ErrorProvider()
    register(prov)
    r = client.get("/enrichment/search", params={"name_id": nid, "source": "errtest"}).json()
    assert r["error"] and r["candidates"] == []
    assert db.has_enrichment_search(nid, "errtest") is False  # not cached
    client.get("/enrichment/search", params={"name_id": nid, "source": "errtest"})
    assert prov.calls == 2  # retried live, not served from cache


def test_empty_results_not_cached(ctx):
    db, client, nid = ctx
    prov = _EmptyProvider()
    register(prov)
    client.get("/enrichment/search", params={"name_id": nid, "source": "emptytest"})
    assert db.has_enrichment_search(nid, "emptytest") is False  # empty not cached
    client.get("/enrichment/search", params={"name_id": nid, "source": "emptytest"})
    assert prov.calls == 2  # searched again


def test_unknown_source_400(ctx):
    _db, client, nid = ctx
    r = client.get("/enrichment/search", params={"name_id": nid, "source": "nope"})
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


def test_profiles_status(ctx):
    db, client, nid = ctx
    db.apply_enrichment_profile(
        nid,
        {
            "gender": {"value": "Female", "source": "wikidata"},
            "country": {"value": "US", "source": "babepedia"},
        },
    )
    r = client.get("/enrichment/profiles", params={"name_ids": str(nid)}).json()["profiles"]
    assert r[str(nid)]["fields"] == 2
    assert set(r[str(nid)]["sources"]) == {"wikidata", "babepedia"}


def test_profile_status_includes_image(ctx):
    db, client, nid = ctx
    db.apply_enrichment_profile(
        nid,
        {
            "gender": {"value": "Female", "source": "wikidata"},
            "images": {"value": ["http://img/1.jpg", "http://img/2.jpg"], "source": "babepedia"},
        },
    )
    r = client.get("/enrichment/profiles", params={"name_ids": str(nid)}).json()["profiles"]
    assert r[str(nid)]["image"] == "http://img/1.jpg"  # first image
    assert set(r[str(nid)]["sources"]) == {"wikidata", "babepedia"}  # all contributing sources


def test_names_matched_unmatched_filter(ctx):
    db, client, nid = ctx
    nid2 = db.add_direct_name("Marilyn Monroe")["id"]
    db.apply_enrichment_profile(nid2, {"gender": {"value": "Female", "source": "wikidata"}})
    matched = client.get("/names", params={"status": "valid", "enriched": "matched"}).json()
    unmatched = client.get("/names", params={"status": "valid", "enriched": "unmatched"}).json()
    assert {n["id"] for n in matched["names"]} == {nid2} and matched["total"] == 1
    assert nid in {n["id"] for n in unmatched["names"]} and nid2 not in {
        n["id"] for n in unmatched["names"]
    }


def test_search_batch_populates(ctx):
    db, client, nid = ctx
    nid2 = db.add_direct_name("Marilyn Monroe")["id"]
    register(FakeProvider())
    body = {"name_ids": [nid, nid2], "source": "faketest"}
    r = client.post("/enrichment/search-batch", json=body).json()
    assert {x["name_id"] for x in r["results"]} == {nid, nid2}
    assert all(x["count"] == 1 and x["error"] is None for x in r["results"])
    r2 = client.post("/enrichment/search-batch", json={"name_ids": [nid], "source": "faketest"})
    assert r2.json()["results"][0]["cached"] is True  # cache-first on re-run


def test_update_batch_auto_resolves(ctx):
    db, client, nid = ctx
    register(FakeProvider())  # returns country="UK", name=term
    body = {"name_ids": [nid], "source": "faketest", "exclude_fields": ["name"]}
    r = client.post("/enrichment/update-batch", json=body).json()
    assert r["results"][0]["applied"] >= 1
    prof = db.get_enrichment_profile(nid)
    assert prof["country"] == "UK"
    assert prof["name"] is None  # excluded field not applied
    assert prof["field_sources"].get("country") == "faketest"


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
    p = WikidataProvider(client=_FakeWikidataClient(), min_interval=0)
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


class _Resp429:
    status_code = 429
    headers = {"Retry-After": "0"}

    def raise_for_status(self):
        pass

    def json(self):
        return {}


class _RespOk:
    status_code = 200
    headers: dict = {}

    def raise_for_status(self):
        pass

    def json(self):
        return {"ok": True}


class _Fake429ThenOk:
    def __init__(self):
        self.calls = 0

    def get(self, url, params):
        self.calls += 1
        return _Resp429() if self.calls == 1 else _RespOk()


def test_wikidata_retries_on_429(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *a, **k: None)  # no real waiting
    fake = _Fake429ThenOk()
    p = WikidataProvider(client=fake, min_interval=0)
    assert p._get({"action": "test"}) == {"ok": True}
    assert fake.calls == 2  # 429 once, then retried and succeeded


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
    r1 = client.get("/enrichment/search", params={"name_id": nid, "source": "parsebot"}).json()
    assert r1["cached"] is False and len(r1["candidates"]) == 1
    assert db.credits_spent("parsebot") == 1
    r2 = client.get("/enrichment/search", params={"name_id": nid, "source": "parsebot"}).json()
    assert r2["cached"] is True and db.credits_spent("parsebot") == 1  # cache: no new credit


def test_parsebot_budget_guard(ctx):
    db, client, nid = ctx
    register(ParseBotProvider(api_key="k", client=_FakeParseBotClient()))
    db.add_credit("parsebot", 199)  # at the soft budget
    r = client.get("/enrichment/search", params={"name_id": nid, "source": "parsebot"}).json()
    assert "budget reached" in (r["error"] or "")
    assert db.credits_spent("parsebot") == 199  # not charged
    assert not db.has_enrichment_search(nid, "parsebot")  # no live call made


# --- Babepedia (HTML scrape, ported extraction) ---

_BABE_HTML = """
<html><body>
  <h1 id="babename">Riley Reid</h1>
  <h2 id="aka">Paige Riley - Molly</h2>
  <div><span>Born:</span><span><a>15th of January</a><a>1991</a></span></div>
  <div><span>Nationality</span><span><span class="fi fi-us"></span></span></div>
  <div><span>Ethnicity</span><span><a>Caucasian</a></span></div>
  <div><span>Eye color</span><span><a>Blue</a></span></div>
  <div><span>Hair color</span><span><a>Brown</a></span></div>
  <div><span>Height</span><span>163 cm</span></div>
  <div><span>Weight</span><span>50 kg</span></div>
  <div><span>Measurements</span><span>32-24-34</span></div>
  <div><span>Bra/cup size</span><span>B</span></div>
  <div><span>Boobs</span><span><a>Real</a></span></div>
  <div><span>Tattoos</span><span>Lower back</span></div>
  <p id="biotext">Some bio text.</p>
  <div id="socialicons"><a href="https://twitter.com/rileyreidx3"></a>
    <a href="/onlyfans/rileyreid"></a></div>
  <div id="profbox2"><a class="img" href="/pics/riley.jpg"></a></div>
</body></html>
"""


class _BabeResp:
    def __init__(self, text=None, data=None):
        self.text = text
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class _FakeBabeClient:
    def get(self, url, params=None):
        if "ajax-search" in url:
            return _BabeResp(data=[{"label": "Riley Reid", "value": "Riley Reid"}])
        return _BabeResp(text=_BABE_HTML)


def test_babepedia_flaresolverr_fallback(monkeypatch):
    import bridge.app.providers.babepedia as bp

    class _Blocked:  # cloudscraper gets a Cloudflare 403
        def get(self, url, params=None):
            r = _BabeResp()
            r.status_code = 403
            return r

    class _Post:
        def __init__(self, data):
            self._data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self._data

    calls = {"n": 0}

    def fake_post(url, json=None, timeout=None):
        calls["n"] += 1
        target = json["url"]
        if "ajax-search" in target:  # FlareSolverr returns JSON HTML-wrapped in <pre>
            inner = '[{"label":"Riley Reid","value":"Riley Reid"}]'
            body = f"<html><body><pre>{inner}</pre></body></html>"
        else:
            body = _BABE_HTML
        return _Post({"solution": {"status": 200, "response": body}})

    monkeypatch.setattr(bp.requests, "post", fake_post)
    prov = BabepediaProvider(client=_Blocked(), flaresolverr_url="http://fs:8191/v1")
    r = prov.search("Riley Reid")[0]
    assert r.name == "Riley Reid" and r.country == "US"
    assert calls["n"] >= 2  # ajax-search + detail, both via FlareSolverr


def test_babepedia_blocked_without_flaresolverr():
    class _Blocked:
        def get(self, url, params=None):
            r = _BabeResp()
            r.status_code = 403
            return r

    with pytest.raises(ProviderError):
        BabepediaProvider(client=_Blocked()).search("Riley Reid")


def test_babepedia_maps_full_bio():
    r = BabepediaProvider(client=_FakeBabeClient()).search("Riley Reid")[0]
    assert r.source == "babepedia" and r.source_entity_id == "Riley_Reid"
    assert r.name == "Riley Reid"
    assert r.aliases == ["Paige Riley", "Molly"]
    assert r.gender == "Female"
    assert r.birthdate == "1991-01-15"
    assert r.country == "US"
    assert r.ethnicity == "Caucasian"
    assert r.eye_color == "Blue"
    assert r.hair_color == "Brunette"  # Brown -> Brunette
    assert r.height == "163" and r.weight == "50"
    assert r.measurements == "B-24-34"  # cup + waist + hip
    assert r.fake_tits == "Natural"  # "Real" -> Natural
    assert r.tattoos == "Lower back"
    assert r.details == "Some bio text."
    assert "https://twitter.com/rileyreidx3" in r.urls
    assert "https://onlyfans.com/rileyreid" in r.urls  # proxy-mapped
    assert r.images == ["https://www.babepedia.com/pics/riley.jpg"]


def test_wikidata_filters_non_humans():
    class NoHuman(_FakeWikidataClient):
        def get(self, url, params):
            if params.get("action") == "wbgetentities" and params.get("props") != "labels":
                claims = {**_ENTITY["claims"], "P31": _snak({"id": "Q515"})}  # instance of city
                return _Resp({"entities": {"Q123": {**_ENTITY, "claims": claims}}})
            return super().get(url, params)

    assert WikidataProvider(client=NoHuman()).search("x") == []
