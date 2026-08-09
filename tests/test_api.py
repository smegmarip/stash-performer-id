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
