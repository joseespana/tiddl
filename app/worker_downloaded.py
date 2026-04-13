"""
DownloadedWorker — loads previously downloaded items from the local index.

Uses the QObject + moveToThread pattern instead of QThread subclassing.
"""
import logging

from PySide6.QtCore import QObject, Signal

from tiddl.core.api.api import TidalAPI

from app.worker_index import load_index, _TIDAL_URL_RE

log = logging.getLogger(__name__)


class DownloadedWorker(QObject):
    """Loads previously downloaded Tidal items by reading URLs from the local index.

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
            download_path: Path to the download directory containing the index.
        """
        super().__init__()
        self.api = api
        self.download_path = download_path
        self._interrupted = False

    def interrupt(self) -> None:
        """Request the worker to stop at the next iteration checkpoint."""
        self._interrupted = True

    def run(self) -> None:
        """Load previously downloaded items from the index and emit them.

        Called by the owning QThread via ``thread.started`` signal.
        Always emits :attr:`finished` before returning.
        """
        try:
            index = load_index(self.download_path)
            urls = index.get("urls", [])

            for url in urls:
                if self._interrupted:
                    break
                try:
                    m = _TIDAL_URL_RE.search(url)
                    if not m:
                        continue
                    rtype, rid = m.groups()

                    if rtype == "playlist":
                        item = self.api.get_playlist(playlist_uuid=rid)
                    elif rtype == "album":
                        item = self.api.get_album(album_id=int(rid))
                    elif rtype == "artist":
                        item = self.api.get_artist(artist_id=int(rid))
                    else:
                        continue

                    self.item_ready.emit(item)
                except Exception as exc:
                    log.warning("Failed to load downloaded item %s: %s", url, exc)

            self.finished.emit()
        except Exception as exc:
            self.error.emit(str(exc))
            self.finished.emit()
