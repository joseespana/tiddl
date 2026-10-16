"""Provider-neutral DTOs shared by the Spotify and Apple Music clients."""
from dataclasses import dataclass, field
from typing import Literal, Optional

Provider = Literal["spotify", "apple_music"]
CollectionKind = Literal["playlist", "album", "track"]

PROVIDER_LABEL: dict[str, str] = {
    "spotify": "Spotify",
    "apple_music": "Apple Music",
}


@dataclass(frozen=True)
class ExternalTrack:
    title: str
    artists: list[str]
    album: str = ""
    isrc: str = ""
    duration_ms: int = 0
    cover_url: Optional[str] = None
    track_number: int = 0


@dataclass(frozen=True)
class ExternalCollection:
    provider: Provider
    kind: CollectionKind
    url: str                  # pseudo-URL, see app.integrations.__init__
    title: str
    creator: str = ""
    cover_url: Optional[str] = None
    track_count: int = 0
    # "owned" | "liked" — feeds the Playlists sub-tab filter in the view
    source: str = ""


@dataclass
class CollectionContents:
    collection: ExternalCollection
    tracks: list[ExternalTrack] = field(default_factory=list)
