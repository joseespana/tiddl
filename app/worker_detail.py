"""
DetailWorker — loads detail data for a library item (album/playlist/artist).

This worker is the mapping boundary between ``tiddl.core.api.models`` and
the pure-data :class:`DetailVM` the dialog consumes. All isinstance /
hasattr checks against API types live here; the view code reads only
string/int fields.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, List, Literal, Optional

from PySide6.QtCore import QObject, Signal

from tiddl.core.api.api import TidalAPI
from app.models.detail_vm import DetailVM, TrackRow, AlbumRow

log = logging.getLogger(__name__)

DetailKind = Literal["playlist", "album", "artist"]

# Page sizes match the ``_MAX`` constants in :mod:`tiddl.core.api.api`.
_ALBUM_PAGE = 100
_PLAYLIST_PAGE = 100
_ARTIST_ALBUMS_PAGE = 50


def _format_duration(total_s: int) -> str:
    """Format a duration in seconds as ``"2h 31min"`` or ``"42min 07s"``."""
    if total_s <= 0:
        return ""
    h, rem = divmod(int(total_s), 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}h {m:02d}min"
    return f"{m}min {s:02d}s"


def _join_artists(item: Any) -> str:
    """Comma-join artist names on *item*, falling back to ``item.artist.name``."""
    artists = getattr(item, "artists", None) or []
    names = [a.name for a in artists if getattr(a, "name", None)]
    if names:
        return ", ".join(names)
    artist = getattr(item, "artist", None)
    return getattr(artist, "name", "") if artist else ""


def _track_quality(track: Any) -> str:
    """Best-effort human-readable quality label for a Track/Video."""
    q = getattr(track, "audioQuality", None) or getattr(track, "quality", None)
    if not q:
        return ""
    # Core constants look like "HI_RES_LOSSLESS" / "LOSSLESS" / "HIGH" / "LOW".
    mapping = {
        "HI_RES_LOSSLESS": "HI-RES",
        "LOSSLESS": "FLAC",
        "HIGH": "AAC 320",
        "LOW": "AAC 96",
    }
    return mapping.get(str(q), str(q))


class DetailWorker(QObject):
    """Fetches album/playlist tracks or artist albums and emits a DetailVM.

    Signals:
        ready: Emitted with the fully-populated :class:`DetailVM`.
        error: Emitted with an error message string on failure.
        finished: Always emitted last (success or error).
    """

    ready = Signal(object)
    error = Signal(str)
    finished = Signal()

    def __init__(self, api: TidalAPI, kind: DetailKind, ident: str) -> None:
        """Initialise the worker.

        Args:
            api: Authenticated TidalAPI instance.
            kind: ``"album"``, ``"playlist"`` or ``"artist"``.
            ident: Album id (str), playlist uuid, or artist id (str).
        """
        super().__init__()
        self.api = api
        self.kind: DetailKind = kind
        self.ident = ident
        self.interrupted = threading.Event()

    def interrupt(self) -> None:
        """Request the worker to stop between paginated API calls."""
        self.interrupted.set()

    # ── Entry point ───────────────────────────────────────────────────────────

    def run(self) -> None:
        try:
            if self.kind == "album":
                vm = self._run_album()
            elif self.kind == "playlist":
                vm = self._run_playlist()
            elif self.kind == "artist":
                vm = self._run_artist()
            else:
                raise ValueError(f"unknown detail kind: {self.kind}")
            if vm is not None and not self.interrupted.is_set():
                self.ready.emit(vm)
        except Exception as exc:  # noqa: BLE001 — surfaced to the UI
            log.exception("DetailWorker failed")
            self.error.emit(str(exc))
        finally:
            self.finished.emit()

    # ── Album ─────────────────────────────────────────────────────────────────

    def _run_album(self) -> Optional[DetailVM]:
        album = self.api.get_album(int(self.ident))
        if self.interrupted.is_set():
            return None

        rows: List[TrackRow] = []
        total_s = 0
        offset = 0
        total = None
        number = 1
        while True:
            page = self.api.get_album_items(
                int(self.ident), limit=_ALBUM_PAGE, offset=offset
            )
            if total is None:
                total = getattr(page, "totalNumberOfItems", 0) or 0
            for wrapper in page.items:
                if self.interrupted.is_set():
                    return None
                item = getattr(wrapper, "item", None)
                if item is None:
                    continue
                w_type = getattr(wrapper, "type", "track")
                rows.append(
                    TrackRow(
                        number=number,
                        title=getattr(item, "title", "") or "",
                        artist=_join_artists(item),
                        duration_s=int(getattr(item, "duration", 0) or 0),
                        quality=_track_quality(item),
                        url=getattr(item, "url", "") or "",
                        kind="video" if w_type == "video" else "track",
                    )
                )
                total_s += int(getattr(item, "duration", 0) or 0)
                number += 1
            offset += _ALBUM_PAGE
            if total is None or offset >= total or not page.items:
                break
            if self.interrupted.is_set():
                return None

        artist_name = ""
        artist = getattr(album, "artist", None)
        if artist is not None:
            artist_name = getattr(artist, "name", "") or ""

        subtitle = f"{artist_name} \u00b7 {len(rows)} tracks \u00b7 {_format_duration(total_s)}".strip(" \u00b7")
        return DetailVM(
            kind="album",
            title=getattr(album, "title", "") or "",
            subtitle=subtitle,
            cover_url=getattr(album, "cover", None),
            tracks=rows,
            albums=[],
            total_duration_s=total_s,
        )

    # ── Playlist ──────────────────────────────────────────────────────────────

    def _run_playlist(self) -> Optional[DetailVM]:
        playlist = self.api.get_playlist(self.ident)
        if self.interrupted.is_set():
            return None

        rows: List[TrackRow] = []
        total_s = 0
        offset = 0
        total = None
        number = 1
        while True:
            page = self.api.get_playlist_items(
                self.ident, limit=_PLAYLIST_PAGE, offset=offset
            )
            if total is None:
                total = getattr(page, "totalNumberOfItems", 0) or 0
            for wrapper in page.items:
                if self.interrupted.is_set():
                    return None
                item = getattr(wrapper, "item", None)
                if item is None:
                    continue
                w_type = getattr(wrapper, "type", "track")
                rows.append(
                    TrackRow(
                        number=number,
                        title=getattr(item, "title", "") or "",
                        artist=_join_artists(item),
                        duration_s=int(getattr(item, "duration", 0) or 0),
                        quality=_track_quality(item),
                        url=getattr(item, "url", "") or "",
                        kind="video" if w_type == "video" else "track",
                    )
                )
                total_s += int(getattr(item, "duration", 0) or 0)
                number += 1
            offset += _PLAYLIST_PAGE
            if total is None or offset >= total or not page.items:
                break
            if self.interrupted.is_set():
                return None

        # Playlist.creator in the core model only exposes an ``id`` field,
        # so there's no human-readable creator name available here. Use the
        # static fallback "Tidal" per spec.
        creator_name = "Tidal"
        subtitle = f"by {creator_name} \u00b7 {len(rows)} tracks \u00b7 {_format_duration(total_s)}".strip(" \u00b7")
        return DetailVM(
            kind="playlist",
            title=getattr(playlist, "title", "") or "",
            subtitle=subtitle,
            cover_url=getattr(playlist, "squareImage", None),
            tracks=rows,
            albums=[],
            total_duration_s=total_s,
        )

    # ── Artist ────────────────────────────────────────────────────────────────

    def _run_artist(self) -> Optional[DetailVM]:
        artist = self.api.get_artist(int(self.ident))
        if self.interrupted.is_set():
            return None

        albums: List[AlbumRow] = []
        offset = 0
        total = None
        while True:
            page = self.api.get_artist_albums(
                int(self.ident), limit=_ARTIST_ALBUMS_PAGE, offset=offset
            )
            if total is None:
                total = getattr(page, "totalNumberOfItems", 0) or 0
            for album in page.items:
                if self.interrupted.is_set():
                    return None
                year = ""
                release = getattr(album, "releaseDate", None)
                if release is not None:
                    # releaseDate is a datetime on Album.
                    try:
                        year = str(getattr(release, "year", ""))
                    except Exception:
                        try:
                            year = str(release)[:4]
                        except Exception:
                            year = ""
                a_artist = getattr(album, "artist", None)
                albums.append(
                    AlbumRow(
                        title=getattr(album, "title", "") or "",
                        artist=getattr(a_artist, "name", "") if a_artist else "",
                        year=year or "",
                        num_tracks=int(getattr(album, "numberOfTracks", 0) or 0),
                        cover_url=getattr(album, "cover", None),
                        url=getattr(album, "url", "") or (
                            f"https://tidal.com/album/{getattr(album, 'id', '')}"
                            if getattr(album, "id", None) else ""
                        ),
                    )
                )
            offset += _ARTIST_ALBUMS_PAGE
            if total is None or offset >= total or not page.items:
                break
            if self.interrupted.is_set():
                return None

        subtitle = f"{len(albums)} albums"
        return DetailVM(
            kind="artist",
            title=getattr(artist, "name", "") or "",
            subtitle=subtitle,
            cover_url=getattr(artist, "picture", None),
            tracks=[],
            albums=albums,
            total_duration_s=0,
        )
