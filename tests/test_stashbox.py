import pytest
import strawberry
from strawberry.schema.config import StrawberryConfig

from bridge.app import stashbox
from bridge.app.cache.db import Database


def test_stashbox_mapping():
    # A resolved enrichment_profile (dict) maps to a source-agnostic stash-box Performer keyed by
    # names.id. base_name/base_disambiguation are the name-record fallbacks.
    profile = {
        "name": "Jane Doe",
        "disambiguation": "model",
        "aliases": ["JD"],
        "gender": "Female",
        "birthdate": "1990-01-01",
        "ethnicity": "Caucasian",
        "country": "US",
        "eye_color": "Blue",
        "hair_color": "Blonde",
        "height": "170",
        "measurements": "34B-24-34",
        "fake_tits": "Natural",
        "career_start": "2010",
        "tattoos": "left arm rose",
        "urls": ["https://x/1"],
        "images": ["https://img/1.jpg"],
    }
    p = stashbox._to_performer(7, "fallback", None, profile)
    assert str(p.id) == "7"  # names.id, no source prefix
    assert p.name == "Jane Doe" and p.disambiguation == "model" and p.aliases == ["JD"]
    assert p.gender == stashbox.GenderEnum.FEMALE
    assert p.ethnicity == stashbox.EthnicityEnum.CAUCASIAN
    assert p.hair_color == stashbox.HairColorEnum.BLONDE
    assert p.eye_color == stashbox.EyeColorEnum.BLUE
    assert p.country == "US" and p.birth_date == "1990-01-01" and p.height == 170
    assert p.measurements.band_size == 34 and p.measurements.cup_size == "B"
    assert p.measurements.waist == 24 and p.measurements.hip == 34
    assert p.breast_type == stashbox.BreastTypeEnum.NATURAL and p.career_start_year == 2010
    assert [u.url for u in p.urls] == ["https://x/1"]
    assert p.images[0].url == "https://img/1.jpg" and str(p.images[0].id) == "7#0"
    assert p.tattoos[0].location == "left arm rose"


def test_stashbox_stub_profile_falls_back_to_name():
    # A stub profile (row exists, fields null) serves a name-only performer.
    p = stashbox._to_performer(3, "Bare Name", "disamb", {"field_sources": {}})
    assert str(p.id) == "3" and p.name == "Bare Name" and p.disambiguation == "disamb"
    assert p.gender is None and p.measurements is None and p.images == []


@pytest.mark.parametrize(
    "value,expected",
    [("34B-24-34", (34, "B", 24, 34)), ("32-22-32", (32, None, 22, 32)), ("junk", None)],
)
def test_measurements_parser(value, expected):
    m = stashbox._measurements(value)
    if expected is None:
        assert m is None
    else:
        assert (m.band_size, m.cup_size, m.waist, m.hip) == expected


def _schema():
    return strawberry.Schema(query=stashbox.Query, config=StrawberryConfig(auto_camel_case=False))


def test_stashbox_search_and_find(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "sb.sqlite"))
    monkeypatch.setattr(stashbox, "get_db", lambda: db)
    nid = db.add_direct_name("Jane Doe")["id"]
    db.apply_enrichment_profile(
        nid,
        {
            "gender": {"value": "Female", "source": "babepedia"},
            "country": {"value": "US", "source": "wikidata"},  # multi-source, never exposed
            "measurements": {"value": "34B-24-34", "source": "babepedia"},
        },
    )
    schema = _schema()

    r = schema.execute_sync(
        '{ searchPerformer(term: "Jane") { id name gender country measurements { band_size } } }'
    )
    assert r.errors is None
    perfs = r.data["searchPerformer"]
    assert len(perfs) == 1
    assert perfs[0]["id"] == str(nid) and perfs[0]["name"] == "Jane Doe"
    assert perfs[0]["gender"] == "FEMALE" and perfs[0]["country"] == "US"
    assert perfs[0]["measurements"]["band_size"] == 34

    # findPerformer by names.id round-trips the profile
    r2 = schema.execute_sync(f'{{ findPerformer(id: "{nid}") {{ name country }} }}')
    assert r2.data["findPerformer"] == {"name": "Jane Doe", "country": "US"}

    # searchPerformer with the id term (stash-id refresh) resolves too
    r3 = schema.execute_sync(f'{{ searchPerformer(term: "{nid}") {{ id }} }}')
    assert r3.data["searchPerformer"][0]["id"] == str(nid)

    # no match -> empty list; name without a profile -> null
    assert schema.execute_sync('{ searchPerformer(term: "Nobody") { id } }').data[
        "searchPerformer"
    ] == []
    nid2 = db.add_direct_name("No Profile")["id"]
    assert (
        schema.execute_sync(f'{{ findPerformer(id: "{nid2}") {{ name }} }}').data["findPerformer"]
        is None
    )
    db.close()


def test_stashbox_schema_builds():
    _schema()  # catches invalid Strawberry type wiring


def test_stashbox_scene_queries_are_noop():
    # The scene surface exists only so the scene tagger's queries validate (rather than erroring
    # with "Unknown type 'Scene'"); it always returns empty — we match scenes via the script scraper.
    schema = _schema()
    r = schema.execute_sync(
        'query($fp: [[FingerprintQueryInput!]!]!) {'
        " findScenesBySceneFingerprints(fingerprints: $fp) { id title performers { performer { id } } }"
        " }",
        variable_values={"fp": [[{"hash": "abc", "algorithm": "PHASH"}]]},
    )
    assert r.errors is None
    assert r.data["findScenesBySceneFingerprints"] == [[]]  # one empty match set per input scene

    r2 = schema.execute_sync('{ searchScene(term: "anything") { id } findScene(id: "1") { id } }')
    assert r2.errors is None
    assert r2.data == {"searchScene": [], "findScene": None}
