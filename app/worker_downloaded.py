"""
DownloadedWorker — loads previously downloaded items from the local index.

Uses the QObject + moveToThread pattern and resolves URLs concurrently
via a ThreadPoolExecutor. The source-of-truth URL list comes from the
SQLite-backed :class:`app.models.index_db.IndexDB`.
"""
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from PySide6.QtCore import QObject, Signal

from tiddl.core.api.api import TidalAPI

from app.models.index_db import IndexDB
from app.worker_index import _TIDAL_URL_RE

log = logging.getLogger(__name__)

_MAX_WORKERS = 8


class DownloadedWorker(QObject):
    """Loads previously downloaded Tidal items by resolving recorded URLs.

    Signals:
        item_ready: Emitted for each successfully loaded item object.
        finished: Emitted when the run loop ends (success or error).
        error: Emitted with an error message string on failure.
    """

    item_ready = Signal(object)
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
        self._interrupted = False

    def interrupt(self) -> None:
        """Request the worker to stop at the next iteration checkpoint."""
        self._interrupted = True

    def _fetch(self, url: str):
        """Resolve a single URL to a Tidal API model."""
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

    def run(self) -> None:
        """Resolve recorded URLs concurrently and emit each item.

        Called by the owning QThread via ``thread.started`` signal.
        Always emits :attr:`finished` before returning.
        """
        try:
            try:
                with IndexDB(self.download_path) as db:
                    urls = db.list_urls()
            except Exception as exc:
                log.warning("Failed to open index DB: %s", exc)
                urls = []

            if not urls or self._interrupted:
                self.finished.emit()
                return

            pool = ThreadPoolExecutor(max_workers=_MAX_WORKERS)
            try:
                futures = {pool.submit(self._fetch, url): url for url in urls}
                for fut in as_completed(futures):
                    if self._interrupted:
                        break
                    url = futures[fut]
                    try:
                        item = fut.result()
                    except Exception as exc:
                        log.warning(
                            "Failed to load downloaded item %s: %s", url, exc,
                        )
                        continue
                    if item is not None:
                        self.item_ready.emit(item)
            finally:
                pool.shutdown(
                    wait=not self._interrupted,
                    cancel_futures=self._interrupted,
                )

            self.finished.emit()
        except Exception as exc:
            self.error.emit(str(exc))
            self.finished.emit()
