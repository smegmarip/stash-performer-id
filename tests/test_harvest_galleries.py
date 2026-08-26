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
