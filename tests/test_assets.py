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
    names = client.get("/names").json()["names"]
    return next(n["id"] for n in names if n["name"] == name)


def test_galleries_expose_image_count(ctx):
    _db, client, gallery = ctx
    body = client.get("/assets", params={"type": "gallery"}).json()
    assert body["total"] == 1
    g = body["assets"][0]
    assert g["asset_id"] == gallery
    assert g["child_count"] == 2
    assert g["resource_type"] == "gallery"
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


def test_ignore_endpoint_cascades_and_filters(ctx):
    db, client, gallery = ctx
    r = client.post(f"/assets/{gallery}/ignore").json()
    assert r["ok"] and r["affected"] == 3  # gallery + 2 images

    # Ignored gallery is out of assigned/unassigned, present under "ignored".
    assert client.get("/assets", params={"type": "gallery", "assigned": "unassigned"}).json()[
        "total"
    ] == 0
    ign = client.get("/assets", params={"type": "gallery", "assigned": "ignored"}).json()
    assert ign["total"] == 1 and ign["assets"][0]["ignored"] is True
    # Member images are ignored too (file scope).
    assert client.get("/assets", params={"type": "file", "assigned": "ignored"}).json()[
        "total"
    ] == 2

    # Un-ignore restores them to the unassigned bucket.
    client.delete(f"/assets/{gallery}/ignore")
    assert client.get("/assets", params={"type": "gallery", "assigned": "unassigned"}).json()[
        "total"
    ] == 1
    assert client.get("/assets", params={"type": "gallery", "assigned": "ignored"}).json()[
        "total"
    ] == 0


def test_ignore_clears_assignment_and_assign_clears_ignore(ctx):
    db, client, gallery = ctx
    nid = _name_id(client, "Jane Doe")
    client.post(f"/assets/{gallery}/activate", json={"name_id": nid})
    # Ignoring an assigned asset clears the assignment (mutually exclusive states).
    client.post(f"/assets/{gallery}/ignore")
    assert db.conn.execute("SELECT COUNT(*) n FROM name_relationship").fetchone()["n"] == 0
    assert client.get("/assets", params={"type": "gallery", "assigned": "ignored"}).json()[
        "total"
    ] == 1
    # Assigning again clears the ignore.
    client.post(f"/assets/{gallery}/activate", json={"name_id": nid})
    g = client.get("/assets", params={"type": "gallery"}).json()["assets"][0]
    assert g["ignored"] is False and g["active"]["name_id"] == nid


def test_ignore_bulk(ctx):
    _db, client, gallery = ctx
    r = client.post("/assets/ignore", json={"ids": [gallery], "ignored": True}).json()
    assert r["ok"] and r["affected"] == 3
    assert client.get("/assets", params={"type": "gallery", "assigned": "ignored"}).json()[
        "total"
    ] == 1
