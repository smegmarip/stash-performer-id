import httpx

from bridge.app.stash.client import StashClient


def _client_returning(content: bytes) -> StashClient:
    """A StashClient whose transport returns `content` verbatim (bypasses httpx encoding)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=content, headers={"content-type": "application/json"})

    s = StashClient("http://stash.test:9999")
    s._client = httpx.Client(transport=httpx.MockTransport(handler))
    return s


def test_query_tolerates_non_utf8_paths():
    # A Latin-1-named file (0xC9 = "É") makes Stash's JSON body invalid UTF-8; strict decoding
    # would raise UnicodeDecodeError and abort the harvest. We decode leniently instead.
    body = b'{"data":{"findImages":{"images":[{"id":"1","path":"/m/Andr\xc9/a.jpg"}]}}}'
    with _client_returning(body) as s:
        data = s.query("{ findImages { images { id path } } }")
    assert data["findImages"]["images"][0]["path"] == "/m/Andr�/a.jpg"


def test_query_preserves_valid_unicode():
    body = '{"data":{"x":"Renée café ✓"}}'.encode()
    with _client_returning(body) as s:
        assert s.query("{ x }")["x"] == "Renée café ✓"
