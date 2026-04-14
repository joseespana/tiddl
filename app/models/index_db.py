"""
IndexDB — SQLite-backed index of downloaded resources and disk scan cache.

Replaces the previous JSON-based ``.tiddl_index.json`` file with a
concurrent-safe SQLite database stored as ``.tiddl_index.db`` inside the
download directory. Also stores an mtime-based filesystem scan cache so
the ``DiskCache`` can avoid re-walking unchanged folders on each launch.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Iterable, Optional

log = logging.getLogger(__name__)

DB_FILENAME = ".tiddl_index.db"
LEGACY_JSON_FILENAME = ".tiddl_index.json"

_TIDAL_URL_RE = re.compile(r'(playlist|album|artist|track)/([a-zA-Z0-9\-]+)')

_SCHEMA = """
CREATE TABLE IF NOT EXISTS downloaded_items (
    kind TEXT NOT NULL,
    id   TEXT NOT NULL,
    url  TEXT,
    recorded_at INTEGER NOT NULL,
    PRIMARY KEY (kind, id)
);
CREATE INDEX IF NOT EXISTS idx_downloaded_kind ON downloaded_items(kind);

CREATE TABLE IF NOT EXISTS disk_scan_cache (
    path       TEXT PRIMARY KEY,
    mtime_ns   INTEGER NOT NULL,
    artist     TEXT,
    album      TEXT,
    has_audio  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS scan_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


class IndexDB:
    """SQLite-backed index of downloaded items and on-disk scan cache.

    One persistent connection per instance, guarded by an :class:`RLock`
    so that multiple download-worker threads can call
    :meth:`add_downloaded` concurrently without corrupting writes.
    """

    def __init__(self, download_path: str) -> None:
        """Open (or create) the database inside *download_path*.

        Also performs a one-shot migration from any legacy
        ``.tiddl_index.json`` sitting next to it.

        Args:
            download_path: Absolute path to the download directory.
        """
        self._download_path = download_path
        self._db_path = Path(download_path) / DB_FILENAME
        self._lock = threading.RLock()

        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        # ``check_same_thread=False`` + our own RLock lets worker threads
        # share the single connection safely.
        self._conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,
            isolation_level=None,  # autocommit; we manage transactions
        )
        self._conn.row_factory = sqlite3.Row
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        except sqlite3.DatabaseError as exc:
            log.warning("Failed to apply PRAGMAs on %s: %s", self._db_path, exc)

        with self._lock:
            self._conn.executescript(_SCHEMA)

        self._maybe_migrate_legacy_json()

    # ------------------------------------------------------------------
    # Migration
    # ------------------------------------------------------------------
    def _maybe_migrate_legacy_json(self) -> None:
        """Import a legacy ``.tiddl_index.json`` on first run.

        Only runs when the SQLite DB has never been populated (tracked
        via ``scan_meta['legacy_migrated']``). Leaves the JSON file in
        place so users can roll back.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM scan_meta WHERE key = 'legacy_migrated'"
            ).fetchone()
            if row is not None:
                return

            legacy = Path(self._download_path) / LEGACY_JSON_FILENAME
            if not legacy.exists():
                self._conn.execute(
                    "INSERT OR REPLACE INTO scan_meta(key, value) VALUES "
                    "('legacy_migrated', '1')"
                )
                return

            try:
                data = json.loads(legacy.read_text())
            except Exception as exc:
                log.warning("Failed to read legacy %s: %s", legacy, exc)
                self._conn.execute(
                    "INSERT OR REPLACE INTO scan_meta(key, value) VALUES "
                    "('legacy_migrated', '1')"
                )
                return

            now = int(time.time())
            urls: list[str] = list(data.get("urls", []))
            # Map url -> (kind, id)
            url_by_key: dict[tuple[str, str], str] = {}
            for url in urls:
                m = _TIDAL_URL_RE.search(url)
                if m:
                    url_by_key[(m.group(1), m.group(2))] = url

            rows: list[tuple[str, str, Optional[str], int]] = []
            for kind in ("playlist", "album", "artist", "track"):
                for rid in data.get(kind, []) or []:
                    rid_str = str(rid)
                    url = url_by_key.get((kind, rid_str))
                    rows.append((kind, rid_str, url, now))
            # Also absorb any URL whose id wasn't in per-kind buckets
            for (kind, rid), url in url_by_key.items():
                rows.append((kind, rid, url, now))

            try:
                self._conn.execute("BEGIN")
                self._conn.executemany(
                    "INSERT OR IGNORE INTO downloaded_items "
                    "(kind, id, url, recorded_at) VALUES (?, ?, ?, ?)",
                    rows,
                )
                self._conn.execute(
                    "INSERT OR REPLACE INTO scan_meta(key, value) VALUES "
                    "('legacy_migrated', '1')"
                )
                self._conn.execute("COMMIT")
                log.info(
                    "Migrated %d rows from legacy %s to %s",
                    len(rows), LEGACY_JSON_FILENAME, DB_FILENAME,
                )
            except sqlite3.DatabaseError as exc:
                log.warning("Legacy migration failed: %s", exc)
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.DatabaseError:
                    pass

    # ------------------------------------------------------------------
    # Downloaded items API
    # ------------------------------------------------------------------
    def add_downloaded(
        self, kind: str, id: str, url: Optional[str] = None,
    ) -> None:
        """Record that *(kind, id)* has been downloaded.

        O(1) single ``INSERT OR IGNORE``.

        Args:
            kind: One of ``"playlist"``, ``"album"``, ``"artist"``,
                ``"track"``.
            id: The Tidal resource id (UUID for playlists, numeric id
                otherwise).
            url: Optional full Tidal URL for reconstruction later.
        """
        if not kind or not id:
            return
        now = int(time.time())
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT OR IGNORE INTO downloaded_items "
                    "(kind, id, url, recorded_at) VALUES (?, ?, ?, ?)",
                    (kind, str(id), url, now),
                )
                # Backfill url if the row existed without one.
                if url:
                    self._conn.execute(
                        "UPDATE downloaded_items SET url = ? "
                        "WHERE kind = ? AND id = ? AND (url IS NULL OR url = '')",
                        (url, kind, str(id)),
                    )
            except sqlite3.DatabaseError as exc:
                log.warning(
                    "add_downloaded(%s, %s) failed: %s", kind, id, exc,
                )

    def contains(self, kind: str, id: str) -> bool:
        """Return ``True`` when *(kind, id)* has been recorded.

        Args:
            kind: Resource kind.
            id: Resource id.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM downloaded_items WHERE kind = ? AND id = ? LIMIT 1",
                (kind, str(id)),
            ).fetchone()
        return row is not None

    def list_urls(self) -> list[str]:
        """Return every non-empty URL recorded, in insertion order."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT url FROM downloaded_items "
                "WHERE url IS NOT NULL AND url != '' "
                "ORDER BY recorded_at ASC, kind ASC, id ASC"
            ).fetchall()
        return [r[0] for r in rows]

    def get_ids(self, kind: str) -> set[str]:
        """Return the set of recorded ids for *kind*."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM downloaded_items WHERE kind = ?",
                (kind,),
            ).fetchall()
        return {r[0] for r in rows}

    # ------------------------------------------------------------------
    # Disk scan cache API
    # ------------------------------------------------------------------
    def get_scan_rows(self) -> list[sqlite3.Row]:
        """Return every row of the disk scan cache."""
        with self._lock:
            return list(
                self._conn.execute(
                    "SELECT path, mtime_ns, artist, album, has_audio "
                    "FROM disk_scan_cache"
                ).fetchall()
            )

    def get_scan_mtimes(self) -> dict[str, int]:
        """Return a ``{path: mtime_ns}`` map for incremental scans."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT path, mtime_ns FROM disk_scan_cache"
            ).fetchall()
        return {r[0]: r[1] for r in rows}

    def upsert_scan_rows(
        self,
        rows: Iterable[tuple[str, int, Optional[str], Optional[str], bool]],
    ) -> None:
        """Insert or replace multiple scan-cache rows in one transaction.

        Args:
            rows: Iterable of ``(path, mtime_ns, artist, album, has_audio)``.
        """
        batch = [
            (p, mt, ar, al, 1 if has else 0)
            for (p, mt, ar, al, has) in rows
        ]
        if not batch:
            return
        with self._lock:
            try:
                self._conn.execute("BEGIN")
                self._conn.executemany(
                    "INSERT OR REPLACE INTO disk_scan_cache "
                    "(path, mtime_ns, artist, album, has_audio) "
                    "VALUES (?, ?, ?, ?, ?)",
                    batch,
                )
                self._conn.execute("COMMIT")
            except sqlite3.DatabaseError as exc:
                log.warning("upsert_scan_rows failed: %s", exc)
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.DatabaseError:
                    pass

    def delete_scan_paths(self, paths: Iterable[str]) -> None:
        """Remove scan-cache rows whose path is in *paths*."""
        batch = [(p,) for p in paths]
        if not batch:
            return
        with self._lock:
            try:
                self._conn.execute("BEGIN")
                self._conn.executemany(
                    "DELETE FROM disk_scan_cache WHERE path = ?", batch,
                )
                self._conn.execute("COMMIT")
            except sqlite3.DatabaseError as exc:
                log.warning("delete_scan_paths failed: %s", exc)
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.DatabaseError:
                    pass

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def close(self) -> None:
        """Close the underlying SQLite connection."""
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.DatabaseError:
                pass

    def __enter__(self) -> "IndexDB":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
