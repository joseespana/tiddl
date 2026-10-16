"""Find the Tidal equivalent of a Spotify / Apple Music track.

Strategy: search Tidal for "<artist> <title>"; an exact ISRC hit wins
outright, otherwise candidates are scored on title + artist similarity and
duration. A second, looser query (title with feat./remaster suffixes
stripped) runs when the first finds nothing good enough.
"""
import logging
import re
import time
import unicodedata
from difflib import SequenceMatcher
from typing import Optional

from tiddl.core.api.api import TidalAPI
from tiddl.core.api.models import Track

from app.integrations.models import ExternalTrack

log = logging.getLogger(__name__)

_MIN_SCORE = 0.72

_NOISE_RE = re.compile(
    r"\s*[\(\[][^)\]]*(feat\.?|ft\.|with |remaster|version|edit|mono|stereo|live|deluxe)[^)\]]*[\)\]]"
    r"|\s+-\s+.*(remaster|version|edit|mix|mono|live).*$",
    re.IGNORECASE,
)


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^\w\s]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def _base_title(title: str) -> str:
    return _NOISE_RE.sub("", title).strip() or title


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _score(ext: ExternalTrack, cand: Track) -> float:
    cand_title = f"{cand.title} ({cand.version})" if cand.version else cand.title
    title = max(
        _similar(ext.title, cand_title),
        _similar(_base_title(ext.title), _base_title(cand_title)),
    )
    ext_artists = {_norm(a) for a in ext.artists if a}
    cand_artists = {_norm(a.name) for a in cand.artists}
    if ext_artists and cand_artists:
        # Apple gives "A & B" as one string — compare joined forms as well.
        artist = max(
            len(ext_artists & cand_artists) / len(ext_artists),
            _similar(" ".join(sorted(ext_artists)), " ".join(sorted(cand_artists))),
        )
    else:
        artist = 0.5
    score = 0.6 * title + 0.4 * artist
    if ext.duration_ms and cand.duration:
        diff = abs(ext.duration_ms / 1000 - cand.duration)
        if diff <= 3:
            score += 0.05
        elif diff > 15:
            score -= 0.15
    return score


def _search_tracks(api: TidalAPI, query: str) -> list[Track]:
    for attempt in range(3):
        try:
            return list(api.get_search(query).tracks.items)
        except Exception as exc:
            # Tidal answers bursts with 429; back off a little and retry.
            log.debug("search %r failed (%s), attempt %d", query, exc, attempt)
            time.sleep(1.5 * (attempt + 1))
    return []


def match_track(api: TidalAPI, ext: ExternalTrack) -> Optional[Track]:
    """Return the best streamable Tidal track for *ext*, or ``None``."""
    artist = ext.artists[0] if ext.artists else ""
    queries = [f"{artist} {ext.title}".strip()]
    base = _base_title(ext.title)
    if base != ext.title:
        queries.append(f"{artist} {base}".strip())

    best: Optional[Track] = None
    best_score = 0.0
    for query in queries:
        candidates = [c for c in _search_tracks(api, query) if c.allowStreaming]
        if ext.isrc:
            isrc = ext.isrc.upper()
            exact = [c for c in candidates if (c.isrc or "").upper() == isrc]
            if exact:
                return exact[0]
        for cand in candidates:
            s = _score(ext, cand)
            if s > best_score:
                best, best_score = cand, s
        if best_score >= _MIN_SCORE:
            return best
    return None
