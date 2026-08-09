"""Thumbnail proxy — serve Stash image thumbnails through the service so the browser needs
neither Stash's host nor its auth. The service fetches from Stash (via its configured URL)
and adds the API key when present.
"""

import httpx
from fastapi import APIRouter, HTTPException, Response

from bridge.app.config import get_settings

router = APIRouter()


@router.get("/thumbnail/{image_id}")
def thumbnail(image_id: str) -> Response:
    s = get_settings()
    url = f"{s.stash_url.rstrip('/')}/image/{image_id}/thumbnail"
    headers = {}
    if s.stash_api_key:
        headers["ApiKey"] = s.stash_api_key.get_secret_value()
    try:
        r = httpx.get(url, headers=headers, timeout=15.0, follow_redirects=True)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"stash unreachable: {e}") from e
    if r.status_code != 200:
        raise HTTPException(status_code=404, detail="thumbnail not found")
    return Response(
        content=r.content,
        media_type=r.headers.get("content-type", "image/jpeg"),
        headers={"Cache-Control": "public, max-age=3600"},
    )
