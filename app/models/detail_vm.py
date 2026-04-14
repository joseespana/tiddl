"""DetailVM — the presenter->view DTO for the detail dialog.

Mirrors the role of :mod:`app.models.card_vm`: the dialog reads only
plain Python fields and never imports Tidal core models. The mapping
from API objects to this DTO lives in :mod:`app.worker_detail`.
"""
from dataclasses import dataclass, field
from typing import List, Literal, Optional


@dataclass(frozen=True)
class TrackRow:
    number: int               # 1-based position in the list
    title: str
    artist: str               # comma-joined artist names
    duration_s: int           # seconds
    quality: str              # e.g. "FLAC HI_RES" or "" if unknown
    url: str                  # tidal.com/track/<id>
    kind: Literal["track", "video"] = "track"


@dataclass(frozen=True)
class AlbumRow:
    title: str
    artist: str
    year: Optional[str]       # "2001" if release_date available, else ""
    num_tracks: int
    cover_url: Optional[str]
    url: str                  # tidal.com/album/<id>


@dataclass(frozen=True)
class DetailVM:
    kind: Literal["playlist", "album", "artist"]
    title: str
    subtitle: str             # e.g. "by Creator · 42 tracks · 2h 31min"
    cover_url: Optional[str]
    tracks: List[TrackRow] = field(default_factory=list)   # for album/playlist
    albums: List[AlbumRow] = field(default_factory=list)   # for artist
    total_duration_s: int = 0
