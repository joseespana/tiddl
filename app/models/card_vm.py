"""CardVM — the presenter->view DTO for library grid cards.

The view used to import ``tiddl.core.api.models`` directly to do
``isinstance(d, Playlist)`` checks and attribute fallbacks. That coupled
UI code to server models; a schema change could break the GUI. The
presenter now maps every incoming API object to a ``CardVM`` and the
view only reads string/bool fields.
"""
from dataclasses import dataclass
from typing import Literal, Optional

Kind = Literal["playlist", "album", "artist"]


@dataclass(frozen=True)
class CardVM:
    kind: Kind
    title: str
    subtitle: str
    url: str              # Tidal URL used for download
    cover_url: Optional[str]
    is_downloaded: bool
    source: str           # "owned" | "liked" | ""
    # Identifier the view can use to refresh a single card in place
    ident: str            # playlist uuid, album id, or artist id (str)
