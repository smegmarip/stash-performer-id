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
    db.commit()
    db.rebuild_names()
    app.dependency_overrides[get_db] = lambda: db
    yield db, TestClient(app), gallery
    app.dependency_overrides.clear()
    db.close()


def _name_id(client, name):
    g = client.get("/assets/galleries").json()[0]
    return next(c["name_id"] for c in g["candidates"] if c["name"] == name)


def test_galleries_expose_folder_candidate(ctx):
    _db, client, gallery = ctx
    gals = client.get("/assets/galleries").json()
    assert len(gals) == 1
    g = gals[0]
    assert g["asset_id"] == gallery
    assert any(c["name"] == "Jane Doe" for c in g["candidates"])
    assert g["active"] is None


def test_activate_replaces_and_deactivates(ctx):
    _db, client, gallery = ctx
    nid = _name_id(client, "Jane Doe")
    assert client.post(f"/assets/{gallery}/activate", json={"name_id": nid}).status_code == 200
    assert client.get("/assets/galleries").json()[0]["active"]["name"] == "Jane Doe"

    other = client.post("/names", json={"name": "Someone Else"}).json()["id"]
    client.post(f"/assets/{gallery}/activate", json={"name_id": other})
    assert client.get("/assets/galleries").json()[0]["active"]["name"] == "Someone Else"

    client.delete(f"/assets/{gallery}/activation")
    assert client.get("/assets/galleries").json()[0]["active"] is None
