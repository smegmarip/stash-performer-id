"""The name database (DESIGN §5), owned by the service.

Five tables: asset, asset_relationship, name_candidate (raw harvest) and names,
name_relationship (deduplicated / triaged / activated). SQLite, WAL, idempotent schema.
"""

import json
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
    thumb_stash_id    TEXT,                    -- Stash image id whose thumbnail represents it
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

-- Enrichment (docs/ENRICHMENT.md): a cache tier (every candidate from every search) plus a
-- resolved profile per name, all FK'd to names.
CREATE TABLE IF NOT EXISTS enrichment_search (
    id           INTEGER PRIMARY KEY,
    name_id      INTEGER NOT NULL REFERENCES names(id) ON DELETE CASCADE,
    source       TEXT NOT NULL,
    query        TEXT,
    result_count INTEGER NOT NULL DEFAULT 0,
    error        TEXT,
    searched_at  TEXT NOT NULL,
    UNIQUE(name_id, source)          -- one cache marker per (name, source)
);

CREATE TABLE IF NOT EXISTS enrichment_candidate (
    id               INTEGER PRIMARY KEY,
    name_id          INTEGER NOT NULL REFERENCES names(id) ON DELETE CASCADE,
    source           TEXT NOT NULL,
    source_entity_id TEXT NOT NULL,
    data             TEXT NOT NULL,   -- JSON PerformerData
    score            REAL,
    fetched_at       TEXT NOT NULL,
    UNIQUE(name_id, source, source_entity_id)
);
CREATE INDEX IF NOT EXISTS ix_enrich_cand ON enrichment_candidate(name_id, source);

CREATE TABLE IF NOT EXISTS enrichment_profile (
    name_id INTEGER PRIMARY KEY REFERENCES names(id) ON DELETE CASCADE,
    name TEXT, disambiguation TEXT, aliases TEXT, gender TEXT, birthdate TEXT,
    death_date TEXT, ethnicity TEXT, country TEXT, hair_color TEXT, eye_color TEXT,
    height TEXT, weight TEXT, measurements TEXT, fake_tits TEXT, penis_length TEXT,
    circumcised TEXT, career_start TEXT, career_end TEXT, tattoos TEXT, piercings TEXT,
    details TEXT, urls TEXT, images TEXT,
    field_sources TEXT,              -- {field: source} provenance
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS enrichment_credit_ledger (
    id      INTEGER PRIMARY KEY,
    source  TEXT NOT NULL,
    cost    INTEGER NOT NULL,
    name_id INTEGER,
    at      TEXT NOT NULL
);
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
        self.conn = sqlite3.connect(path, check_same_thread=False, timeout=30.0)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout=30000")
        # WAL improves multi-connection concurrency but needs an mmap'd -shm file and POSIX
        # locking that FUSE user shares (Unraid /mnt/user shfs) don't provide — there the pragma
        # raises "database is locked". We run one process with a single shared connection, so WAL
        # isn't required for correctness; fall back to the default rollback journal if it's
        # rejected so the DB works on network/FUSE filesystems too.
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            pass
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.init_schema()

    def init_schema(self) -> None:
        self.conn.executescript(SCHEMA)
        # Lightweight migration for DBs created before thumb_stash_id existed.
        try:
            self.conn.execute("ALTER TABLE asset ADD COLUMN thumb_stash_id TEXT")
        except sqlite3.OperationalError:
            pass  # column already present
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
        thumb_stash_id: str | None = None,
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
            " thumb_stash_id, evaluated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (resource_type, stash_entity_type, stash_id, path, basename, thumb_stash_id, _now()),
        )
        return cur.lastrowid

    def set_thumb_if_null(self, asset_id: int, thumb_stash_id: str) -> None:
        """Backfill an asset's thumbnail image id (first image wins; cover already set wins)."""
        self.conn.execute(
            "UPDATE asset SET thumb_stash_id = ? WHERE id = ? AND thumb_stash_id IS NULL",
            (thumb_stash_id, asset_id),
        )

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
    _NAME_SORT = {"name": "name", "edited": "edited_at", "id": "id"}

    @staticmethod
    def _name_where(status: str | None, q: str | None) -> tuple[str, list]:
        where: list[str] = []
        params: list = []
        if status == "valid":
            where.append("valid = 1")
        elif status == "invalid":
            where.append("valid = 0")
        if q:
            where.append("name LIKE ?")
            params.append(f"%{q}%")
        return ("WHERE " + " AND ".join(where)) if where else "", params

    def count_names(self, status: str | None = None, q: str | None = None) -> int:
        where, params = self._name_where(status, q)
        return self.conn.execute(f"SELECT COUNT(*) n FROM names {where}", params).fetchone()["n"]

    def list_names(
        self,
        status: str | None = None,
        q: str | None = None,
        sort: str = "name",
        order: str = "asc",
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        where, params = self._name_where(status, q)
        sort_col = self._NAME_SORT.get(sort, "name")
        order_sql = "DESC" if order.lower() == "desc" else "ASC"
        rows = self.conn.execute(
            f"SELECT {self._NAME_COLS} FROM names {where}"
            f" ORDER BY {sort_col} {order_sql} LIMIT ? OFFSET ?",
            [*params, limit, offset],
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

    # A scope's cascade reaches its member images via this relationship kind.
    _CASCADE_KIND = {"gallery": "gallery_image", "folder": "folder_image"}

    _ASSET_SORT = {"path": "path", "name": "basename", "id": "id"}

    @staticmethod
    def _asset_where(resource_type: str, q: str | None, assigned: str | None) -> tuple[str, list]:
        where = ["resource_type = ?"]
        params: list = [resource_type]
        if q:
            where.append("(basename LIKE ? OR path LIKE ?)")
            params += [f"%{q}%", f"%{q}%"]
        if assigned in ("assigned", "unassigned"):
            neg = "NOT " if assigned == "unassigned" else ""
            where.append(
                f"{neg}EXISTS (SELECT 1 FROM name_relationship nr"
                " WHERE nr.asset_id = asset.id AND nr.active = 1)"
            )
        return " AND ".join(where), params

    def count_assets(
        self, resource_type: str, q: str | None = None, assigned: str | None = None
    ) -> int:
        where, params = self._asset_where(resource_type, q, assigned)
        return self.conn.execute(
            f"SELECT COUNT(*) n FROM asset WHERE {where}", params
        ).fetchone()["n"]

    def list_assets(
        self,
        resource_type: str,
        q: str | None = None,
        sort: str = "path",
        order: str = "asc",
        assigned: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """Assets of a scope (gallery/folder/file) with the active assignment (incl. the
        source_level that set it) and member-image count. Names are assigned from the
        authoritative `names` bank, so no per-asset candidates are returned."""
        where, params = self._asset_where(resource_type, q, assigned)
        sort_col = self._ASSET_SORT.get(sort, "path")
        order_sql = "DESC" if order.lower() == "desc" else "ASC"
        assets = self.conn.execute(
            f"SELECT id, stash_id, path, basename, thumb_stash_id FROM asset WHERE {where}"
            f" ORDER BY {sort_col} {order_sql} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        cascade_kind = self._CASCADE_KIND.get(resource_type)
        out = []
        for a in assets:
            active = self.conn.execute(
                """SELECT nr.name_id, n.name, nr.source_level FROM name_relationship nr
                   JOIN names n ON n.id = nr.name_id
                   WHERE nr.asset_id = ? AND nr.active = 1""",
                (a["id"],),
            ).fetchone()
            child_count = 0
            if cascade_kind:
                child_count = self.conn.execute(
                    "SELECT COUNT(*) n FROM asset_relationship"
                    " WHERE parent_asset_id = ? AND kind = ?",
                    (a["id"], cascade_kind),
                ).fetchone()["n"]
            out.append(
                {
                    "asset_id": a["id"],
                    "stash_id": a["stash_id"],
                    "path": a["path"],
                    "basename": a["basename"],
                    "thumb_stash_id": a["thumb_stash_id"],
                    "resource_type": resource_type,
                    "child_count": child_count,
                    "active": dict(active) if active else None,
                }
            )
        return out

    def _cascade_targets(self, asset_id: int) -> list[int]:
        """The asset itself plus, for a gallery/folder, its member images (cascade set).

        A file cascades to only itself.
        """
        targets = [asset_id]
        row = self.conn.execute(
            "SELECT resource_type FROM asset WHERE id = ?", (asset_id,)
        ).fetchone()
        kind = self._CASCADE_KIND.get(row["resource_type"]) if row else None
        if kind:
            targets += [
                r["child_asset_id"]
                for r in self.conn.execute(
                    "SELECT child_asset_id FROM asset_relationship"
                    " WHERE parent_asset_id = ? AND kind = ?",
                    (asset_id, kind),
                )
            ]
        return targets

    def activate_name(
        self, asset_id: int, name_id: int, source_level: str, origin_asset_id: int | None = None
    ) -> int:
        """Set the active name for an asset, cascading onto its members. Returns rows affected."""
        origin = origin_asset_id if origin_asset_id is not None else asset_id
        targets = self._cascade_targets(asset_id)
        for t in targets:
            self.conn.execute("DELETE FROM name_relationship WHERE asset_id = ?", (t,))
            self.conn.execute(
                "INSERT INTO name_relationship(name_id, asset_id, active, source_level,"
                " origin_asset_id, created_at) VALUES (?, ?, 1, ?, ?, ?)",
                (name_id, t, source_level, origin, _now()),
            )
        self.conn.commit()
        return len(targets)

    def deactivate_asset(self, asset_id: int) -> int:
        targets = self._cascade_targets(asset_id)
        for t in targets:
            self.conn.execute("DELETE FROM name_relationship WHERE asset_id = ?", (t,))
        self.conn.commit()
        return len(targets)

    # --- scrape surface (Step 2: image -> performer, via the metadata provider) ---

    def lookup_active_name_for_image(
        self,
        stash_id: str | None = None,
        paths: list[str] | None = None,
    ) -> dict | None:
        """Resolve an image (by Stash image id, else by any of its file paths) to its active
        name, for the `imageByFragment` scraper. Returns {name_id, name, disambiguation} or None.

        The image id is the reliable key (an image file-asset's stash_id IS the Stash image id);
        paths are the fallback for assets harvested before an id was known.
        """
        asset_id = None
        if stash_id:
            row = self.conn.execute(
                "SELECT id FROM asset WHERE stash_entity_type = 'image' AND stash_id = ?",
                (stash_id,),
            ).fetchone()
            if row:
                asset_id = row["id"]
        if asset_id is None and paths:
            placeholders = ",".join("?" * len(paths))
            row = self.conn.execute(
                f"SELECT id FROM asset WHERE resource_type = 'file' AND path IN ({placeholders})"
                " LIMIT 1",
                paths,
            ).fetchone()
            if row:
                asset_id = row["id"]
        if asset_id is None:
            return None
        active = self.conn.execute(
            """SELECT nr.name_id, n.name, n.disambiguation FROM name_relationship nr
               JOIN names n ON n.id = nr.name_id
               WHERE nr.asset_id = ? AND nr.active = 1""",
            (asset_id,),
        ).fetchone()
        return dict(active) if active else None

    # --- enrichment (docs/ENRICHMENT.md) ---

    # The resolved-profile columns (superset of possible fields); list-valued ones are JSON.
    _PROFILE_COLS = (
        "name", "disambiguation", "aliases", "gender", "birthdate", "death_date",
        "ethnicity", "country", "hair_color", "eye_color", "height", "weight",
        "measurements", "fake_tits", "penis_length", "circumcised", "career_start",
        "career_end", "tattoos", "piercings", "details", "urls", "images",
    )
    _PROFILE_LIST_COLS = frozenset({"aliases", "urls", "images"})

    def has_enrichment_search(self, name_id: int, source: str) -> bool:
        """True if a (name, source) search has been run — the cache-first marker."""
        return (
            self.conn.execute(
                "SELECT 1 FROM enrichment_search WHERE name_id = ? AND source = ?",
                (name_id, source),
            ).fetchone()
            is not None
        )

    def search_status(self, name_ids: list[int], source: str) -> dict[int, dict]:
        """Bulk cache status for a page of names against a source: {name_id: {count, error}} for
        those already searched. Lets the UI show cached candidates without a live call per name."""
        if not name_ids:
            return {}
        placeholders = ",".join("?" * len(name_ids))
        rows = self.conn.execute(
            f"SELECT name_id, result_count, error FROM enrichment_search"
            f" WHERE source = ? AND name_id IN ({placeholders})",
            [source, *name_ids],
        ).fetchall()
        return {r["name_id"]: {"count": r["result_count"], "error": r["error"]} for r in rows}

    def record_enrichment_search(
        self, name_id: int, source: str, query: str, result_count: int, error: str | None = None
    ) -> None:
        self.conn.execute(
            "INSERT INTO enrichment_search(name_id, source, query, result_count, error,"
            " searched_at) VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(name_id, source) DO UPDATE SET"
            " query=excluded.query, result_count=excluded.result_count,"
            " error=excluded.error, searched_at=excluded.searched_at",
            (name_id, source, query, result_count, error, _now()),
        )
        self.conn.commit()

    def replace_candidates(self, name_id: int, source: str, candidates: list[dict]) -> None:
        """Replace the cached candidates for (name, source). `candidates` items:
        {source_entity_id, data: dict, score?}."""
        self.conn.execute(
            "DELETE FROM enrichment_candidate WHERE name_id = ? AND source = ?", (name_id, source)
        )
        for c in candidates:
            self.conn.execute(
                "INSERT INTO enrichment_candidate(name_id, source, source_entity_id, data, score,"
                " fetched_at) VALUES (?, ?, ?, ?, ?, ?)",
                (name_id, source, c["source_entity_id"], json.dumps(c["data"]),
                 c.get("score"), _now()),
            )
        self.conn.commit()

    def list_candidates(self, name_id: int, source: str | None = None) -> list[dict]:
        where = "name_id = ?"
        params: list = [name_id]
        if source:
            where += " AND source = ?"
            params.append(source)
        rows = self.conn.execute(
            f"SELECT source, source_entity_id, data, score FROM enrichment_candidate"
            f" WHERE {where} ORDER BY id",
            params,
        ).fetchall()
        return [
            {
                "source": r["source"],
                "source_entity_id": r["source_entity_id"],
                "score": r["score"],
                "data": json.loads(r["data"]),
            }
            for r in rows
        ]

    def get_enrichment_profile(self, name_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM enrichment_profile WHERE name_id = ?", (name_id,)
        ).fetchone()
        if not row:
            return None
        out = dict(row)
        for col in self._PROFILE_LIST_COLS:
            out[col] = json.loads(out[col]) if out.get(col) else []
        out["field_sources"] = json.loads(out["field_sources"]) if out.get("field_sources") else {}
        return out

    def apply_enrichment_profile(self, name_id: int, fields: dict) -> dict | None:
        """Write the given fields onto the resolved profile (override), recording provenance.

        `fields` maps column -> {"value": ..., "source": ...} (or a bare value). Only whitelisted,
        populated columns are written; the rest of the profile is untouched (docs §5.3).
        """
        self.conn.execute(
            "INSERT OR IGNORE INTO enrichment_profile(name_id, updated_at) VALUES (?, ?)",
            (name_id, _now()),
        )
        row = self.conn.execute(
            "SELECT field_sources FROM enrichment_profile WHERE name_id = ?", (name_id,)
        ).fetchone()
        sources = json.loads(row["field_sources"]) if row and row["field_sources"] else {}
        sets: list[str] = []
        params: list = []
        for col, spec in fields.items():
            if col not in self._PROFILE_COLS:
                continue
            value = spec.get("value") if isinstance(spec, dict) else spec
            src = spec.get("source") if isinstance(spec, dict) else None
            if col in self._PROFILE_LIST_COLS:
                value = json.dumps(value or [])
            sets.append(f"{col} = ?")
            params.append(value)
            if src:
                sources[col] = src
        sets.append("field_sources = ?")
        params.append(json.dumps(sources))
        sets.append("updated_at = ?")
        params.append(_now())
        params.append(name_id)
        self.conn.execute(
            f"UPDATE enrichment_profile SET {', '.join(sets)} WHERE name_id = ?", params
        )
        self.conn.commit()
        return self.get_enrichment_profile(name_id)

    def add_credit(self, source: str, cost: int, name_id: int | None = None) -> None:
        self.conn.execute(
            "INSERT INTO enrichment_credit_ledger(source, cost, name_id, at) VALUES (?, ?, ?, ?)",
            (source, cost, name_id, _now()),
        )
        self.conn.commit()

    def credits_spent(self, source: str) -> int:
        r = self.conn.execute(
            "SELECT COALESCE(SUM(cost), 0) n FROM enrichment_credit_ledger WHERE source = ?",
            (source,),
        ).fetchone()
        return r["n"]

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
