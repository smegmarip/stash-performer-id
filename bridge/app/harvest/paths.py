"""Path harvest (DESIGN §9): crawl a filesystem tree rooted at `top_folder`.

Per the cascade model: if a folder name yields a candidate, record it at the folder level
(its files inherit it later, during activation). If a folder name yields nothing, fall back
to the image filenames inside it. The crawl root's own name is skipped (it's the container,
not a subject).
"""

import os

from bridge.app.cache.db import Database
from bridge.app.harvest.normalize import candidate

IMAGE_EXTS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp",
    ".tif", ".tiff", ".heic", ".heif", ".avif",
}


def harvest_path(db: Database, root: str, image_exts: set[str] = IMAGE_EXTS) -> dict:
    root = os.path.abspath(root)
    folders = 0
    files = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        is_root = os.path.abspath(dirpath) == root
        base = os.path.basename(dirpath.rstrip("/"))
        folder_cand = None if is_root else candidate(base)

        if folder_cand:
            fid = db.upsert_asset("folder", path=dirpath, basename=base)
            db.add_candidate(fid, folder_cand, "folder", base)
            folders += 1
        else:
            # Unnamed folder (or root): collapse to image filenames.
            for fn in filenames:
                stem, ext = os.path.splitext(fn)
                if ext.lower() not in image_exts:
                    continue
                if (fc := candidate(stem)):
                    aid = db.upsert_asset(
                        "file",
                        stash_entity_type="image",
                        path=os.path.join(dirpath, fn),
                        basename=stem,
                    )
                    db.add_candidate(aid, fc, "file", stem)
                    files += 1
        db.commit()

    new_names = db.rebuild_names()
    return {"folders": folders, "files": files, "new_names": new_names}
