import pytest
from fastapi.testclient import TestClient

from bridge.app.api.deps import get_db
from bridge.app.cache.db import Database
from bridge.app.main import app


@pytest.fixture
def ctx(tmp_path):
    db = Database(str(tmp_path / "assets.sqlite"))
    folder = db.upsert_asset(
        "folder", stash_entity_type="folder", stash_id="f1",
        path="/lib/Jane Doe", basename="Jane Doe",
    )
    gallery = db.upsert_asset(
        "gallery", stash_entity_type="gallery", stash_id="g1",
        path="/lib/Jane Doe", basename="Jane Doe",
    )
    db.add_relationship(gallery, folder, "gallery_folder")
    db.add_candidate(folder, "Jane Doe", "folder")
    # two member images
    for i in (1, 2):
        img = db.upsert_asset(
            "file", stash_entity_type="image", stash_id=f"i{i}", path=f"/lib/Jane Doe/{i}.jpg"
        )
        db.add_relationship(gallery, img, "gallery_image")
    db.commit()
    db.rebuild_names()
    app.dependency_overrides[get_db] = lambda: db
    yield db, TestClient(app), gallery
    app.dependency_overrides.clear()
    db.close()


def _galleries(client):
    return client.get("/assets", params={"type": "gallery"}).json()["assets"]


def _name_id(client, name):
    g = _galleries(client)[0]
    return next(c["name_id"] for c in g["candidates"] if c["name"] == name)


def test_galleries_expose_folder_candidate_and_image_count(ctx):
    _db, client, gallery = ctx
    body = client.get("/assets", params={"type": "gallery"}).json()
    assert body["total"] == 1
    g = body["assets"][0]
    assert g["asset_id"] == gallery
    assert g["child_count"] == 2
    assert g["resource_type"] == "gallery"
    assert any(c["name"] == "Jane Doe" for c in g["candidates"])
    assert g["active"] is None


def test_files_view_lists_member_images(ctx):
    _db, client, _gallery = ctx
    body = client.get("/assets", params={"type": "file"}).json()
    assert body["total"] == 2  # the two member images are file-assets


def test_activation_cascades_to_images(ctx):
    db, client, gallery = ctx
    nid = _name_id(client, "Jane Doe")
    client.post(f"/assets/{gallery}/activate", json={"name_id": nid})
    # gallery + its 2 images all carry the active name.
    total = db.conn.execute(
        "SELECT COUNT(*) n FROM name_relationship WHERE name_id = ? AND active = 1", (nid,)
    ).fetchone()["n"]
    assert total == 3

    client.delete(f"/assets/{gallery}/activation")
    remaining = db.conn.execute("SELECT COUNT(*) n FROM name_relationship").fetchone()["n"]
    assert remaining == 0


def test_activate_replaces(ctx):
    _db, client, gallery = ctx
    nid = _name_id(client, "Jane Doe")
    client.post(f"/assets/{gallery}/activate", json={"name_id": nid})
    other = client.post("/names", json={"name": "Someone Else"}).json()["id"]
    client.post(f"/assets/{gallery}/activate", json={"name_id": other})
    assert _galleries(client)[0]["active"]["name"] == "Someone Else"
