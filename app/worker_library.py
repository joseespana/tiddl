"""
LibraryWorker — loads user playlists / albums / artists from the Tidal API.

Uses the QObject + moveToThread pattern, and a ThreadPoolExecutor to
parallelise the per-id detail fetches. The underlying
``requests_cache.CachedSession`` (SQLite backend) is safe for concurrent
reads and serialises writes, so 8 workers is a sensible sweet spot.
"""
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Literal

from PySide6.QtCore import QObject, Signal

from tiddl.core.api.api import TidalAPI
from tiddl.core.api.models.base import Favorites

log = logging.getLogger(__name__)

LibraryTab = Literal["playlists", "albums", "artists"]

_MAX_WORKERS = 8


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
                self._run_playlists(favorites)
            elif self.tab == "albums":
                self._run_albums(favorites)
            elif self.tab == "artists":
                self._run_artists(favorites)

            self.finished.emit()
        except Exception as exc:
            self.error.emit(str(exc))
            self.finished.emit()

    # ------------------------------------------------------------------
    # Per-tab implementations
    # ------------------------------------------------------------------
    def _run_playlists(self, favorites: Favorites) -> None:
        """Load user-created then favorited playlists concurrently."""
        seen_uuids: set[str] = set()

        # 1) User-CREATED playlists (paginated — sequential because we
        # need the running total to know when to stop).
        offset = 0
        page_size = 50
        while not self._interrupted:
            try:
                page = self.api.get_user_playlists(
                    limit=page_size, offset=offset,
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
                if pl.uuid in seen_uuids:
                    continue
                if not (pl.title and pl.title.strip()):
                    log.debug("Skipping unnamed playlist %s", pl.uuid)
                    continue
                seen_uuids.add(pl.uuid)
                self.item_ready_tagged.emit(pl, "owned")
            total = page.totalNumberOfItems
            offset += len(page.items)
            if not page.items or offset >= total:
                break

        if self._interrupted:
            return

        # 2) FAVORITED playlists — concurrent fetch.
        uuids = [u for u in favorites.PLAYLIST if u not in seen_uuids]
        if not uuids:
            return

        pool = ThreadPoolExecutor(max_workers=_MAX_WORKERS)
        try:
            futures = {
                pool.submit(self.api.get_playlist, playlist_uuid=u): u
                for u in uuids
            }
            for fut in as_completed(futures):
                if self._interrupted:
                    break
                uuid = futures[fut]
                try:
                    pl = fut.result()
                except Exception as exc:
                    log.warning("Failed to load playlist %s: %s", uuid, exc)
                    continue
                if not (pl.title and pl.title.strip()):
                    continue
                if pl.uuid in seen_uuids:
                    continue
                seen_uuids.add(pl.uuid)
                self.item_ready_tagged.emit(pl, "liked")
        finally:
            pool.shutdown(
                wait=not self._interrupted,
                cancel_futures=self._interrupted,
            )

    def _run_albums(self, favorites: Favorites) -> None:
        """Load favorited albums concurrently."""
        ids = list(favorites.ALBUM)
        if not ids:
            return
        pool = ThreadPoolExecutor(max_workers=_MAX_WORKERS)
        try:
            futures = {
                pool.submit(self.api.get_album, album_id=aid): aid
                for aid in ids
            }
            for fut in as_completed(futures):
                if self._interrupted:
                    break
                aid = futures[fut]
                try:
                    item = fut.result()
                    self.item_ready.emit(item)
                except Exception as exc:
                    log.warning("Failed to load album %s: %s", aid, exc)
        finally:
            pool.shutdown(
                wait=not self._interrupted,
                cancel_futures=self._interrupted,
            )

    def _run_artists(self, favorites: Favorites) -> None:
        """Load favorited artists concurrently."""
        ids = list(favorites.ARTIST)
        if not ids:
            return
        pool = ThreadPoolExecutor(max_workers=_MAX_WORKERS)
        try:
            futures = {
                pool.submit(self.api.get_artist, artist_id=aid): aid
                for aid in ids
            }
            for fut in as_completed(futures):
                if self._interrupted:
                    break
                aid = futures[fut]
                try:
                    item = fut.result()
                    self.item_ready.emit(item)
                except Exception as exc:
                    log.warning("Failed to load artist %s: %s", aid, exc)
        finally:
            pool.shutdown(
                wait=not self._interrupted,
                cancel_futures=self._interrupted,
            )
