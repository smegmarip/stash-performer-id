from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from bridge.app.api import imageproxy
from bridge.app.main import app


def _settings(hosts: str, base: str = "http://svc:15000"):
    return SimpleNamespace(image_proxy_hosts=hosts, public_base_url=base)


@pytest.mark.parametrize(
    "hosts,url,proxied",
    [
        ("thehandbook.com", "https://thehandbook.com/p/x.jpg", True),
        ("thehandbook.com", "https://img.thehandbook.com/x.jpg", True),  # subdomain
        ("thehandbook.com", "https://www.babepedia.com/x.jpg", False),  # working host untouched
        ("thehandbook.com", "https://notthehandbook.com/x.jpg", False),  # not a real suffix match
        ("*", "https://anything.example/x.jpg", True),  # wildcard proxies all
        ("thehandbook.com", None, False),
        ("", "https://thehandbook.com/x.jpg", False),  # feature off -> untouched
    ],
)
def test_proxy_image_url(monkeypatch, hosts, url, proxied):
    monkeypatch.setattr(imageproxy, "get_settings", lambda: _settings(hosts))
    out = imageproxy.proxy_image_url(url)
    if proxied:
        assert out is not None
        assert out.startswith("http://svc:15000/image-proxy?url=")
        assert "thehandbook.com" in out or "anything.example" in out  # original URL is encoded in
    else:
        assert out == url


def test_private_host_guard():
    assert imageproxy._is_private_host("localhost") is True
    assert imageproxy._is_private_host("127.0.0.1") is True
    assert imageproxy._is_private_host("10.0.0.5") is True
    assert imageproxy._is_private_host("192.168.1.20") is True
    assert imageproxy._is_private_host("169.254.1.1") is True
    assert imageproxy._is_private_host("no-such-host.invalid") is True  # unresolvable -> refuse
    assert imageproxy._is_private_host("93.184.216.34") is False  # public IP literal


def test_image_proxy_endpoint_rejects_private_and_bad_scheme():
    client = TestClient(app)
    assert client.get("/image-proxy", params={"url": "ftp://x/y.jpg"}).status_code == 400
    assert client.get("/image-proxy", params={"url": "http://127.0.0.1/y.jpg"}).status_code == 400
    assert client.get("/image-proxy", params={"url": "http://localhost:9999/y.jpg"}).status_code == 400


def test_image_proxy_endpoint_fetches_with_antihotlink_headers(monkeypatch):
    captured = {}

    class _Resp:
        status_code = 200
        content = b"\xff\xd8\xff-jpeg-bytes"
        headers = {"content-type": "image/jpeg"}

    def _fake_get(url, headers=None, timeout=None, follow_redirects=None):
        captured["url"] = url
        captured["headers"] = headers
        return _Resp()

    monkeypatch.setattr(imageproxy.httpx, "get", _fake_get)
    client = TestClient(app)
    # A public IP literal passes the SSRF guard without a DNS lookup.
    r = client.get("/image-proxy", params={"url": "https://93.184.216.34/pic.jpg"})
    assert r.status_code == 200
    assert r.content == b"\xff\xd8\xff-jpeg-bytes"
    assert r.headers["content-type"].startswith("image/jpeg")
    # The anti-hotlink headers are what defeats the Referer check.
    assert captured["headers"]["Referer"] == "https://93.184.216.34/"
    assert "Chrome" in captured["headers"]["User-Agent"]


def test_image_proxy_endpoint_rejects_non_image(monkeypatch):
    class _Resp:
        status_code = 200
        content = b"<html>"
        headers = {"content-type": "text/html"}

    monkeypatch.setattr(imageproxy.httpx, "get", lambda *a, **k: _Resp())
    client = TestClient(app)
    r = client.get("/image-proxy", params={"url": "https://93.184.216.34/x"})
    assert r.status_code == 415
