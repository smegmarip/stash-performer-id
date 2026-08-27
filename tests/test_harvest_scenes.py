from bridge.app.cache.db import Database
from bridge.app.harvest.scenes import harvest_scenes


class _RecordingStash:
    """Records the variables of each query and returns empty result pages."""

    def __init__(self):
        self.calls = []

    def query(self, query: str, variables=None) -> dict:
        self.calls.append(variables or {})
        return {"findScenes": {"count": 0, "scenes": []}}


class _OnePageStash:
    """Returns a single page of scenes, then empties."""

    def __init__(self, scenes):
        self._scenes = scenes
        self._served = False

    def query(self, query: str, variables=None) -> dict:
        if self._served:
            return {"findScenes": {"count": len(self._scenes), "scenes": []}}
        self._served = True
        return {"findScenes": {"count": len(self._scenes), "scenes": self._scenes}}


def test_top_folder_scopes_scene_query(tmp_path):
    db = Database(str(tmp_path / "h.sqlite"))
    stash = _RecordingStash()
    harvest_scenes(db, stash, path_prefix="/data/vids/celeb")
    sf = stash.calls[0]["sf"]
    assert sf == {"path": {"value": "/data/vids/celeb", "modifier": "INCLUDES"}}
    db.close()


def test_no_top_folder_harvests_whole_library(tmp_path):
    db = Database(str(tmp_path / "h.sqlite"))
    stash = _RecordingStash()
    harvest_scenes(db, stash, path_prefix=None)
    assert stash.calls[0]["sf"] is None
    db.close()


def test_scene_folder_candidate_cascades_to_scene(tmp_path):
    db = Database(str(tmp_path / "h.sqlite"))
    stash = _OnePageStash(
        [{"id": "s1", "title": "Some Clip", "files": [{"path": "/lib/Jane Doe/clip.mp4"}]}]
    )
    result = harvest_scenes(db, stash, path_prefix=None)
    assert result["scenes"] == 1

    # The parent-folder name became a candidate → name; activating the folder cascades to the scene,
    # so /scrape/scene resolution (lookup_active_name for entity_type='scene') can find it.
    assert any(n["name"] == "Jane Doe" for n in db.list_names())
    nid = next(n["id"] for n in db.list_names() if n["name"] == "Jane Doe")
    folder = db.conn.execute(
        "SELECT id FROM asset WHERE resource_type='folder' AND path='/lib/Jane Doe'"
    ).fetchone()["id"]
    db.activate_name(folder, nid, "folder")
    active = db.lookup_active_name(stash_id="s1", entity_type="scene")
    assert active and active["name"] == "Jane Doe"
    db.close()
