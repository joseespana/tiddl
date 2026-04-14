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
        item_ready_tagged: Emitted as (item, source) where source is
            ``"owned"`` (user-created playlist) or ``"liked"`` (favorited).
            Non-playlist tabs always emit source=``""``.
        finished: Emitted when the run loop ends (success or error).
        error: Emitted with an error message string on failure.
    """

    item_ready = Signal(object)
    item_ready_tagged = Signal(object, str)
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
                seen_uuids: set[str] = set()

                # 1) User-CREATED playlists (paginated endpoint)
                offset = 0
                page_size = 50
                while not self._interrupted:
                    try:
                        page = self.api.get_user_playlists(
                            limit=page_size, offset=offset
                        )
                    except Exception as exc:
                        log.warning(
                            "Failed to load user playlists offset=%d: %s",
                            offset, exc,
                        )
                        break
                    for pl in page.items:
                        if self._interrupted:
                            break
                        # Skip duplicates (Tidal can return the same uuid
                        # across pages when items are added during fetch).
                        if pl.uuid in seen_uuids:
                            continue
                        # Skip untitled/imported placeholders.
                        if not (pl.title and pl.title.strip()):
                            log.debug("Skipping unnamed playlist %s", pl.uuid)
                            continue
                        seen_uuids.add(pl.uuid)
                        self.item_ready_tagged.emit(pl, "owned")
                    total = page.totalNumberOfItems
                    offset += len(page.items)
                    if not page.items or offset >= total:
                        break

                # 2) FAVORITED (liked/followed) playlists
                for uuid in favorites.PLAYLIST:
                    if self._interrupted:
                        break
                    if uuid in seen_uuids:
                        continue
                    try:
                        pl = self.api.get_playlist(playlist_uuid=uuid)
                    except Exception as exc:
                        log.warning("Failed to load playlist %s: %s", uuid, exc)
                        continue
                    if not (pl.title and pl.title.strip()):
                        continue
                    seen_uuids.add(pl.uuid)
                    self.item_ready_tagged.emit(pl, "liked")

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
