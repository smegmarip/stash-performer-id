"""Image proxy — refetch an enrichment image server-side with anti-hotlinking headers, so Stash
(and in-UI previews) can load images from hosts like thehandbook.com that block direct hotlinks by
Referer.

`proxy_image_url` rewrites a profile image URL to point at `/image-proxy` when its host is
configured for proxying (config `image_proxy_hosts`); it's applied where images are handed to Stash
— the scrape endpoints and the stash-box relay. Stash downloads the image once at performer-create
time, so the proxy sits only on the create path, not permanently.
"""

import ipaddress
import socket
from urllib.parse import quote, urlparse

import httpx
from fastapi import APIRouter, HTTPException, Query, Response

from bridge.app.config import get_settings

router = APIRouter()

# A real browser UA — some hosts reject the default httpx UA outright.
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)


def _proxy_hosts() -> list[str]:
    raw = get_settings().image_proxy_hosts or ""
    return [h.strip().lower() for h in raw.split(",") if h.strip()]


def _host_needs_proxy(host: str, hosts: list[str]) -> bool:
    host = host.lower()
    # "*" = all; otherwise exact host or a subdomain of a configured suffix (img.thehandbook.com).
    return any(h == "*" or host == h or host.endswith("." + h) for h in hosts)


def proxy_image_url(url: str | None) -> str | None:
    """Rewrite an external image URL through /image-proxy when its host is configured for proxying;
    return it unchanged otherwise (so working hosts like babepedia stay direct)."""
    if not url:
        return url
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return url
    if not host or not _host_needs_proxy(host, _proxy_hosts()):
        return url
    base = get_settings().public_base_url.rstrip("/")
    return f"{base}/image-proxy?url={quote(url, safe='')}"


def _is_private_host(host: str) -> bool:
    """SSRF guard: refuse hosts that resolve to loopback/private/link-local/reserved space (or that
    don't resolve at all)."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return True
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return True
    return False


@router.get("/image-proxy")
def image_proxy(url: str = Query(...)) -> Response:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise HTTPException(status_code=400, detail="only absolute http(s) URLs are proxied")
    if _is_private_host(parsed.hostname):
        raise HTTPException(status_code=400, detail="host not allowed")
    # Anti-hotlinking: a same-origin Referer + a real browser UA is what these hosts check.
    headers = {
        "User-Agent": _BROWSER_UA,
        "Referer": f"{parsed.scheme}://{parsed.netloc}/",
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    }
    try:
        r = httpx.get(url, headers=headers, timeout=20.0, follow_redirects=True)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"upstream fetch failed: {e}") from e
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"upstream returned {r.status_code}")
    ctype = r.headers.get("content-type", "").split(";")[0].strip() or "application/octet-stream"
    if not (ctype.startswith("image/") or ctype == "application/octet-stream"):
        raise HTTPException(status_code=415, detail=f"not an image ({ctype})")
    return Response(
        content=r.content,
        media_type="image/jpeg" if ctype == "application/octet-stream" else ctype,
        headers={"Cache-Control": "public, max-age=3600"},
    )
