"""Gallery harvest (DESIGN §9): enumerate Stash galleries and derive name candidates from
folder names and gallery titles (and, optionally, member image basenames).

Folder/gallery names are the high-value signal; image harvest is opt-in (galleries can hold
thousands of images, most with digit-only basenames that yield no candidate).
"""

import os

from bridge.app.cache.db import Database
from bridge.app.harvest.normalize import candidate
from bridge.app.stash.client import StashClient

_GALLERIES_QUERY = """
query Galleries($page: Int!, $per_page: Int!) {
  findGalleries(filter: {page: $page, per_page: $per_page, sort: "path", direction: ASC}) {
    count
    galleries {
      id
      title
      folder { id path }
      files { id path basename }
    }
  }
}
"""


def _basename_no_ext(path: str | None) -> str | None:
    if not path:
        return None
    base = os.path.basename(path.rstrip("/"))
    stem, _ = os.path.splitext(base)
    return stem or base


def harvest_galleries(
    db: Database, stash: StashClient, per_page: int = 100, progress=None
) -> dict:
    """Populate asset/relationship/candidate tables from Stash galleries. Returns counts."""
    page = 1
    total = None
    seen = 0
    while True:
        data = stash.query(_GALLERIES_QUERY, {"page": page, "per_page": per_page})
        block = data["findGalleries"]
        if total is None:
            total = block["count"]
        galleries = block["galleries"]
        if not galleries:
            break
        for g in galleries:
            _harvest_one(db, g)
            seen += 1
            if progress and total:
                progress(min(seen / total, 1.0))
        db.commit()
        if len(galleries) < per_page:
            break
        page += 1

    new_names = db.rebuild_names()
    return {"galleries": seen, "new_names": new_names}


def _harvest_one(db: Database, g: dict) -> None:
    folder = g.get("folder")
    files = g.get("files") or []

    folder_asset_id = None
    if folder and folder.get("path"):
        folder_asset_id = db.upsert_asset(
            "folder",
            stash_entity_type="folder",
            stash_id=str(folder["id"]),
            path=folder["path"],
            basename=_basename_no_ext(folder["path"]),
        )
        if (cand := candidate(_basename_no_ext(folder["path"]) or "")):
            db.add_candidate(folder_asset_id, cand, "folder", folder["path"])

    # Gallery asset. Path = folder path (folder-based) or first file path (zip-based).
    g_path = folder["path"] if folder else (files[0]["path"] if files else None)
    gallery_asset_id = db.upsert_asset(
        "gallery",
        stash_entity_type="gallery",
        stash_id=str(g["id"]),
        path=g_path,
        basename=_basename_no_ext(g_path),
    )

    # Gallery name: title, else the folder/zip basename.
    gallery_name_src = g.get("title") or _basename_no_ext(g_path)
    if gallery_name_src and (cand := candidate(gallery_name_src)):
        db.add_candidate(gallery_asset_id, cand, "gallery", gallery_name_src)

    if folder_asset_id is not None:
        db.add_relationship(gallery_asset_id, folder_asset_id, "gallery_folder")
