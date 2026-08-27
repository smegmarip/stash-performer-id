"""The image-scraper transport is a standalone stdlib script; load it by path and exercise the
endpoint-discovery helpers with a mocked Stash GraphQL."""

import importlib.util
import json
from pathlib import Path


def _load():
    path = Path(__file__).resolve().parent.parent / "scraper" / "stash-performer-id-scrape.py"
    spec = importlib.util.spec_from_file_location("spid_scraper", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)  # side-effect-free: BASE is resolved lazily in main()
    return m


class _Resp:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _boxes(*pairs):
    return {
        "data": {
            "configuration": {
                "general": {
                    "stashBoxes": [{"name": n, "endpoint": e} for n, e in pairs]
                }
            }
        }
    }


def test_endpoint_to_base():
    m = _load()
    assert m._endpoint_to_base("http://api:15000/graphql") == "http://api:15000"
    assert m._endpoint_to_base("http://api:15000/graphql/") == "http://api:15000"
    assert m._endpoint_to_base("http://api:15000") == "http://api:15000"


def test_base_resolved_from_registered_box(monkeypatch):
    m = _load()
    payload = _boxes(
        ("IAFD", "http://x/graphql"),
        ("Stash Performer ID", "http://api-host:15000/graphql"),
    )
    monkeypatch.setattr(m.urllib.request, "urlopen", lambda *a, **k: _Resp(payload))
    assert m._base_from_stash() == "http://api-host:15000"


def test_base_none_when_box_absent(monkeypatch):
    m = _load()
    monkeypatch.setattr(
        m.urllib.request, "urlopen", lambda *a, **k: _Resp(_boxes(("IAFD", "http://x/graphql")))
    )
    assert m._base_from_stash() is None


def test_env_override_wins(monkeypatch):
    m = _load()
    monkeypatch.setenv("STASH_PERFORMER_ID_URL", "http://override:9000/")
    # Should not even consult Stash when the explicit override is set.
    monkeypatch.setattr(
        m.urllib.request,
        "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not query Stash")),
    )
    assert m._resolve_base() == "http://override:9000"
