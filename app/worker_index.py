"""
Index helpers — thin compatibility layer over :mod:`app.models.index_db`.

The legacy JSON index (``.tiddl_index.json``) is superseded by the
SQLite-backed :class:`app.models.index_db.IndexDB`, which supports O(1)
inserts and thread-safe concurrent writes from download workers.

The public API (``record_downloaded``, ``_TIDAL_URL_RE``, ``load_index``,
``save_index``, ``INDEX_FILENAME``) is preserved so existing callers in
``app.workers``, ``app.worker_download`` and ``app.worker_downloaded``
keep working unchanged.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from app.models.index_db import IndexDB

_TIDAL_URL_RE = re.compile(r'(playlist|album|artist|track)/([a-zA-Z0-9\-]+)')

# Legacy JSON filename — still referenced for rollback; no new writes here.
INDEX_FILENAME = ".tiddl_index.json"


def load_index(download_path: str) -> dict:
    """Return an index dict compatible with the legacy JSON layout.

    Reconstructed from SQLite so any caller that still introspects the
    old ``{"playlist": [...], "album": [...], "urls": [...]}`` shape
    continues to work. Falls back to reading the on-disk JSON file if
    SQLite cannot be opened.

    Args:
        download_path: Path to the download directory.

    Returns:
        Dict with ``playlist``, ``album``, ``artist``, ``track`` and
        ``urls`` keys. Empty on any error.
    """
    try:
        with IndexDB(download_path) as db:
            return {
                "playlist": sorted(db.get_ids("playlist")),
                "album": sorted(db.get_ids("album")),
                "artist": sorted(db.get_ids("artist")),
                "track": sorted(db.get_ids("track")),
                "urls": db.list_urls(),
            }
    except Exception:
        # Last-ditch fallback to the legacy JSON file.
        f = Path(download_path) / INDEX_FILENAME
        try:
            return json.loads(f.read_text())
        except Exception:
            return {}


def save_index(download_path: str, index: dict) -> None:
    """Persist the given index dict into SQLite.

    Kept for backward compatibility. Existing callers passing a legacy
    dict have each entry written via
    :meth:`IndexDB.add_downloaded`.

    Args:
        download_path: Path to the download directory.
        index: Index dict to write.
    """
    try:
        with IndexDB(download_path) as db:
            # Map ids -> urls if caller supplied them.
            url_by_key: dict[tuple[str, str], str] = {}
            for url in index.get("urls", []) or []:
                m = _TIDAL_URL_RE.search(url)
                if m:
                    url_by_key[(m.group(1), m.group(2))] = url
            for kind in ("playlist", "album", "artist", "track"):
                for rid in index.get(kind, []) or []:
                    rid_str = str(rid)
                    db.add_downloaded(
                        kind, rid_str, url_by_key.get((kind, rid_str)),
                    )
            for (kind, rid), url in url_by_key.items():
                db.add_downloaded(kind, rid, url)
    except Exception:
        pass


def record_downloaded(download_path: str, url: str) -> None:
    """Record a downloaded Tidal URL's resource id in the local index.

    O(1) insert into SQLite — safe to call from multiple worker threads
    concurrently.

    Args:
        download_path: Path to the download directory.
        url: Full Tidal URL (e.g. ``https://tidal.com/playlist/uuid``).
    """
    m = _TIDAL_URL_RE.search(url)
    if not m:
        return
    kind, rid = m.group(1), m.group(2)
    try:
        with IndexDB(download_path) as db:
            db.add_downloaded(kind, rid, url)
    except Exception:
        pass
