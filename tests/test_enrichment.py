import pytest
from fastapi.testclient import TestClient

from bridge.app.api.deps import get_db
from bridge.app.cache.db import Database
from bridge.app.main import app
from bridge.app.providers import (
    BabepediaProvider,
    NcaaProvider,
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

    def search(self, term, disambiguation=None):
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

    def search(self, term, disambiguation=None):
        self.calls += 1
        raise ProviderError("boom")


class _EmptyProvider:
    id = "emptytest"
    label = "Empty"
    metered = False

    def __init__(self):
        self.calls = 0

    def search(self, term, disambiguation=None):
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


# --- NCAA (stats.ncaa.org) ---

_NCAA_ROWS = {
    "aaData": [
        {  # oldest-first, as the real backend returns
            "people-last_name": (
                '<a target="person_1_win" class="skipMask" href="/players/1505026">'
                "Liz Gregorski</a>"
            ),
            "players-seasons_played": "1",
            "players-career": (
                '2001-02 - 2001-02 @<a href="/teams/history/WSO/75">'
                "Bridgewater (VA) Women&#39;s Soccer</a>"
            ),
        },
        {
            "people-last_name": (
                '<a target="person_2_win" class="skipMask" href="/players/8905834">'
                "Liz Gregorski</a>"
            ),
            "players-seasons_played": "6",
            "players-career": (
                '2019-20 - 2022-23 @<a href="/teams/history/WVB/754">'
                "Wisconsin Women&#39;s Volleyball</a>"
                '2023-24 - 2024-25 @<a href="/teams/history/WVB/327">'
                "Kansas St. Women&#39;s Volleyball</a>"
            ),
        },
    ]
}

_NCAA_DETAIL = """
<html><body>
<dl class="row mb-0"><dt>Name:</dt><dd>Morgan Family Arena</dd><dt>Capacity:</dt><dd>3,044</dd></dl>
<dl class="row mb-0 text-nowrap">
  <dt>Class:</dt><dd>Sr.</dd>
  <dt>Jersey #:</dt><dd>1</dd>
  <dt>Position #:</dt><dd>OH</dd>
  <dt>Height:</dt><dd>5-11</dd>
  <dt>Hometown:</dt><dd>Appleton, WI</dd>
  <dt>High School:</dt><dd>Xavier</dd>
</dl>
</body></html>
"""

_NCAA_CHALLENGE = """
<html><head><script> var i = 100; var j = i + Number("6052" + "30563"); </script></head>
<body><script>xhr.send(JSON.stringify({"bm-verify": "TOKEN123", "pow": j}));</script></body></html>
"""

# NCAA member directory (same org-id space as the career links: 327 = Kansas St.).
_NCAA_DIRECTORY = [
    {
        "orgId": 327,
        "nameOfficial": "Kansas State University",
        "athleticWebUrl": "www.kstatesports.com",
    },
]

_NCAA_ROSTER = """
<html><body>
<a href="/sports/womens-volleyball/roster/aliyah-carter/11942">Aliyah Carter</a>
<a href="/sports/womens-volleyball/roster/liz-gregorski/12660">Liz Gregorski</a>
</body></html>
"""

_NCAA_ROSTER_BIO = """
<html><head><meta property="og:image" content="https://images.sidearmdev.com/liz.png"></head>
<body>
<span>Position</span><span>Outside Hitter</span>
<span>Class</span><span>Redshirt Junior</span>
<span>Height</span><span>5-11</span>
<span>Hometown</span><span>Appleton, Wis.</span>
<span>High School</span><span>Xavier</span>
<span>Major</span><span>Kinesiology</span>
</body></html>
"""


class _NcaaResp:
    def __init__(self, text="", status=200, json_data=None):
        self.text = text
        self.status_code = status
        self._json = json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json


class _FakeNcaaClient:
    def __init__(self, challenge=False, detail_status=200, search_status=200, directory=None):
        self.challenge = challenge
        self.detail_status = detail_status
        self.search_status = search_status
        self.directory = _NCAA_DIRECTORY if directory is None else directory
        self.verified = False
        self.posts = []

    def get(self, url, params=None, headers=None, timeout=None):
        if "web3.ncaa.org/directory" in url:
            return _NcaaResp(json_data=self.directory)
        if "kstatesports.com/sports/womens-volleyball/roster/liz-gregorski/12660" in url:
            return _NcaaResp(text=_NCAA_ROSTER_BIO)
        if "kstatesports.com/sports/womens-volleyball/roster/2024" in url:
            return _NcaaResp(text=_NCAA_ROSTER)
        if "kstatesports.com" in url:
            return _NcaaResp(text="not found", status=404)
        if "/search/players/data" in url:
            return _NcaaResp(status=self.search_status, json_data=_NCAA_ROWS)
        if "/search/players" in url:
            return _NcaaResp(text="<html>search page</html>", status=self.search_status)
        if "stats.ncaa.org/players/" in url:
            if self.challenge and not self.verified:
                return _NcaaResp(text=_NCAA_CHALLENGE)
            return _NcaaResp(text=_NCAA_DETAIL, status=self.detail_status)
        raise AssertionError(f"unexpected URL {url}")

    def post(self, url, json=None, headers=None, timeout=None):
        self.posts.append((url, json))
        self.verified = True
        return _NcaaResp(json_data={"reload": True})


def _ncaa(client):
    return NcaaProvider(client=client, min_interval=0)


def test_ncaa_maps_search_and_sorts_recent_first():
    out = _ncaa(_FakeNcaaClient()).search("Liz Gregorski")
    assert len(out) == 2
    r = out[0]  # 2019-25 career sorts ahead of the 2001-02 one
    assert r.source == "ncaa" and r.source_entity_id == "8905834"
    assert r.name == "Liz Gregorski"
    assert r.gender == "Female"  # from "Women's" in the team names
    assert r.career_start == "2019" and r.career_end == "2025"  # "2024-25" ends in 2025
    assert r.height == "180"  # 5-11 -> cm
    assert r.country == "US"  # "Appleton, WI" -> US state
    assert r.urls[0] == "https://stats.ncaa.org/players/8905834"
    assert "Wisconsin Women's Volleyball" in r.disambiguation
    assert "2019–2025" in r.disambiguation
    assert r.custom_fields["ncaa_position"] == "OH"
    assert r.custom_fields["ncaa_class"] == "Sr."
    assert r.custom_fields["ncaa_jersey"] == "1"
    assert r.custom_fields["ncaa_hometown"] == "Appleton, WI"
    assert r.custom_fields["ncaa_high_school"] == "Xavier"
    assert "Kansas St. Women's Volleyball (2023–2025)" in r.custom_fields["ncaa_teams"]
    # Roster hop (directory -> Sidearm roster -> bio page): photo + bio URL + gap fill.
    assert r.images == ["https://images.sidearmdev.com/liz.png"]
    assert (
        "https://www.kstatesports.com/sports/womens-volleyball/roster/liz-gregorski/12660"
        in r.urls
    )
    assert r.custom_fields["ncaa_major"] == "Kinesiology"  # only the bio page has it
    assert r.custom_fields["ncaa_position"] == "OH"  # stats.ncaa.org wins over "Outside Hitter"
    assert r.custom_fields["ncaa_hometown"] == "Appleton, WI"  # not "Appleton, Wis."
    # The older candidate's school (org 75) is not in the directory -> roster hop skipped.
    assert out[1].source_entity_id == "1505026" and out[1].career_end == "2002"
    assert out[1].images == []


def test_ncaa_solves_akamai_interstitial():
    client = _FakeNcaaClient(challenge=True)
    r = _ncaa(client).search("Liz Gregorski")[0]
    url, body = client.posts[0]
    assert url.startswith("https://stats.ncaa.org/_sec/verify")
    assert body == {"bm-verify": "TOKEN123", "pow": 100 + 605230563}
    assert r.height == "180"  # detail parsed after the challenge cleared


def test_ncaa_keeps_row_candidate_on_detail_failure():
    r = _ncaa(_FakeNcaaClient(detail_status=500, directory=[])).search("Liz Gregorski")[0]
    assert r.source_entity_id == "8905834" and r.career_end == "2025"
    assert r.height is None and r.country is None  # bio fields missing, row fields kept
    assert set(r.custom_fields) == {"ncaa_teams"}  # detail-page keys absent


def test_ncaa_roster_bio_fills_gaps_when_detail_fails():
    # stats.ncaa.org detail page down -> the athletic-site bio supplies the missing fields.
    r = _ncaa(_FakeNcaaClient(detail_status=500)).search("Liz Gregorski")[0]
    assert r.custom_fields["ncaa_position"] == "Outside Hitter"
    assert r.custom_fields["ncaa_class"] == "Redshirt Junior"
    assert r.custom_fields["ncaa_hometown"] == "Appleton, Wis."
    assert r.custom_fields["ncaa_high_school"] == "Xavier"
    assert r.height == "180"  # 5-11 from the bio page
    assert r.images == ["https://images.sidearmdev.com/liz.png"]


def test_ncaa_exact_name_outranks_recency():
    # The backend fuzzy-matches: a more recent "Gregorsky" must not outrank the exact match.
    rows = {
        "aaData": _NCAA_ROWS["aaData"]
        + [
            {
                "people-last_name": '<a href="/players/9999999">Liz Gregorsky</a>',
                "players-seasons_played": "1",
                "players-career": (
                    '2025-26 - 2026-27 @<a href="/teams/history/WLA/75">'
                    "Coast Guard Women&#39;s Lacrosse</a>"
                ),
            }
        ]
    }

    class _Client(_FakeNcaaClient):
        def get(self, url, params=None, headers=None, timeout=None):
            if "/search/players/data" in url:
                return _NcaaResp(json_data=rows)
            return super().get(url, params, headers, timeout)

    out = _ncaa(_Client()).search("Liz Gregorski")
    assert out[0].source_entity_id == "8905834"  # exact + most recent
    assert [r.name for r in out] == ["Liz Gregorski", "Liz Gregorski", "Liz Gregorsky"]


def test_ncaa_wmt_grammar_and_slug_discovery():
    # Purdue-style site: WMT URL grammar, sport slug "volleyball" (not "womens-volleyball"),
    # discovered from the nav of the default-slug page; bio uses classic name="og:image".
    rows = {
        "aaData": [
            {
                "people-last_name": '<a href="/players/6271216">Blake Mohler</a>',
                "players-seasons_played": "5",
                "players-career": (
                    '2015-16 - 2019-20 @<a href="/teams/history/WVB/512">'
                    "Purdue Women&#39;s Volleyball</a>"
                ),
            }
        ]
    }
    nav_page = '<html><a href="/sports/volleyball/roster">Volleyball</a>no players here</html>'
    wmt_roster = (
        '<html><a href="/sports/volleyball/roster/season/2019/player/blake-mohler">'
        "Blake Mohler</a></html>"
    )
    wmt_bio = (
        '<html><head><meta name="og:image" content="https://purdue.test/blake.jpg"></head>'
        "<body><span>Position</span><span>MB</span></body></html>"
    )

    class _Client(_FakeNcaaClient):
        def get(self, url, params=None, headers=None, timeout=None):
            if "/search/players/data" in url:
                return _NcaaResp(json_data=rows)
            if "web3.ncaa.org/directory" in url:
                return _NcaaResp(json_data=[{"orgId": 512, "athleticWebUrl": "purdue.test"}])
            if "purdue.test/sports/womens-volleyball/roster/2019" in url:
                return _NcaaResp(text=nav_page)  # 200 but wrong slug: only the nav
            if "purdue.test/sports/volleyball/roster/season/2019/player/blake-mohler" in url:
                return _NcaaResp(text=wmt_bio)
            if "purdue.test/sports/volleyball/roster/season/2019" in url:
                return _NcaaResp(text=wmt_roster)
            if "purdue.test" in url:
                return _NcaaResp(text="not found", status=404)
            return super().get(url, params, headers, timeout)

    r = _ncaa(_Client(detail_status=500)).search("Blake Mohler")[0]
    assert (
        "https://purdue.test/sports/volleyball/roster/season/2019/player/blake-mohler" in r.urls
    )
    assert r.images == ["https://purdue.test/blake.jpg"]  # name="og:image" variant
    assert r.custom_fields["ncaa_position"] == "MB"


def test_ncaa_presto_grammar_with_homepage_slug_discovery():
    # Ferris State-style PrestoSports site: every default-slug URL 404s, the homepage nav
    # reveals the abbreviated slug ("wsoc"), the roster lives at /{season}/roster, and bios
    # use last_first_hash slugs.
    rows = {
        "aaData": [
            {
                "people-last_name": '<a href="/players/5555555">Morgan Irwin</a>',
                "players-seasons_played": "4",
                "players-career": (
                    '2016-17 - 2019-20 @<a href="/teams/history/WSO/224">'
                    "Ferris St. Women&#39;s Soccer</a>"
                ),
            }
        ]
    }
    # Nav link is site-absolute (as on ferrisstatebulldogs.com); bio has only twitter:image.
    home = '<html><a href="https://ferris.test/sports/wsoc/2025-26/roster">Soccer</a></html>'
    roster = (
        '<html><a href="/sports/wsoc/2019-20/bios/irwin_morgan_g6mz">Morgan Irwin</a></html>'
    )
    bio = (
        '<html><head><meta name="twitter:image" content="https://ferris.test/morgan.jpg">'
        "</head><body><span>Position</span><span>GK</span></body></html>"
    )

    class _Client(_FakeNcaaClient):
        def get(self, url, params=None, headers=None, timeout=None):
            if "/search/players/data" in url:
                return _NcaaResp(json_data=rows)
            if "web3.ncaa.org/directory" in url:
                return _NcaaResp(json_data=[{"orgId": 224, "athleticWebUrl": "ferris.test"}])
            if url.rstrip("/") == "https://ferris.test":
                return _NcaaResp(text=home)
            if "ferris.test/sports/wsoc/2019-20/bios/irwin_morgan_g6mz" in url:
                return _NcaaResp(text=bio)
            if "ferris.test/sports/wsoc/2019-20/roster" in url:
                return _NcaaResp(text=roster)
            if "ferris.test" in url:
                return _NcaaResp(text="not found", status=404)
            return super().get(url, params, headers, timeout)

    r = _ncaa(_Client(detail_status=500)).search("Morgan Irwin")[0]
    assert "https://ferris.test/sports/wsoc/2019-20/bios/irwin_morgan_g6mz" in r.urls
    assert r.images == ["https://ferris.test/morgan.jpg"]
    assert r.custom_fields["ncaa_position"] == "GK"


def test_ncaa_placeholder_share_image_skipped():
    p = PerformerData(source="ncaa", source_entity_id="1", name="X")
    page = '<meta property="og:image" content="https://x/images/setup/thumbnail_default.jpg">'
    _ncaa(_FakeNcaaClient())._apply_bio(p, page, "https://x/bio")
    assert p.images == []  # stock fallback image is not a headshot
    assert p.urls == ["https://x/bio"]


def test_ncaa_roster_skips_unknown_school():
    r = _ncaa(_FakeNcaaClient(directory=[])).search("Liz Gregorski")[0]
    assert r.images == [] and r.custom_fields.get("ncaa_major") is None
    assert r.custom_fields["ncaa_position"] == "OH"  # stats.ncaa.org data intact


def test_profile_custom_fields_round_trip(ctx):
    db, client, nid = ctx
    cf = {"ncaa_position": "OH", "ncaa_hometown": "Appleton, WI"}
    r = client.post(
        "/enrichment/profile",
        json={"name_id": nid, "fields": {"custom_fields": {"value": cf, "source": "ncaa"}}},
    )
    assert r.status_code == 200
    prof = db.get_enrichment_profile(nid)
    assert prof["custom_fields"] == cf  # dict round-trips via JSON column
    assert prof["field_sources"]["custom_fields"] == "ncaa"


def test_ncaa_blocked_raises_provider_error():
    with pytest.raises(ProviderError, match="Akamai"):
        _ncaa(_FakeNcaaClient(search_status=403)).search("x")


_NCAA_SEARCH_PAGE = (
    "<html>search page"
    '<select id=\\"org_id_filter\\"><option value=\\"\\">Filter by team</option>'
    '<option value=\\"8\\">Alabama</option>'
    '<option value=\\"327\\">Kansas St.</option></select></html>'
)


class _FakeOrgClient(_FakeNcaaClient):
    """Serves the org <select> on the search page and records data-endpoint params."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.data_params = []

    def get(self, url, params=None, headers=None, timeout=None):
        if "/search/players/data" in url:
            self.data_params.append(dict(params or {}))
            return _NcaaResp(json_data=_NCAA_ROWS)
        if "/search/players" in url:
            return _NcaaResp(text=_NCAA_SEARCH_PAGE)
        return super().get(url, params, headers, timeout)


def test_ncaa_school_hint_filters_by_org():
    client = _FakeOrgClient()
    out = _ncaa(client).search("Liz Gregorski", disambiguation="Kansas St.")
    assert client.data_params[0]["org_id_filter"] == "327"  # resolved from the org select
    assert len(client.data_params) == 1  # rows came back filtered: no unfiltered retry
    assert out[0].source_entity_id == "8905834"


def test_ncaa_unresolvable_hint_searches_unfiltered():
    client = _FakeOrgClient()
    out = _ncaa(client).search("Liz Gregorski", disambiguation="UVA")
    assert client.data_params[0]["org_id_filter"] == ""  # hint unused, single search
    assert len(out) == 2


def test_ncaa_school_hint_site_fallback_for_uncovered_sport():
    # No stats.ncaa.org rows (e.g. dance) -> scan the school site's rosters by name.
    home = '<html><a href="/sports/dance/roster">Dance</a></html>'
    roster = '<html><a href="/sports/dance/roster/abi-beckham/44">Abi Beckham</a></html>'
    bio = (
        '<html><head><meta property="og:image" content="https://tide.test/abi.jpg"></head>'
        "<body><span>Hometown</span><span>Mobile, AL</span></body></html>"
    )

    class _Client(_FakeOrgClient):
        def get(self, url, params=None, headers=None, timeout=None):
            if "/search/players/data" in url:
                self.data_params.append(dict(params or {}))
                return _NcaaResp(json_data={"aaData": []})
            if "web3.ncaa.org/directory" in url:
                return _NcaaResp(
                    json_data=[
                        {
                            "orgId": 8,
                            "nameOfficial": "University of Alabama",
                            "athleticWebUrl": "tide.test",
                        }
                    ]
                )
            if url.rstrip("/") == "https://tide.test":
                return _NcaaResp(text=home)
            if "tide.test/sports/dance/roster/abi-beckham/44" in url:
                return _NcaaResp(text=bio)
            if "tide.test/sports/dance/roster" in url:
                return _NcaaResp(text=roster)
            return super().get(url, params, headers, timeout)

    out = _ncaa(_Client()).search("Abi Beckham", disambiguation="Alabama")
    assert len(out) == 1
    r = out[0]
    assert r.source_entity_id == "/sports/dance/roster/abi-beckham/44"
    assert r.name == "Abi Beckham"
    assert r.disambiguation == "University of Alabama Dance"
    assert r.images == ["https://tide.test/abi.jpg"]
    assert r.custom_fields["ncaa_hometown"] == "Mobile, AL"
    assert r.country == "US"  # AL -> US state
    assert "https://tide.test/sports/dance/roster/abi-beckham/44" in r.urls
