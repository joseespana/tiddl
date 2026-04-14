"""Map Tidal core models to :class:`CardVM` view DTOs.

Keeping the ``tiddl.core.api.models`` imports here (not in the view)
means the GUI stays decoupled from server-schema changes.
"""
from __future__ import annotations

from typing import Any, Optional

from app.models.card_vm import CardVM, Kind


def _classify(item: Any) -> Kind:
    """Return the ``Kind`` discriminator for *item*.

    Import Tidal model types lazily so unit tests / headless imports
    don't have to load the whole core stack.
    """
    try:
        from tiddl.core.api.models import Playlist, Album
        if isinstance(item, Playlist):
            return "playlist"
        if isinstance(item, Album):
            return "album"
    except Exception:
        pass
    if hasattr(item, "uuid"):
        return "playlist"
    if hasattr(item, "numberOfTracks"):
        return "album"
    if hasattr(item, "artistTypes") or not hasattr(item, "title"):
        return "artist"
    return "album"


def _cover(item: Any) -> Optional[str]:
    for attr in ("squareImage", "cover", "picture"):
        v = getattr(item, attr, None)
        if v:
            return v
    return None


def _title(item: Any) -> str:
    return getattr(item, "title", getattr(item, "name", "Unknown"))


def _subtitle(kind: Kind, item: Any) -> str:
    if kind == "playlist":
        n = getattr(item, "numberOfTracks", None)
        creator = getattr(item, "creator", None)
        cname = getattr(creator, "name", None) if creator else None
        if cname and n:
            return f"{cname} \u00b7 {n} tracks"
        if n:
            return f"Playlist \u00b7 {n} tracks"
        return "Playlist"
    if kind == "album":
        artist_name = item.artist.name if getattr(item, "artist", None) else ""
        n = getattr(item, "numberOfTracks", None)
        if artist_name and n:
            return f"{artist_name} \u00b7 {n} tracks"
        if n:
            return f"{n} tracks"
        return artist_name
    # artist
    pop = getattr(item, "popularity", None)
    return f"Popularity: {pop}" if pop else "Artist"


def _url(kind: Kind, item: Any) -> str:
    if kind == "playlist":
        uid = getattr(item, "uuid", "")
        return f"https://tidal.com/playlist/{uid}" if uid else ""
    if kind == "album":
        aid = getattr(item, "id", "")
        return f"https://tidal.com/album/{aid}" if aid else getattr(item, "url", "") or ""
    if kind == "artist":
        aid = getattr(item, "id", "")
        return f"https://tidal.com/artist/{aid}" if aid else getattr(item, "url", "") or ""
    return getattr(item, "url", "") or ""


def _ident(kind: Kind, item: Any) -> str:
    if kind == "playlist":
        return str(getattr(item, "uuid", ""))
    return str(getattr(item, "id", ""))


def _is_downloaded(kind: Kind, item: Any, cache: Any) -> bool:
    if cache is None:
        return False
    try:
        if kind == "playlist":
            return cache.has_playlist(
                _title(item), uuid=str(getattr(item, "uuid", ""))
            )
        if kind == "album":
            artist = item.artist.name if getattr(item, "artist", None) else ""
            return cache.has_album(
                artist, _title(item), album_id=str(getattr(item, "id", "")),
            )
        if kind == "artist":
            name = getattr(item, "name", "")
            return cache.has_artist(
                name, artist_id=str(getattr(item, "id", "")),
            )
    except Exception:
        return False
    return False


def to_card_vm(item: Any, cache: Any, source: str = "") -> CardVM:
    """Build a :class:`CardVM` for *item*, consulting *cache* for the badge."""
    kind = _classify(item)
    return CardVM(
        kind=kind,
        title=_title(item),
        subtitle=_subtitle(kind, item),
        url=_url(kind, item),
        cover_url=_cover(item),
        is_downloaded=_is_downloaded(kind, item, cache),
        source=source,
        ident=_ident(kind, item),
    )


def compute_downloaded(vm: CardVM, cache: Any) -> bool:
    """Re-check the downloaded flag for *vm* against an updated cache.

    Used by ``MainPresenter._rebuild_cache`` to refresh badges without
    rebuilding every widget. Mirrors the logic in :func:`to_card_vm`
    but consumes only string fields so the view can stay model-free.
    """
    if cache is None:
        return False
    try:
        if vm.kind == "playlist":
            return cache.has_playlist(vm.title, uuid=vm.ident)
        if vm.kind == "album":
            # vm.subtitle is "<artist> · <n> tracks" — we stored the
            # downloaded flag at creation using the real artist name,
            # which we can recover from the subtitle's "… · N tracks"
            # tail. Falls back to album_id lookup which is what actually
            # matters for exact matches.
            artist = vm.subtitle.split(" \u00b7 ")[0] if " \u00b7 " in vm.subtitle else ""
            return cache.has_album(artist, vm.title, album_id=vm.ident)
        if vm.kind == "artist":
            return cache.has_artist(vm.title, artist_id=vm.ident)
    except Exception:
        return False
    return False
