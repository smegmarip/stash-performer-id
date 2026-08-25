import pytest
from fastapi.testclient import TestClient

from bridge.app.api.deps import get_db
from bridge.app.cache.db import Database
from bridge.app.main import app


@pytest.fixture
def ctx(tmp_path):
    db = Database(str(tmp_path / "scrape.sqlite"))
    gallery = db.upsert_asset(
        "gallery", stash_entity_type="gallery", stash_id="g1",
        path="/lib/Jane Doe", basename="Jane Doe",
    )
    db.add_candidate(gallery, "Jane Doe", "gallery")
    imgs = {}
    for i in (1, 2):
        img = db.upsert_asset(
            "file", stash_entity_type="image", stash_id=f"i{i}", path=f"/lib/Jane Doe/{i}.jpg"
        )
        db.add_relationship(gallery, img, "gallery_image")
        imgs[i] = img
    db.commit()
    db.rebuild_names()
    nid = next(n["id"] for n in db.list_names() if n["name"] == "Jane Doe")
    db.activate_name(gallery, nid, "gallery")  # cascades onto both images
    app.dependency_overrides[get_db] = lambda: db
    yield db, TestClient(app), nid
    app.dependency_overrides.clear()
    db.close()


def test_scrape_by_image_id(ctx):
    _db, client, nid = ctx
    body = client.post("/scrape/image", json={"id": "i1", "files": []}).json()
    assert body["performers"] == [{"name": "Jane Doe", "remote_site_id": str(nid)}]


def test_scrape_falls_back_to_path(ctx):
    _db, client, nid = ctx
    # No id match; resolve via the file path instead.
    body = client.post(
        "/scrape/image", json={"id": "999", "files": [{"path": "/lib/Jane Doe/2.jpg"}]}
    ).json()
    assert body["performers"][0]["remote_site_id"] == str(nid)


def test_scrape_unknown_image_returns_empty(ctx):
    _db, client, _nid = ctx
    body = client.post(
        "/scrape/image", json={"id": "nope", "files": [{"path": "/lib/other/x.jpg"}]}
    ).json()
    assert body == {"performers": []}


def test_scrape_unassigned_image_returns_empty(ctx):
    db, client, _nid = ctx
    lone = db.upsert_asset("file", stash_entity_type="image", stash_id="i3", path="/lib/x/3.jpg")
    db.commit()
    body = client.post("/scrape/image", json={"id": "i3", "files": []}).json()
    assert body == {"performers": []}
    assert lone  # asset exists but carries no active name


def test_scrape_merges_enrichment_profile(ctx):
    db, client, nid = ctx
    db.apply_enrichment_profile(
        nid,
        {
            "name": {"value": "Jane Q. Doe", "source": "babepedia"},  # canonical spelling
            "gender": {"value": "Female", "source": "babepedia"},
            "country": {"value": "US", "source": "babepedia"},
            "aliases": {"value": ["Janie", "JD"], "source": "babepedia"},
            "urls": {"value": ["https://example.com/jane"], "source": "babepedia"},
            "images": {"value": ["https://example.com/jane.jpg"], "source": "babepedia"},
        },
    )
    p = client.post("/scrape/image", json={"id": "i1", "files": []}).json()["performers"][0]
    assert p["name"] == "Jane Q. Doe"  # profile name overrides the activated name
    assert p["remote_site_id"] == str(nid)
    assert p["gender"] == "Female" and p["country"] == "US"
    assert p["aliases"] == "Janie, JD"  # list -> comma-joined string (ScrapedPerformer shape)
    assert p["urls"] == ["https://example.com/jane"]
    assert p["images"] == ["https://example.com/jane.jpg"]
