"""
SearchWorker — searches the Tidal catalog and emits matching items.

Uses the QObject + moveToThread pattern instead of QThread subclassing.
"""
import logging
from typing import Literal

from PySide6.QtCore import QObject, Signal

from tiddl.core.api.api import TidalAPI

log = logging.getLogger(__name__)

SearchType = Literal["playlists", "albums", "artists"]


class SearchWorker(QObject):
    """Searches the Tidal catalog for a given query.

    A single API call is made; no interruption checkpoint is possible
    during the network round-trip, but :attr:`finished` is always emitted.

    Signals:
        item_ready: Emitted for each result item.
        finished: Emitted when the search completes (success or error).
        error: Emitted with an error message string on failure.
    """

    item_ready = Signal(object)
    finished = Signal()
    error = Signal(str)

    def __init__(self, api: TidalAPI, query: str, search_type: SearchType) -> None:
        """Initialise the worker.

        Args:
            api: Authenticated TidalAPI instance.
            query: Search term entered by the user.
            search_type: Which content type to surface
                (``"playlists"``, ``"albums"``, or ``"artists"``).
        """
        super().__init__()
        self.api = api
        self.query = query
        self.search_type = search_type

    def run(self) -> None:
        """Run the Tidal search and emit results.

        Called by the owning QThread via ``thread.started`` signal.
        Always emits :attr:`finished` before returning.
        """
        try:
            results = self.api.get_search(self.query)
            if self.search_type == "playlists":
                for item in results.playlists.items:
                    self.item_ready.emit(item)
            elif self.search_type == "albums":
                for item in results.albums.items:
                    self.item_ready.emit(item)
            elif self.search_type == "artists":
                for item in results.artists.items:
                    self.item_ready.emit(item)
            self.finished.emit()
        except Exception as exc:
            self.error.emit(str(exc))
            self.finished.emit()
