import sqlite3

import pytest

from bridge.app.cache.db import Database


@pytest.fixture
def db():
    d = Database(":memory:")
    yield d
    d.close()


def test_upsert_asset_is_idempotent(db):
    a = db.upsert_asset("gallery", stash_entity_type="gallery", stash_id="7", path="/x")
    b = db.upsert_asset("gallery", stash_entity_type="gallery", stash_id="7", path="/x")
    assert a == b
    assert db.summary()["assets"] == 1


def test_candidates_dedupe_into_names(db):
    a1 = db.upsert_asset("folder", path="/a")
    a2 = db.upsert_asset("folder", path="/b")
    db.add_candidate(a1, "Jane Doe", "folder")
    db.add_candidate(a2, "Jane Doe", "folder")  # same name, different asset
    db.add_candidate(a2, "John Smith", "folder")
    db.commit()
    new = db.rebuild_names()
    assert new == 2  # Jane Doe, John Smith
    assert set(db.sample_names()) == {"Jane Doe", "John Smith"}
    # Idempotent: re-running adds nothing.
    assert db.rebuild_names() == 0


def test_active_name_unique_per_asset(db):
    asset = db.upsert_asset("gallery", stash_id="1", stash_entity_type="gallery", path="/g")
    db.add_candidate(asset, "A", "gallery")
    db.rebuild_names()
    n1 = db.add_direct_name("A")["id"]
    n2 = db.add_direct_name("B")["id"]
    now = "2026-01-01T00:00:00Z"
    db.conn.execute(
        "INSERT INTO name_relationship(name_id, asset_id, active, source_level, created_at)"
        " VALUES (?, ?, 1, 'gallery', ?)",
        (n1, asset, now),
    )
    db.conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        db.conn.execute(
            "INSERT INTO name_relationship(name_id, asset_id, active, source_level, created_at)"
            " VALUES (?, ?, 1, 'gallery', ?)",
            (n2, asset, now),
        )
        db.conn.commit()


def test_names_are_valid_by_default(db):
    a = db.upsert_asset("folder", path="/a")
    db.add_candidate(a, "Jane Doe", "folder")
    db.commit()
    db.rebuild_names()
    assert db.list_names(status="valid") != []
    assert db.list_names(status="invalid") == []


def test_update_name_partial(db):
    nid = db.add_direct_name("Temp")["id"]
    row = db.update_name(nid, valid=False)
    assert row["valid"] == 0
    # Only disambiguation changes; valid stays.
    row = db.update_name(nid, disambiguation="actor")
    assert row["valid"] == 0
    assert row["disambiguation"] == "actor"
    assert db.update_name(9999, valid=True) is None


def test_set_valid_bulk(db):
    ids = [db.add_direct_name(n)["id"] for n in ("A", "B", "C")]
    assert db.set_valid_bulk(ids[:2], False) == 2
    assert {r["name"] for r in db.list_names(status="invalid")} == {"A", "B"}
    assert {r["name"] for r in db.list_names(status="valid")} == {"C"}
    assert db.set_valid_bulk([], False) == 0  # no-op on empty
    assert db.set_valid_bulk(ids, True) == 3  # restore all
    assert db.list_names(status="invalid") == []
