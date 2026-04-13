"""
LibraryWorker — loads user playlists / albums / artists from the Tidal API.

Uses the QObject + moveToThread pattern instead of QThread subclassing.
"""
import logging
from typing import Literal

from PySide6.QtCore import QObject, Signal

from tiddl.core.api.api import TidalAPI
from tiddl.core.api.models.base import Favorites

log = logging.getLogger(__name__)

LibraryTab = Literal["playlists", "albums", "artists"]


class LibraryWorker(QObject):
    """Loads the user's Tidal library items for a given tab.

    Signals:
        item_ready: Emitted for each loaded item object.
        finished: Emitted when the run loop ends (success or error).
        error: Emitted with an error message string on failure.
    """

    item_ready = Signal(object)
    finished = Signal()
    error = Signal(str)

    def __init__(self, api: TidalAPI, tab: LibraryTab) -> None:
        """Initialise the worker.

        Args:
            api: Authenticated TidalAPI instance.
            tab: Which library tab to load (``"playlists"``, ``"albums"``,
                or ``"artists"``).
        """
        super().__init__()
        self.api = api
        self.tab = tab
        self._interrupted = False

    def interrupt(self) -> None:
        """Request the worker to stop at the next iteration checkpoint."""
        self._interrupted = True

    def run(self) -> None:
        """Fetch library items and emit them one by one.

        Called by the owning QThread via ``thread.started`` signal.
        Always emits :attr:`finished` before returning.
        """
        try:
            favorites: Favorites = self.api.get_favorites()

            if self.tab == "playlists":
                for uuid in favorites.PLAYLIST:
                    if self._interrupted:
                        break
                    pl = self.api.get_playlist(playlist_uuid=uuid)
                    self.item_ready.emit(pl)

            elif self.tab == "albums":
                for album_id in favorites.ALBUM:
                    if self._interrupted:
                        break
                    try:
                        al = self.api.get_album(album_id=album_id)
                        self.item_ready.emit(al)
                    except Exception as exc:
                        log.warning("Failed to load album %s: %s", album_id, exc)

            elif self.tab == "artists":
                for artist_id in favorites.ARTIST:
                    if self._interrupted:
                        break
                    try:
                        ar = self.api.get_artist(artist_id=artist_id)
                        self.item_ready.emit(ar)
                    except Exception as exc:
                        log.warning("Failed to load artist %s: %s", artist_id, exc)

            self.finished.emit()
        except Exception as exc:
            self.error.emit(str(exc))
            self.finished.emit()
