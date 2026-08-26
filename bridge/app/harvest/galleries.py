"""Gallery harvest (DESIGN §9): enumerate Stash galleries and derive name candidates from
folder names and gallery titles, and create file-assets for member images (so a gallery-name
activation can cascade onto its images).
"""

import os

from bridge.app.cache.db import Database
from bridge.app.harvest.normalize import candidate
from bridge.app.stash.client import StashClient

_GALLERIES_QUERY = """
query Galleries($page: Int!, $per_page: Int!, $gf: GalleryFilterType) {
  findGalleries(
    gallery_filter: $gf
    filter: {page: $page, per_page: $per_page, sort: "path", direction: ASC}
  ) {
    count
    galleries {
      id
      title
      cover { id }
      folder { id path }
      files { id path basename }
    }
  }
}
"""

_GALLERY_IMAGES_QUERY = """
query GalleryImages($gid: ID!, $page: Int!, $per_page: Int!) {
  findImages(
    image_filter: {galleries: {value: [$gid], modifier: INCLUDES}}
    filter: {page: $page, per_page: $per_page}
  ) {
    count
    images {
      id
      visual_files {
        ... on ImageFile { path basename }
        ... on VideoFile { path basename }
      }
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
    db: Database, stash: StashClient, per_page: int = 100, progress=None,
    path_prefix: str | None = None,
) -> dict:
    """Populate asset/relationship/candidate tables from Stash galleries. Returns counts.

    When `path_prefix` is set (from TOP_FOLDER), only galleries whose path contains it are
    harvested; otherwise the whole library is swept.
    """
    # Stash has no STARTS_WITH modifier; INCLUDES is a substring match, which for a rooted
    # absolute path behaves as a prefix filter.
    gf = {"path": {"value": path_prefix, "modifier": "INCLUDES"}} if path_prefix else None
    page = 1
    total = None
    seen = 0
    images = 0
    while True:
        data = stash.query(
            _GALLERIES_QUERY, {"page": page, "per_page": per_page, "gf": gf}
        )
        block = data["findGalleries"]
        if total is None:
            total = block["count"]
        galleries = block["galleries"]
        if not galleries:
            break
        for g in galleries:
            images += _harvest_one(db, stash, g)
            seen += 1
            if progress and total:
                progress(min(seen / total, 1.0))
        db.commit()
        if len(galleries) < per_page:
            break
        page += 1

    new_names = db.rebuild_names()
    return {"galleries": seen, "images": images, "new_names": new_names}


def _harvest_one(db: Database, stash: StashClient, g: dict) -> int:
    """Harvest one gallery; returns the number of member-image assets created/seen."""
    folder = g.get("folder")
    files = g.get("files") or []
    # Gallery's own path (folder-based -> its folder; zip -> the zip; user-created -> none).
    g_path = folder["path"] if folder else (files[0]["path"] if files else None)
    # Display name: the folder/zip basename, else the gallery title (user-created galleries).
    g_basename = _basename_no_ext(g_path) or g.get("title")

    cover = g.get("cover") or {}
    gallery_asset_id = db.upsert_asset(
        "gallery",
        stash_entity_type="gallery",
        stash_id=str(g["id"]),
        path=g_path,
        basename=g_basename,
        thumb_stash_id=str(cover["id"]) if cover.get("id") else None,
    )

    gallery_name_src = g.get("title") or _basename_no_ext(g_path)
    if gallery_name_src and (cand := candidate(gallery_name_src)):
        db.add_candidate(gallery_asset_id, cand, "gallery", gallery_name_src)

    return _harvest_gallery_images(db, stash, str(g["id"]), gallery_asset_id)


def _harvest_gallery_images(
    db: Database, stash: StashClient, gallery_stash_id: str, gallery_asset_id: int,
    per_page: int = 500,
) -> int:
    """For each member image: a file-asset linked to the gallery, and a folder-asset derived
    from the image's own parent directory (so a gallery may span multiple folders). Returns count.
    """
    page = 1
    count = 0
    folder_cache: dict[str, int] = {}  # parent path -> folder asset id (per gallery)
    while True:
        data = stash.query(
            _GALLERY_IMAGES_QUERY,
            {"gid": gallery_stash_id, "page": page, "per_page": per_page},
        )
        images = data["findImages"]["images"]
        if not images:
            break
        for img in images:
            vfs = img.get("visual_files") or []
            path = vfs[0].get("path") if vfs else None
            img_id = str(img["id"])
            image_asset_id = db.upsert_asset(
                "file",
                stash_entity_type="image",
                stash_id=img_id,
                path=path,
                basename=_basename_no_ext(path),
                thumb_stash_id=img_id,  # a file's thumbnail is its own image
            )
            db.add_relationship(gallery_asset_id, image_asset_id, "gallery_image")
            # Backfill gallery thumbnail from the first member image (if it has no cover).
            db.set_thumb_if_null(gallery_asset_id, img_id)

            if path:
                parent = os.path.dirname(path)
                folder_asset_id = folder_cache.get(parent)
                if folder_asset_id is None:
                    folder_asset_id = db.upsert_asset(
                        "folder", path=parent, basename=_basename_no_ext(parent)
                    )
                    folder_cache[parent] = folder_asset_id
                    if (fcand := candidate(_basename_no_ext(parent) or "")):
                        db.add_candidate(folder_asset_id, fcand, "folder", parent)
                    # link gallery -> folder (candidate inheritance)
                    db.add_relationship(gallery_asset_id, folder_asset_id, "gallery_folder")
                db.add_relationship(folder_asset_id, image_asset_id, "folder_image")
                db.set_thumb_if_null(folder_asset_id, img_id)  # folder thumb = first image
            count += 1
        if len(images) < per_page:
            break
        page += 1
    return count
