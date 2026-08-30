"""Scene harvest (DESIGN §9): enumerate Stash scenes and derive name candidates from the scene's
parent folder name and its title/filename, so a folder-name activation cascades onto the scenes
inside it — reusing the same `folder_image` cascade galleries use for their images.

A scene is a single video file, so (unlike a gallery) it has no members of its own: the scene
file-asset is the leaf. We still create a folder-asset for its parent directory so a
performer-named folder can carry its name down to every scene (and image) it contains.
"""

import os

from bridge.app.cache.db import Database
from bridge.app.harvest.normalize import candidate_parts
from bridge.app.stash.client import StashClient

_SCENES_QUERY = """
query Scenes($page: Int!, $per_page: Int!, $sf: SceneFilterType) {
  findScenes(
    scene_filter: $sf
    filter: {page: $page, per_page: $per_page, sort: "path", direction: ASC}
  ) {
    count
    scenes {
      id
      title
      files { path basename }
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


def harvest_scenes(
    db: Database, stash: StashClient, per_page: int = 100, progress=None,
    path_prefix: str | None = None,
) -> dict:
    """Populate asset/relationship/candidate tables from Stash scenes. Returns counts.

    When `path_prefix` is set (from TOP_FOLDER), only scenes whose path contains it are harvested;
    otherwise the whole library is swept.
    """
    # Stash has no STARTS_WITH modifier; INCLUDES is a substring match, which for a rooted
    # absolute path behaves as a prefix filter (same trick as the gallery harvest).
    sf = {"path": {"value": path_prefix, "modifier": "INCLUDES"}} if path_prefix else None
    page = 1
    total = None
    seen = 0
    folder_cache: dict[str, int] = {}  # parent path -> folder asset id (whole harvest)
    while True:
        data = stash.query(_SCENES_QUERY, {"page": page, "per_page": per_page, "sf": sf})
        block = data["findScenes"]
        if total is None:
            total = block["count"]
        scenes = block["scenes"]
        if not scenes:
            break
        for s in scenes:
            _harvest_one(db, s, folder_cache)
            seen += 1
            if progress and total:
                progress(min(seen / total, 1.0))
        db.commit()
        if len(scenes) < per_page:
            break
        page += 1

    new_names = db.rebuild_names()
    return {"scenes": seen, "new_names": new_names}


def _harvest_one(db: Database, s: dict, folder_cache: dict[str, int]) -> None:
    """Harvest one scene: its file-asset (+ file-level title/filename candidate) and, from its
    parent directory, a folder-asset (+ folder-name candidate) linked so folder activation
    cascades onto the scene."""
    files = s.get("files") or []
    path = files[0]["path"] if files else None
    basename = _basename_no_ext(path)

    scene_asset_id = db.upsert_asset(
        "file",
        stash_entity_type="scene",
        stash_id=str(s["id"]),
        path=path,
        basename=basename,
        thumb_stash_id=None,  # scenes have no member-image id; the viewer shows a placeholder
    )

    # File-level candidate from the scene title (preferred, usually cleaner) else the filename.
    name_src = s.get("title") or basename
    if name_src and (cand := candidate_parts(name_src)):
        db.add_candidate(
            scene_asset_id, cand[0], "file", name_src, disambiguation=cand[1]
        )

    if not path:
        return
    parent = os.path.dirname(path)
    folder_asset_id = folder_cache.get(parent)
    if folder_asset_id is None:
        folder_asset_id = db.upsert_asset(
            "folder", path=parent, basename=_basename_no_ext(parent)
        )
        folder_cache[parent] = folder_asset_id
        if fcand := candidate_parts(_basename_no_ext(parent) or ""):
            db.add_candidate(
                folder_asset_id, fcand[0], "folder", parent, disambiguation=fcand[1]
            )
    # Reuse the gallery cascade kind: folder activation reaches its member files (images + scenes).
    db.add_relationship(folder_asset_id, scene_asset_id, "folder_image")
