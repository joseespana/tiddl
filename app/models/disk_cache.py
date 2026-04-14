"""
DiskCache — persistent, incremental scanner for the download directory.

Loads the cached (artist, album) pairs from the SQLite-backed
:class:`app.models.index_db.IndexDB` on construction so the UI is ready
immediately, then runs an mtime-based background refresh to absorb any
new or changed folders. Large libraries (10k+ tracks) thus pay a full
walk only once — on first launch.
"""
from __future__ import annotations

import logging
import re
import threading
from pathlib import Path
from typing import Optional

from app.models.index_db import IndexDB

log = logging.getLogger(__name__)


def _sanitize(s: str) -> str:
    """Remove filesystem-unsafe characters from *s*."""
    return re.sub(r'[\\/:"*?<>|]+', "", s)


def _norm(s: str) -> str:
    """Return *s* sanitized, lower-cased and stripped."""
    return _sanitize(s).lower().strip()


class DiskCache:
    """Scans the download directory and provides O(1) lookups.

    The public attributes and method signatures are identical to the
    previous eager implementation; internally the data is now loaded
    from SQLite on construction and refreshed in the background.

    Attributes:
        m3u_stems: Normalised playlist title stems found as .m3u files.
        albums: Set of ``(artist_norm, album_norm)`` tuples.
        artists: Set of normalised artist folder names.
        pl_uuids: Playlist UUIDs recorded in the index.
        album_ids: Album IDs (as strings) recorded in the index.
        artist_ids: Artist IDs (as strings) recorded in the index.
    """

    _SKIP_DIRS = {
        "m3u", "mixes", "playlists",
        ".spotlight-v100", ".fseventsd", ".trashes", ".temporaryitems",
    }

    def __init__(self, path: str) -> None:
        """Initialise the cache for the directory at *path*.

        Synchronously loads whatever is already in the SQLite cache so
        the caller sees populated sets immediately; then enqueues a
        background scan to pick up new/changed folders.

        Args:
            path: Absolute path to the download directory.
        """
        self._path = path
        self.m3u_stems: set[str] = set()
        self.albums: set[tuple[str, str]] = set()
        self.artists: set[str] = set()
        self.pl_uuids: set[str] = set()
        self.album_ids: set[str] = set()
        self.artist_ids: set[str] = set()

        self._refresh_lock = threading.Lock()
        self._refresh_thread: Optional[threading.Thread] = None

        base = Path(path)
        if not base.exists():
            return

        try:
            self._db = IndexDB(path)
        except Exception as exc:
            log.warning("IndexDB open failed at %s: %s", path, exc)
            self._db = None  # type: ignore[assignment]
            # Without DB we still do a live walk so the UI is usable.
            self._live_scan(base)
            self._scan_m3u(base)
            return

        # 1) Downloaded-items sets from SQLite.
        self.pl_uuids = self._db.get_ids("playlist")
        self.album_ids = self._db.get_ids("album")
        self.artist_ids = self._db.get_ids("artist")

        # 2) Seed albums/artists from the persisted scan cache.
        for row in self._db.get_scan_rows():
            if not row["has_audio"]:
                continue
            artist = row["artist"]
            album = row["album"]
            if artist and album:
                self.albums.add((artist, album))
                self.artists.add(artist)

        # 3) M3U stems are cheap — do them synchronously.
        self._scan_m3u(base)

        # 4) Kick off background refresh (mtime-based, incremental).
        self._start_background_refresh()

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------
    def _scan_m3u(self, base: Path) -> None:
        """Populate :attr:`m3u_stems` from the ``m3u`` subdirectory."""
        m3u_dir = base / "m3u"
        if m3u_dir.exists():
            try:
                self.m3u_stems = {_norm(f.stem) for f in m3u_dir.glob("*.m3u")}
            except Exception as exc:
                log.warning("m3u scan failed: %s", exc)

    def _live_scan(self, base: Path) -> None:
        """Fallback full walk used only when SQLite is unavailable."""
        try:
            for artist_dir in base.iterdir():
                if not artist_dir.is_dir():
                    continue
                if artist_dir.name.lower() in self._SKIP_DIRS:
                    continue
                aname = _norm(artist_dir.name)
                found_album = False
                for album_dir in artist_dir.iterdir():
                    if album_dir.is_dir() and self._dir_has_audio(album_dir):
                        self.albums.add((aname, _norm(album_dir.name)))
                        found_album = True
                if found_album:
                    self.artists.add(aname)
        except Exception as exc:
            log.warning("live scan failed: %s", exc)

    @staticmethod
    def _dir_has_audio(album_dir: Path) -> bool:
        """Return True when *album_dir* contains at least one .flac/.m4a."""
        try:
            for entry in album_dir.iterdir():
                if entry.is_file():
                    suffix = entry.suffix.lower()
                    if suffix == ".flac" or suffix == ".m4a":
                        return True
        except Exception:
            return False
        return False

    @staticmethod
    def _count_audio(album_dir: Path) -> int:
        """Count .flac/.m4a files directly under *album_dir* (non-recursive)."""
        n = 0
        try:
            for entry in album_dir.iterdir():
                if entry.is_file():
                    suffix = entry.suffix.lower()
                    if suffix == ".flac" or suffix == ".m4a":
                        n += 1
        except Exception:
            return 0
        return n

    def _start_background_refresh(self) -> None:
        """Launch the incremental refresh on a daemon thread."""
        t = threading.Thread(
            target=self._run_refresh,
            name="DiskCacheRefresh",
            daemon=True,
        )
        self._refresh_thread = t
        t.start()

    def _run_refresh(self) -> None:
        """Thread target — guarded to avoid concurrent refreshes."""
        if not self._refresh_lock.acquire(blocking=False):
            return
        try:
            self._incremental_scan()
        except Exception as exc:
            log.warning("DiskCache refresh failed: %s", exc)
        finally:
            self._refresh_lock.release()

    def _incremental_scan(self) -> None:
        """Walk the download dir and upsert only changed folders."""
        if self._db is None:
            return
        base = Path(self._path)
        if not base.exists():
            return

        cached_mtimes = self._db.get_scan_mtimes()
        # Rows that were cached before track_count existed and still show 0
        # even though they have audio. Force a recount on these regardless
        # of whether the folder mtime changed.
        try:
            needs_count = self._db.paths_missing_track_count()
        except Exception:
            needs_count = set()
        seen_paths: set[str] = set()
        new_rows: list[
            tuple[str, int, str | None, str | None, bool, int]
        ] = []

        new_albums: set[tuple[str, str]] = set()
        new_artists: set[str] = set()

        try:
            artist_iter = list(base.iterdir())
        except Exception as exc:
            log.warning("iterdir(%s) failed: %s", base, exc)
            return

        for artist_dir in artist_iter:
            if not artist_dir.is_dir():
                continue
            if artist_dir.name.lower() in self._SKIP_DIRS:
                continue
            aname = _norm(artist_dir.name)

            try:
                album_iter = list(artist_dir.iterdir())
            except Exception:
                continue

            artist_found = False
            for album_dir in album_iter:
                if not album_dir.is_dir():
                    continue
                path_str = str(album_dir)
                seen_paths.add(path_str)
                try:
                    mtime_ns = album_dir.stat().st_mtime_ns
                except OSError:
                    continue

                bname = _norm(album_dir.name)

                mtime_unchanged = cached_mtimes.get(path_str) == mtime_ns
                if mtime_unchanged and path_str not in needs_count:
                    # Unchanged — keep whatever the cache said.
                    # (albums set was already seeded in __init__.)
                    if (aname, bname) in self.albums:
                        artist_found = True
                    continue

                track_count = self._count_audio(album_dir)
                has_audio = track_count > 0
                new_rows.append(
                    (path_str, mtime_ns, aname, bname, has_audio, track_count),
                )
                if has_audio:
                    new_albums.add((aname, bname))
                    artist_found = True

            if artist_found:
                new_artists.add(aname)

        # Upsert changed folders.
        if new_rows:
            self._db.upsert_scan_rows(new_rows)

        # Delete rows whose folder no longer exists.
        stale = [p for p in cached_mtimes.keys() if p not in seen_paths]
        if stale:
            self._db.delete_scan_paths(stale)

        # Rebuild in-memory sets from the authoritative DB state.
        rebuilt_albums: set[tuple[str, str]] = set()
        rebuilt_artists: set[str] = set()
        for row in self._db.get_scan_rows():
            if not row["has_audio"]:
                continue
            artist = row["artist"]
            album = row["album"]
            if artist and album:
                rebuilt_albums.add((artist, album))
                rebuilt_artists.add(artist)
        self.albums = rebuilt_albums
        self.artists = rebuilt_artists

        # Merge any brand-new entries in case get_scan_rows was empty.
        self.albums.update(new_albums)
        self.artists.update(new_artists)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def refresh(self) -> None:
        """Run the incremental scan synchronously.

        Used by the Resync button so callers can wait for completion.
        """
        if self._refresh_thread is not None and self._refresh_thread.is_alive():
            self._refresh_thread.join()
        self._run_refresh()

    def stats(self) -> dict[str, int]:
        """Return counters for the Downloaded-tab dashboard.

        - ``playlists``: count of distinct playlists found on disk (via
          m3u stems — these are the ones with a usable ``.m3u``).
        - ``albums``: count of album folders with at least one audio file.
        - ``artists``: count of artist folders that host any audio.
        - ``tracks``: total audio files across every album folder.

        The track count is summed from ``disk_scan_cache.track_count``;
        falls back to 0 when the DB isn't available.
        """
        tracks = 0
        if self._db is not None:
            try:
                tracks = self._db.total_tracks()
            except Exception as exc:
                log.warning("total_tracks() failed: %s", exc)
        return {
            "playlists": len(self.m3u_stems),
            "albums": len(self.albums),
            "artists": len(self.artists),
            "tracks": tracks,
        }

    def has_playlist(self, title: str, uuid: str = "") -> bool:
        """Return True if the playlist is recorded as downloaded."""
        if uuid and uuid in self.pl_uuids:
            return True
        return _norm(title) in self.m3u_stems

    def has_album(self, artist: str, album: str, album_id: str = "") -> bool:
        """Return True if the album is recorded as downloaded."""
        if album_id and str(album_id) in self.album_ids:
            return True
        return (_norm(artist), _norm(album)) in self.albums

    def has_artist(self, name: str, artist_id: str = "") -> bool:
        """Return True if the artist is recorded as downloaded."""
        if artist_id and str(artist_id) in self.artist_ids:
            return True
        return _norm(name) in self.artists
