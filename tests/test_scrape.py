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


def _add_scene(db, folder_path, scene_stash_id, scene_path, folder_name):
    """A folder + a scene file linked by folder_image, with a folder-name candidate; activation of
    the folder cascades onto the scene (mirrors harvest_scenes)."""
    folder = db.upsert_asset("folder", path=folder_path, basename=folder_name)
    db.add_candidate(folder, folder_name, "folder")
    scene = db.upsert_asset(
        "file", stash_entity_type="scene", stash_id=scene_stash_id, path=scene_path
    )
    db.add_relationship(folder, scene, "folder_image")
    db.commit()
    db.rebuild_names()
    nid = next(n["id"] for n in db.list_names() if n["name"] == folder_name)
    db.activate_name(folder, nid, "folder")  # cascades onto the scene
    return scene, nid


def test_scrape_scene_by_id(ctx):
    db, client, _ = ctx
    _scene, nid = _add_scene(db, "/lib/Scene Star", "s1", "/lib/Scene Star/clip.mp4", "Scene Star")
    body = client.post("/scrape/scene", json={"id": "s1", "files": []}).json()
    assert body["performers"] == [{"name": "Scene Star", "remote_site_id": str(nid)}]


def test_scrape_scene_falls_back_to_path(ctx):
    db, client, _ = ctx
    _scene, nid = _add_scene(db, "/lib/Scene Star", "s1", "/lib/Scene Star/clip.mp4", "Scene Star")
    body = client.post(
        "/scrape/scene", json={"id": "999", "files": [{"path": "/lib/Scene Star/clip.mp4"}]}
    ).json()
    assert body["performers"][0]["remote_site_id"] == str(nid)


def test_scrape_scene_id_does_not_match_image(ctx):
    # The id branch is entity_type-scoped: an image id posted to /scrape/scene must not resolve.
    _db, client, _nid = ctx
    body = client.post("/scrape/scene", json={"id": "i1", "files": []}).json()
    assert body == {"performers": []}


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
    assert p["images"] == ["https://example.com/jane.jpg"]  # non-proxied host stays direct


def test_scrape_proxies_hotlink_protected_images(ctx):
    db, client, nid = ctx
    db.apply_enrichment_profile(
        nid,
        {"images": {"value": ["https://thehandbook.com/p/jane.jpg"], "source": "parsebot"}},
    )
    p = client.post("/scrape/image", json={"id": "i1", "files": []}).json()["performers"][0]
    # thehandbook.com is a default proxy host -> the URL is rewritten through /image-proxy.
    assert p["images"][0].startswith("http://localhost:15000/image-proxy?url=")
    assert "thehandbook.com" in p["images"][0]  # original URL encoded in the query


def test_scrape_renders_custom_fields_into_details(ctx):
    db, client, nid = ctx
    db.apply_enrichment_profile(
        nid,
        {
            "details": {"value": "A volleyball player.", "source": "ncaa"},
            "custom_fields": {
                "value": {"ncaa_position": "OH", "ncaa_high_school": "Xavier"},
                "source": "ncaa",
            },
        },
    )
    p = client.post("/scrape/image", json={"id": "i1", "files": []}).json()["performers"][0]
    # The map itself can't ride ScrapedPerformer — it lands as a templated details paragraph,
    # appended after the profile's own details, with the source prefix stripped from labels.
    assert p["details"] == "A volleyball player.\n\nNCAA — Position: OH · High School: Xavier"


def test_scrape_custom_fields_paragraph_without_details(ctx):
    db, client, nid = ctx
    db.apply_enrichment_profile(
        nid,
        {"custom_fields": {"value": {"ncaa_class": "Sr."}, "source": "ncaa"}},
    )
    p = client.post("/scrape/image", json={"id": "i1", "files": []}).json()["performers"][0]
    assert p["details"] == "NCAA — Class: Sr."


def test_scrape_ignored_image_returns_empty(ctx):
    # An assigned image that is then ignored resolves to nothing (removed from scraping).
    db, client, _nid = ctx
    assert client.post("/scrape/image", json={"id": "i1", "files": []}).json()["performers"]
    # Ignore the gallery -> cascades onto image i1.
    gallery = db.conn.execute(
        "SELECT id FROM asset WHERE resource_type='gallery'"
    ).fetchone()["id"]
    db.ignore_asset(gallery, True)
    body = client.post("/scrape/image", json={"id": "i1", "files": []}).json()
    assert body == {"performers": []}
