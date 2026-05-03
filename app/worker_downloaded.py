"""
DownloadedWorker — loads previously downloaded items from the local index.

Uses the QObject + moveToThread pattern and resolves URLs concurrently
via a ThreadPoolExecutor. The source-of-truth URL list comes from the
SQLite-backed :class:`app.models.index_db.IndexDB`.

SoundCloud entries are surfaced from local data alone: the IndexDB row
already carries ``title`` and ``creator``, so we build a ready-to-display
:class:`CardVM` without an API round-trip and emit it via
:attr:`card_ready`.
"""
import logging
import re
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from tiddl.core.api.api import TidalAPI

from app.models.card_vm import CardVM
from app.models.index_db import IndexDB
from app.worker_index import _TIDAL_URL_RE
from app.workers_base import fanout

log = logging.getLogger(__name__)


def _sanitize(s: str) -> str:
    """Mirror SoundCloudRunnable._sanitize_segment for folder lookup."""
    return re.sub(r'[\\/:"*?<>|]+', "_", s).strip().rstrip(".")


class DownloadedWorker(QObject):
    """Loads previously downloaded items by reading the local index.

    Tidal entries fan out to :class:`TidalAPI` for resolution, the same
    as before. SoundCloud entries are built locally from the row's
    ``title`` / ``creator`` columns plus a quick file count, so they
    stay in the Downloaded grid even when offline.

    Signals:
        item_ready: Emitted for each successfully resolved Tidal model.
        card_ready: Emitted for each pre-built :class:`CardVM` (currently
            only used for SoundCloud playlists).
        finished: Emitted when the run loop ends (success or error).
        error: Emitted with an error message string on failure.
    """

    item_ready = Signal(object)
    card_ready = Signal(object)
    finished = Signal()
    error = Signal(str)

    def __init__(self, api: TidalAPI, download_path: str) -> None:
        """Initialise the worker.

        Args:
            api: Authenticated TidalAPI instance.
            download_path: Path to the download directory.
        """
        super().__init__()
        self.api = api
        self.download_path = download_path
        self._interrupted = threading.Event()

    def interrupt(self) -> None:
        """Request the worker to stop at the next iteration checkpoint."""
        self._interrupted.set()

    def _resolve(self, url: str):
        """Resolve a single Tidal URL to an API model (or None)."""
        m = _TIDAL_URL_RE.search(url)
        if not m:
            return None
        rtype, rid = m.groups()
        if rtype == "playlist":
            return self.api.get_playlist(playlist_uuid=rid)
        if rtype == "album":
            return self.api.get_album(album_id=int(rid))
        if rtype == "artist":
            return self.api.get_artist(artist_id=int(rid))
        return None

    def _build_sc_card(self, entry: dict) -> CardVM | None:
        """Construct a CardVM for a SoundCloud playlist row.

        All data comes from the IndexDB row plus an optional disk
        count. No network call is made — this lets the user browse
        their downloaded SC playlists offline.
        """
        title = (entry.get("title") or "").strip()
        creator = (entry.get("creator") or "SoundCloud").strip()
        sc_url = (entry.get("url") or entry.get("id") or "").strip()
        if not title:
            # Last-resort: derive a title from the URL slug so we still
            # render something instead of dropping the row.
            title = sc_url.rstrip("/").rsplit("/", 1)[-1] or "SoundCloud playlist"

        track_count = self._count_sc_tracks(creator, title)
        if track_count > 0:
            subtitle = f"{creator} · {track_count} tracks · SoundCloud"
        else:
            subtitle = f"{creator} · SoundCloud"

        return CardVM(
            kind="playlist",
            title=title,
            subtitle=subtitle,
            url=sc_url,
            cover_url=None,
            is_downloaded=True,
            source="",
            ident=sc_url,
        )

    def _count_sc_tracks(self, creator: str, title: str) -> int:
        """Best-effort track count for an SC playlist on disk."""
        try:
            folder = (
                Path(self.download_path)
                / _sanitize(creator)
                / _sanitize(title)
            )
            if not folder.is_dir():
                return 0
            n = 0
            for entry in folder.iterdir():
                if entry.is_file() and entry.suffix.lower() in (
                    ".mp3", ".m4a", ".flac",
                ):
                    n += 1
            return n
        except Exception:
            return 0

    def run(self) -> None:
        """Resolve recorded entries and emit items + SC cards concurrently.

        Called by the owning QThread via ``thread.started`` signal.
        Always emits :attr:`finished` before returning.
        """
        try:
            try:
                with IndexDB(self.download_path) as db:
                    entries = db.list_entries()
            except Exception as exc:
                log.warning("Failed to open index DB: %s", exc)
                entries = []

            if self._interrupted.is_set():
                self.finished.emit()
                return

            tidal_urls: list[str] = []
            for entry in entries:
                if self._interrupted.is_set():
                    break
                if entry["provider"] == "soundcloud":
                    vm = self._build_sc_card(entry)
                    if vm is not None:
                        self.card_ready.emit(vm)
                else:
                    if entry["url"]:
                        tidal_urls.append(entry["url"])

            if tidal_urls and not self._interrupted.is_set():
                fanout(
                    self._resolve,
                    tidal_urls,
                    self.item_ready.emit,
                    self._interrupted,
                    label="url",
                )

            self.finished.emit()
        except Exception as exc:
            self.error.emit(str(exc))
            self.finished.emit()
