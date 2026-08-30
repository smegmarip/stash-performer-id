import pytest
from fastapi.testclient import TestClient

from bridge.app.api.deps import get_db
from bridge.app.cache.db import Database
from bridge.app.main import app


@pytest.fixture
def client(tmp_path):
    db = Database(str(tmp_path / "api.sqlite"))
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()
    db.close()


def test_summary_empty(client):
    r = client.get("/audit/summary")
    assert r.status_code == 200
    assert r.json()["distinct_names"] == 0


def test_direct_name_list_and_triage(client):
    r = client.post("/names", json={"name": "Test Person"})
    assert r.status_code == 200
    nid = r.json()["id"]
    assert r.json()["valid"] == 1  # valid by default

    body = client.get("/names").json()
    assert body["total"] >= 1
    assert any(n["name"] == "Test Person" for n in body["names"])

    r = client.patch(f"/names/{nid}", json={"valid": False})
    assert r.status_code == 200
    assert r.json()["valid"] == 0

    r = client.get("/names", params={"status": "invalid"})
    assert [n["id"] for n in r.json()["names"]] == [nid]

    r = client.get("/names", params={"status": "valid"})
    assert nid not in [n["id"] for n in r.json()["names"]]


def test_bulk_set_valid(client):
    ids = [client.post("/names", json={"name": n}).json()["id"] for n in ("A", "B", "C")]
    r = client.post("/names/set-valid", json={"ids": ids[:2], "valid": False})
    assert r.status_code == 200
    assert r.json()["updated"] == 2
    resp = client.get("/names", params={"status": "invalid"}).json()
    assert {n["name"] for n in resp["names"]} == {"A", "B"}


def test_patch_missing_name_404(client):
    r = client.patch("/names/424242", json={"valid": True})
    assert r.status_code == 404


def test_path_harvest_multiple_roots(client, tmp_path, monkeypatch):
    for sub in ("rootA/Jane Doe (Alabama)", "rootB/Mary Major (UVA)"):
        d = tmp_path / sub
        d.mkdir(parents=True)
        (d / "001.jpg").write_bytes(b"x")
    monkeypatch.setenv("TOP_FOLDER", f"{tmp_path}/rootA:{tmp_path}/rootB")
    from bridge.app.config import get_settings

    get_settings.cache_clear()
    try:
        r = client.post("/harvest/path", json={})
        assert r.status_code == 200
        assert r.json()["folders"] == 2  # one named folder per root, counts merged
        names = client.get("/names", params={"q": "doe"}).json()["names"]
        assert names[0]["name"] == "Jane Doe" and names[0]["disambiguation"] == "Alabama"
    finally:
        get_settings.cache_clear()
