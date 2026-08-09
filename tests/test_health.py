from fastapi.testclient import TestClient

from bridge.app.main import app

client = TestClient(app)


def test_healthz():
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "stash-performer-id"


def test_graphql_me():
    r = client.post("/graphql", json={"query": "{ me { name } }"})
    assert r.status_code == 200
    data = r.json()
    assert "errors" not in data, data
    assert data["data"]["me"]["name"] == "stash-performer-id"
