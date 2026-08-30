from bridge.app.cache.db import Database
from bridge.app.harvest.galleries import harvest_galleries


class _RecordingStash:
    """Records the variables of each query and returns empty result pages."""

    def __init__(self):
        self.calls = []

    def query(self, query: str, variables=None) -> dict:
        self.calls.append(variables or {})
        if "findGalleries" in query:
            return {"findGalleries": {"count": 0, "galleries": []}}
        return {"findImages": {"count": 0, "images": []}}


def test_top_folder_scopes_gallery_query(tmp_path):
    db = Database(str(tmp_path / "h.sqlite"))
    stash = _RecordingStash()
    harvest_galleries(db, stash, path_prefix="/data/pics/exposed/celeb")
    gf = stash.calls[0]["gf"]
    assert gf == {"path": {"value": "/data/pics/exposed/celeb", "modifier": "INCLUDES"}}
    db.close()


def test_no_top_folder_harvests_whole_library(tmp_path):
    db = Database(str(tmp_path / "h.sqlite"))
    stash = _RecordingStash()
    harvest_galleries(db, stash, path_prefix=None)
    # gf is null -> Stash applies no gallery_filter (whole library).
    assert stash.calls[0]["gf"] is None
    db.close()


class _GalleryStash:
    """One page of galleries, then one page of images per gallery, then empties."""

    def __init__(self, gallery, images):
        self._gallery = gallery
        self._images = images
        self._gal_served = False
        self._img_served = False

    def query(self, query: str, variables=None) -> dict:
        if "findGalleries" in query:
            if self._gal_served:
                return {"findGalleries": {"count": 1, "galleries": []}}
            self._gal_served = True
            return {"findGalleries": {"count": 1, "galleries": [self._gallery]}}
        if self._img_served:
            return {"findImages": {"count": len(self._images), "images": []}}
        self._img_served = True
        return {"findImages": {"count": len(self._images), "images": self._images}}


def test_deep_gallery_captures_name_folder_above_leaf(tmp_path):
    # Stash makes a gallery at the leaf folder (.../P/instagram); the images sit several levels
    # below the subject folder. Every ancestor up to TOP_FOLDER gets a candidate.
    db = Database(str(tmp_path / "h.sqlite"))
    root = "/data/pics/exposed/ncaa"
    leaf = f"{root}/Basketball/Blair Green (UKY)/P/instagram"
    stash = _GalleryStash(
        {"id": "g1", "title": None, "cover": None, "folder": {"id": "f1", "path": leaf},
         "files": []},
        [{"id": "i1", "visual_files": [{"path": f"{leaf}/blairgreen_001.jpg"}]}],
    )
    harvest_galleries(db, stash, path_prefix=root)
    names = {n["name"]: n["disambiguation"] for n in db.list_names()}
    assert names.get("Blair Green") == "UKY"  # subject folder captured despite nesting
    assert "instagram" in names  # leaf still present (triaged away)
    db.close()
