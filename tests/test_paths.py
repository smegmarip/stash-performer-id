from bridge.app.cache.db import Database
from bridge.app.harvest.paths import harvest_path


def _touch(p):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")


def test_named_folder_vs_filename_fallback(tmp_path):
    # Named folder -> folder candidate; its files are NOT harvested (they inherit the name).
    _touch(tmp_path / "Jane Doe" / "001.jpg")
    _touch(tmp_path / "Jane Doe" / "002.jpg")
    # Unnamed (date) folder -> fall back to image filenames.
    _touch(tmp_path / "2024-01-15" / "John Smith.jpg")
    _touch(tmp_path / "2024-01-15" / "0001.jpg")  # digits-only -> no candidate

    db = Database(":memory:")
    result = harvest_path(db, str(tmp_path))
    names = set(db.sample_names(100))
    db.close()

    assert "Jane Doe" in names          # from folder name
    assert "John Smith" in names        # from filename fallback
    assert "001" not in names           # named folder's files skipped
    assert "0001" not in names          # digits-only filename gated out
    assert result["folders"] == 1
    assert result["files"] == 1


def test_non_image_files_ignored(tmp_path):
    _touch(tmp_path / "2020" / "Notes Reilly.txt")  # .txt ignored
    db = Database(":memory:")
    result = harvest_path(db, str(tmp_path))
    db.close()
    assert result["files"] == 0
