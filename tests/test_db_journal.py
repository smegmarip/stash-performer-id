import sqlite3

from bridge.app.cache import db as db_mod
from bridge.app.cache.db import Database


def test_wal_used_on_normal_fs(tmp_path):
    db = Database(str(tmp_path / "n.sqlite"))
    assert db.conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    db.close()


class _NoWalConn:
    """Wraps a real connection but rejects the WAL pragma, mimicking a FUSE/shfs share."""

    def __init__(self, real):
        object.__setattr__(self, "_real", real)

    def execute(self, sql, *args):
        if "journal_mode=WAL" in sql:
            raise sqlite3.OperationalError("database is locked")
        return self._real.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._real, name)

    def __setattr__(self, name, value):  # forward e.g. row_factory to the real connection
        setattr(self._real, name, value)


def test_falls_back_when_wal_rejected(tmp_path, monkeypatch):
    real_connect = sqlite3.connect
    monkeypatch.setattr(
        db_mod.sqlite3, "connect", lambda *a, **k: _NoWalConn(real_connect(*a, **k))
    )
    # Must construct without raising, and be usable (schema created, queries work).
    db = Database(str(tmp_path / "shfs.sqlite"))
    assert db.conn.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal"
    db.add_direct_name("Samantha Fox")  # a real write must succeed on the fallback journal
    assert db.summary()["distinct_names"] == 1
    db.close()
