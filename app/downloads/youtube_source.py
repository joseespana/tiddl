"""YouTube Music fallback source for Spotify / Apple Music tracks.

Used when the user has no Tidal account, or for tracks Tidal doesn't carry.
Audio is lossy (YouTube serves ~128–256 kbps Opus/AAC), so this is the
"spotDL" path, not a lossless one.

For each track yt-dlp walks the top YouTube Music *songs* results in
relevance order and downloads the first one whose duration is within
±15 s of the original and whose title isn't a karaoke / cover / sped-up
variant. A plain YouTube search is the second chance.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import quote_plus

import requests

from tiddl.core.metadata.track import Metadata, add_flac_metadata, add_mp3_metadata

from app.integrations.models import ExternalTrack

log = logging.getLogger(__name__)

_DURATION_TOLERANCE = 15
_CANDIDATES = 5
# Variants we never want unless the original title asks for them.
_BAD_WORDS = [
    "karaoke", "originally performed", "instrumental", "cover", "8d",
    "sped up", "slowed", "nightcore", "reverb", "tribute", "remix", "live",
]


def ytdlp_path() -> Optional[str]:
    return shutil.which("yt-dlp")


def _match_filter(track: ExternalTrack) -> str:
    rules = []
    if track.duration_ms:
        d = track.duration_ms / 1000
        rules.append(f"duration>={int(d - _DURATION_TOLERANCE)}")
        rules.append(f"duration<={int(d + _DURATION_TOLERANCE)}")
    low = track.title.lower()
    bad = [w for w in _BAD_WORDS if w not in low]
    if bad:
        words = "|".join(re.escape(w) for w in bad)
        rules.append(f"title!~='(?i)\\b({words})\\b'")
    return " & ".join(rules)


def _queries(track: ExternalTrack) -> list[str]:
    artist = track.artists[0] if track.artists else ""
    q = f"{artist} {track.title}".strip()
    return [
        f"https://music.youtube.com/search?q={quote_plus(q)}#songs",
        f"ytsearch{_CANDIDATES}:{artist} - {track.title} audio",
    ]


def _fetch_cover(url: Optional[str]) -> Optional[bytes]:
    if not url:
        return None
    try:
        resp = requests.get(url, timeout=20)
        if resp.status_code == 200:
            return resp.content
    except Exception as exc:
        log.debug("cover fetch failed for %s: %s", url, exc)
    return None


def _tag(path: Path, track: ExternalTrack, album_artist: str) -> None:
    meta = Metadata(
        title=track.title,
        track_number=str(track.track_number or ""),
        disc_number="",
        copyright=None,
        album_artist=album_artist or (track.artists[0] if track.artists else ""),
        artists=track.artists,
        album_title=track.album,
        date="",
        isrc=track.isrc,
        cover_data=_fetch_cover(track.cover_url),
        comment="Source: YouTube Music",
    )
    if path.suffix == ".mp3":
        add_mp3_metadata(path, meta)
    elif path.suffix == ".flac":
        add_flac_metadata(path, meta)


def download_track(
    track: ExternalTrack,
    out_stem: Path,
    audio_format: str,
    album_artist: str = "",
    cancel: Optional[threading.Event] = None,
    on_proc: Optional[Callable[[subprocess.Popen], None]] = None,
) -> Optional[Path]:
    """Download *track* to ``out_stem.<ext>``; return the file or ``None``.

    ``audio_format`` "flac" re-encodes YouTube's lossy stream into FLAC
    (bigger file, same quality); anything else produces MP3.
    """
    ytdlp = ytdlp_path()
    if ytdlp is None:
        return None
    ext = "flac" if audio_format == "flac" else "mp3"
    out_stem.parent.mkdir(parents=True, exist_ok=True)
    # yt-dlp treats % as a template marker in -o.
    out_tpl = str(out_stem).replace("%", "%%") + ".%(ext)s"
    flt = _match_filter(track)

    for query in _queries(track):
        if cancel is not None and cancel.is_set():
            return None
        cmd = [
            ytdlp,
            "--quiet", "--no-warnings", "--no-progress",
            "--socket-timeout", "30",
            "--playlist-items", f"1:{_CANDIDATES}",
            "--max-downloads", "1",
            "--extract-audio", "--audio-format", ext, "--audio-quality", "0",
            "--output", out_tpl,
            "--print", "after_move:filepath",
        ]
        if flt:
            cmd += ["--match-filter", flt]
        cmd += ["--", query]
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace",
            )
            if on_proc is not None:
                on_proc(proc)
            out, err = proc.communicate(timeout=600)
        except Exception as exc:
            log.warning("yt-dlp failed for %s: %s", track.title, exc)
            continue
        # rc 101 = stopped by --max-downloads, i.e. success.
        paths = [Path(line.strip()) for line in out.splitlines() if line.strip()]
        paths = [p for p in paths if p.exists()]
        if paths:
            path = paths[-1]
            try:
                _tag(path, track, album_artist)
            except Exception as exc:
                log.warning("tagging %s failed: %s", path, exc)
            return path
        if proc.returncode not in (0, 101):
            log.debug("yt-dlp rc=%s for %r: %s", proc.returncode, query, err[-300:])
    return None
