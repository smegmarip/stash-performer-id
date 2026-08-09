"""The name database (DESIGN §5), owned by the service.

Five tables: asset, asset_relationship, name_candidate (raw harvest) and names,
name_relationship (deduplicated / triaged / activated). SQLite, WAL, idempotent schema.
"""

import os
import sqlite3
from datetime import UTC, datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS asset (
    id                INTEGER PRIMARY KEY,
    resource_type     TEXT NOT NULL,          -- file | folder | gallery
    stash_entity_type TEXT,                    -- gallery | image | scene | folder | NULL
    stash_id          TEXT,                    -- Stash entity id (NULL for non-entity folders)
    path              TEXT,
    basename          TEXT,
    evaluated_at      TEXT NOT NULL
);
-- Identity key: a Stash entity (type+id) or, lacking one, its path.
CREATE UNIQUE INDEX IF NOT EXISTS ux_asset_identity ON asset(
    resource_type,
    COALESCE(stash_entity_type, ''),
    COALESCE(stash_id, ''),
    COALESCE(path, '')
);

CREATE TABLE IF NOT EXISTS asset_relationship (
    id              INTEGER PRIMARY KEY,
    parent_asset_id INTEGER NOT NULL REFERENCES asset(id) ON DELETE CASCADE,
    child_asset_id  INTEGER NOT NULL REFERENCES asset(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL,             -- file_folder | file_gallery | gallery_folder
    UNIQUE(parent_asset_id, child_asset_id, kind)
);
CREATE INDEX IF NOT EXISTS ix_rel_parent ON asset_relationship(parent_asset_id);
CREATE INDEX IF NOT EXISTS ix_rel_child  ON asset_relationship(child_asset_id);

CREATE TABLE IF NOT EXISTS name_candidate (
    id             INTEGER PRIMARY KEY,
    asset_id       INTEGER NOT NULL REFERENCES asset(id) ON DELETE CASCADE,
    name           TEXT NOT NULL,             -- gated candidate string
    original_name  TEXT,                      -- raw source string, preserved
    disambiguation TEXT,
    source         TEXT NOT NULL,             -- gallery | folder | file | direct
    evaluation_time TEXT NOT NULL,
    UNIQUE(asset_id, source, name)
);
CREATE INDEX IF NOT EXISTS ix_candidate_name ON name_candidate(name);
CREATE INDEX IF NOT EXISTS ix_candidate_asset ON name_candidate(asset_id);

CREATE TABLE IF NOT EXISTS names (
    id             INTEGER PRIMARY KEY,
    name           TEXT NOT NULL,
    disambiguation TEXT NOT NULL DEFAULT '',
    valid          INTEGER NOT NULL DEFAULT 1, -- valid by default; triage invalidates the junk
    edited_by      TEXT,
    edited_at      TEXT,
    UNIQUE(name, disambiguation)
);

CREATE TABLE IF NOT EXISTS name_relationship (
    id             INTEGER PRIMARY KEY,
    name_id        INTEGER NOT NULL REFERENCES names(id) ON DELETE CASCADE,
    asset_id       INTEGER NOT NULL REFERENCES asset(id) ON DELETE CASCADE,
    active         INTEGER NOT NULL DEFAULT 1,
    source_level   TEXT NOT NULL,             -- gallery | folder | file | direct
    origin_asset_id INTEGER REFERENCES asset(id) ON DELETE SET NULL,
    created_at     TEXT NOT NULL
);
-- One active name per asset (DESIGN §5).
CREATE UNIQUE INDEX IF NOT EXISTS ux_active_name_per_asset
    ON name_relationship(asset_id) WHERE active = 1;
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


_UNSET = object()  # sentinel for "argument not provided" in partial updates


class Database:
    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        # check_same_thread=False: FastAPI runs sync endpoints in a threadpool. SQLite's
        # default serialized mode makes a shared connection safe for our low-concurrency use.
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.init_schema()

    def init_schema(self) -> None:
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # --- harvest writes ---

    def upsert_asset(
        self,
        resource_type: str,
        *,
        stash_entity_type: str | None = None,
        stash_id: str | None = None,
        path: str | None = None,
        basename: str | None = None,
    ) -> int:
        cur = self.conn.execute(
            """SELECT id FROM asset
               WHERE resource_type = ?
                 AND COALESCE(stash_entity_type,'') = COALESCE(?,'')
                 AND COALESCE(stash_id,'') = COALESCE(?,'')
                 AND COALESCE(path,'') = COALESCE(?,'')""",
            (resource_type, stash_entity_type, stash_id, path),
        )
        row = cur.fetchone()
        if row:
            return row["id"]
        cur = self.conn.execute(
            "INSERT INTO asset(resource_type, stash_entity_type, stash_id, path, basename,"
            " evaluated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (resource_type, stash_entity_type, stash_id, path, basename, _now()),
        )
        return cur.lastrowid

    def add_relationship(self, parent_asset_id: int, child_asset_id: int, kind: str) -> None:
        self.conn.execute(
            """INSERT OR IGNORE INTO asset_relationship(parent_asset_id, child_asset_id, kind)
               VALUES (?, ?, ?)""",
            (parent_asset_id, child_asset_id, kind),
        )

    def add_candidate(
        self, asset_id: int, name: str, source: str, original_name: str | None = None
    ) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO name_candidate(asset_id, name, original_name, source,"
            " evaluation_time) VALUES (?, ?, ?, ?, ?)",
            (asset_id, name, original_name, source, _now()),
        )

    def commit(self) -> None:
        self.conn.commit()

    # --- dedup / triage projection ---

    def rebuild_names(self) -> int:
        """Project distinct candidate names into `names` (untriaged). Preserves existing rows.

        Returns the number of newly-inserted names.
        """
        # valid defaults to 1 (valid-by-default; triage invalidates the junk).
        cur = self.conn.execute(
            """INSERT OR IGNORE INTO names(name, disambiguation)
               SELECT DISTINCT name, '' FROM name_candidate"""
        )
        self.conn.commit()
        return cur.rowcount

    # --- reads (audit + name-DB API) ---

    def summary(self) -> dict:
        c = self.conn.execute
        by_source = {
            r["source"]: r["n"]
            for r in c("SELECT source, COUNT(*) n FROM name_candidate GROUP BY source")
        }
        return {
            "assets": c("SELECT COUNT(*) n FROM asset").fetchone()["n"],
            "relationships": c("SELECT COUNT(*) n FROM asset_relationship").fetchone()["n"],
            "candidates": c("SELECT COUNT(*) n FROM name_candidate").fetchone()["n"],
            "candidates_by_source": by_source,
            "distinct_names": c("SELECT COUNT(*) n FROM names").fetchone()["n"],
        }

    def sample_names(self, limit: int = 25) -> list[str]:
        return [
            r["name"]
            for r in self.conn.execute(
                "SELECT name FROM names ORDER BY name LIMIT ?", (limit,)
            )
        ]

    # --- name-DB API (viewer + tagger) ---

    _NAME_COLS = "id, name, disambiguation, valid, edited_at"
    _STATUS_WHERE = {
        "valid": "WHERE valid = 1",
        "invalid": "WHERE valid = 0",
    }

    def list_names(
        self, status: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[dict]:
        where = self._STATUS_WHERE.get(status, "")
        rows = self.conn.execute(
            f"SELECT {self._NAME_COLS} FROM names {where} ORDER BY name LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_name(self, name_id: int) -> dict | None:
        r = self.conn.execute(
            f"SELECT {self._NAME_COLS} FROM names WHERE id = ?", (name_id,)
        ).fetchone()
        return dict(r) if r else None

    def update_name(
        self,
        name_id: int,
        *,
        valid: object = _UNSET,
        name: object = _UNSET,
        disambiguation: object = _UNSET,
        edited_by: str | None = None,
    ) -> dict | None:
        """Partial update (triage). Unpassed fields (left as _UNSET) are unchanged."""
        sets: list[str] = []
        params: list = []
        if valid is not _UNSET:
            sets.append("valid = ?")
            params.append(int(bool(valid)))
        if name is not _UNSET:
            sets.append("name = ?")
            params.append(name)
        if disambiguation is not _UNSET:
            sets.append("disambiguation = ?")
            params.append(disambiguation)
        sets.append("edited_by = ?")
        params.append(edited_by)
        sets.append("edited_at = ?")
        params.append(_now())
        params.append(name_id)
        cur = self.conn.execute(
            f"UPDATE names SET {', '.join(sets)} WHERE id = ?", params
        )
        self.conn.commit()
        return self.get_name(name_id) if cur.rowcount else None

    def set_valid_bulk(self, ids: list[int], valid: bool) -> int:
        """Batch valid/invalid over many names. Returns the number of rows updated."""
        if not ids:
            return 0
        placeholders = ",".join("?" * len(ids))
        cur = self.conn.execute(
            f"UPDATE names SET valid = ?, edited_at = ? WHERE id IN ({placeholders})",
            [int(bool(valid)), _now(), *ids],
        )
        self.conn.commit()
        return cur.rowcount

    # --- activation (name -> asset), Step 1 (DESIGN §3) ---

    def list_gallery_assets(self, limit: int = 500, offset: int = 0) -> list[dict]:
        """Galleries with their candidate names (own + folder) and the active assignment."""
        galleries = self.conn.execute(
            "SELECT id, stash_id, path, basename FROM asset WHERE resource_type = 'gallery'"
            " ORDER BY path LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        out = []
        for g in galleries:
            cands = self.conn.execute(
                """SELECT DISTINCT n.id AS name_id, n.name, n.valid
                   FROM name_candidate nc
                   JOIN names n ON n.name = nc.name
                   WHERE nc.asset_id = ?
                      OR nc.asset_id IN (
                         SELECT child_asset_id FROM asset_relationship
                         WHERE parent_asset_id = ? AND kind = 'gallery_folder')
                   ORDER BY n.valid DESC, n.name""",
                (g["id"], g["id"]),
            ).fetchall()
            active = self.conn.execute(
                """SELECT nr.name_id, n.name FROM name_relationship nr
                   JOIN names n ON n.id = nr.name_id
                   WHERE nr.asset_id = ? AND nr.active = 1""",
                (g["id"],),
            ).fetchone()
            out.append(
                {
                    "asset_id": g["id"],
                    "stash_id": g["stash_id"],
                    "path": g["path"],
                    "basename": g["basename"],
                    "candidates": [dict(c) for c in cands],
                    "active": dict(active) if active else None,
                }
            )
        return out

    def activate_name(
        self, asset_id: int, name_id: int, source_level: str, origin_asset_id: int | None = None
    ) -> None:
        """Set the single active name for an asset (replaces any existing active)."""
        self.conn.execute("DELETE FROM name_relationship WHERE asset_id = ?", (asset_id,))
        self.conn.execute(
            "INSERT INTO name_relationship(name_id, asset_id, active, source_level,"
            " origin_asset_id, created_at) VALUES (?, ?, 1, ?, ?, ?)",
            (name_id, asset_id, source_level, origin_asset_id, _now()),
        )
        self.conn.commit()

    def deactivate_asset(self, asset_id: int) -> None:
        self.conn.execute("DELETE FROM name_relationship WHERE asset_id = ?", (asset_id,))
        self.conn.commit()

    def add_direct_name(self, name: str, disambiguation: str = "") -> dict:
        """Direct-input name (marked valid)."""
        self.conn.execute(
            "INSERT OR IGNORE INTO names(name, disambiguation, valid, edited_at)"
            " VALUES (?, ?, 1, ?)",
            (name, disambiguation, _now()),
        )
        self.conn.commit()
        r = self.conn.execute(
            f"SELECT {self._NAME_COLS} FROM names WHERE name = ? AND disambiguation = ?",
            (name, disambiguation),
        ).fetchone()
        return dict(r)
