"""Spotify / Apple Music download runnable.

Neither service lets anyone download its (DRM-protected) audio, so each
track is fetched from somewhere else:

* **Tidal** (lossless) when the user is signed in to Tidal and the track
  matches (ISRC first, fuzzy search fallback) — via the tiddl CLI;
* **YouTube Music** (lossy, via yt-dlp) for everything else, including
  every track when there is no Tidal account at all.

Afterwards ``m3u/<title>.m3u`` + a ``.tiddl_index.db`` row are written so
the Downloaded tab and the ✓ badge work like for any Tidal playlist.

On-disk layout for playlists (and albums without Tidal):

    {download_path}/
      Spotify/<title>/<artist> - <title>.flac|mp3
      Spotify/<title>/_not_found.txt        (tracks no source had)
      m3u/<title>.m3u

Albums matched on Tidal are downloaded as the Tidal *album* (tiddl's
normal album template); single tracks as a Tidal track, or into
``<Service>/Singles/`` from YouTube Music.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QRunnable

from tiddl.core.api.models import Track
from tiddl.core.utils.format import format_template

from app.downloads import youtube_source
from app.downloads.download_manager import DownloadTask, _RunnableSignals
from app.integrations.matcher import match_track
from app.integrations.models import PROVIDER_LABEL, ExternalTrack
from app.integrations.registry import client_for, provider_of
from app.models.index_db import IndexDB

log = logging.getLogger(__name__)

_AUDIO_EXTS = (".flac", ".m4a", ".mp3")
_MATCH_WORKERS = 4
_YT_WORKERS = 3
# Keep each tiddl invocation's argv well under Windows' 32k-char limit.
_BATCH = 200
_TRACK_NAME_TEMPLATE = "{item.artist} - {item.title_version}"


def _safe_segment(text: str) -> str:
    """Make *text* usable as a literal path segment inside a tiddl template."""
    text = text.replace("/", "-").replace("\\", "-")
    # Braces would be read as template fields by str.format.
    return text.replace("{", "{{").replace("}", "}}")


def collection_folder(download_path: Path, provider_label: str, title: str) -> Path:
    """On-disk folder tiddl creates for a matched playlist."""
    rel = format_template(
        f"{_safe_segment(provider_label)}/{_safe_segment(title)}",
        with_asterisk_ext=False,
    )
    return download_path / rel


class _ExternalRunnable(QRunnable):
    def __init__(self, task: DownloadTask) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self.task = task
        self.signals = _RunnableSignals()
        self._cancelled = False
        self._proc: Optional[subprocess.Popen] = None
        self._yt_procs: set[subprocess.Popen] = set()
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancelled = True
        self._cancel_event.set()
        for proc in [self._proc, *list(self._yt_procs)]:
            if proc is not None and proc.poll() is None:
                proc.terminate()

    def _log(self, line: str) -> None:
        self.signals.log_line.emit(self.task.id, line)

    # ── Run ──────────────────────────────────────────────────────────────────

    def run(self) -> None:  # noqa: D102
        if self._cancelled:
            self.signals.finished.emit(self.task.id)
            return
        try:
            self._run()
        except Exception as exc:
            log.exception("external download failed for %s", self.task.url)
            self.signals.failed.emit(self.task.id, str(exc))
            return
        self.signals.finished.emit(self.task.id)

    def _run(self) -> None:
        from app.api_client import build_api, is_authenticated

        provider = provider_of(self.task.url)
        assert provider is not None
        label = PROVIDER_LABEL[provider]

        self._log(f"\u25b6 Downloading from {label}: reading track list\u2026")
        contents = client_for(provider).get_contents(self.task.url)
        coll = contents.collection
        tracks = contents.tracks
        if not tracks:
            raise RuntimeError(f"{label} returned no tracks for '{coll.title}'")

        # Tidal (lossless) when the user has an account; YouTube Music for
        # everything Tidal can't provide.
        matches: list[Optional[Track]] = [None] * len(tracks)
        if is_authenticated():
            self._log(f"\u25b6 Downloading: matching {len(tracks)} tracks on Tidal\u2026")
            matches = self._match_all(build_api(), tracks)
            if self._cancelled:
                return
            self._log(
                f"Matched {sum(m is not None for m in matches)}/{len(tracks)} "
                f"tracks of '{coll.title}' on Tidal"
            )
        found = [m for m in matches if m is not None]
        download_path = Path(self.task.download_path)

        if coll.kind == "album" and found:
            # Download the Tidal album most matches belong to — gets the
            # full native album layout, cover and track numbering.
            album_id, _ = Counter(t.album.id for t in found).most_common(1)[0]
            self._run_tiddl([f"album/{album_id}"])
            self._record(coll.url, "album", coll.title, coll.creator, provider)
            return

        if coll.kind == "track":
            if found:
                self._run_tiddl([f"track/{found[0].id}"])
                return
            folder = collection_folder(download_path, label, "Singles")
            if not self._download_youtube(tracks, folder, coll.creator):
                raise RuntimeError(f"'{coll.title}' was not found on YouTube Music")
            return

        template = (
            f"{_safe_segment(label)}/{_safe_segment(coll.title)}/"
            f"{_TRACK_NAME_TEMPLATE}"
        )
        # Dedupe while keeping playlist order (a track can appear twice).
        unique_ids = list(dict.fromkeys(t.id for t in found))
        for i in range(0, len(unique_ids), _BATCH):
            if self._cancelled:
                return
            self._run_tiddl(
                [f"track/{tid}" for tid in unique_ids[i:i + _BATCH]],
                template=template,
            )

        folder = collection_folder(download_path, label, coll.title)
        yt_idx = [i for i, m in enumerate(matches) if m is None]
        album_artist = coll.creator if coll.kind == "album" else ""
        yt_paths = self._download_youtube(
            [tracks[i] for i in yt_idx], folder, album_artist
        )
        if self._cancelled:
            return
        yt_by_index = dict(zip(yt_idx, yt_paths))

        entries: list[tuple[Path, int, str]] = []
        missing: list[ExternalTrack] = []
        for i, (ext, match) in enumerate(zip(tracks, matches)):
            path = (
                self._find_audio(download_path, template, match)
                if match is not None else yt_by_index.get(i)
            )
            if path is None:
                missing.append(ext)
                continue
            entries.append((
                path, ext.duration_ms // 1000,
                f"{', '.join(ext.artists)} - {ext.title}",
            ))

        if not entries:
            raise RuntimeError(f"No tracks of '{coll.title}' could be downloaded")
        self._write_m3u(download_path, coll.title, entries)
        self._write_missing(folder, missing)
        self._record(coll.url, coll.kind, coll.title, coll.creator, provider)

    # ── YouTube Music fallback ───────────────────────────────────────────────

    def _download_youtube(
        self, tracks: list[ExternalTrack], folder: Path, album_artist: str
    ) -> list[Optional[Path]]:
        """Download *tracks* into *folder*; result aligned with *tracks*."""
        if not tracks:
            return []
        if youtube_source.ytdlp_path() is None:
            self._log(
                f"\u26a0 {len(tracks)} track(s) need yt-dlp (YouTube Music) — "
                "install it with `pip install yt-dlp` or `brew install yt-dlp`."
            )
            return [None] * len(tracks)
        self._log(
            f"\u25b6 Downloading {len(tracks)} track(s) from YouTube Music\u2026"
        )
        fmt = "flac" if self.task.audio_format == "flac" else "mp3"

        def one(ext: ExternalTrack) -> Optional[Path]:
            if self._cancelled:
                return None
            name = f"{ext.artists[0] if ext.artists else 'Unknown'} - {ext.title}"
            stem = folder / format_template(_safe_segment(name), with_asterisk_ext=False)
            for suffix in _AUDIO_EXTS:
                existing = stem.with_name(stem.name + suffix)
                if existing.exists():
                    return existing
            path = youtube_source.download_track(
                ext, stem, self.task.audio_format,
                album_artist=album_artist,
                cancel=self._cancel_event,
                on_proc=self._yt_procs.add,
            )
            if path is not None:
                self._log(f"Downloaded {name}  YouTube \u00b7 {fmt}")
            return path

        with ThreadPoolExecutor(max_workers=_YT_WORKERS) as pool:
            return list(pool.map(one, tracks))

    # ── Matching ─────────────────────────────────────────────────────────────

    def _match_all(self, api, tracks: list[ExternalTrack]) -> list[Optional[Track]]:
        def one(t: ExternalTrack) -> Optional[Track]:
            if self._cancelled:
                return None
            try:
                return match_track(api, t)
            except Exception as exc:
                log.warning("match failed for %s: %s", t.title, exc)
                return None

        with ThreadPoolExecutor(max_workers=_MATCH_WORKERS) as pool:
            return list(pool.map(one, tracks))

    # ── tiddl subprocess ─────────────────────────────────────────────────────

    def _run_tiddl(self, resources: list[str], template: str = "") -> None:
        tiddl_bin = shutil.which("tiddl") or "tiddl"
        cmd = [
            tiddl_bin, "download",
            "-q", self.task.quality,
            "-p", self.task.download_path,
        ]
        if self.task.audio_format != "original":
            cmd += ["-f", self.task.audio_format]
        if template:
            cmd += ["-o", template]
        cmd += ["url", *resources]

        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        try:
            for line in self._proc.stdout:
                if self._cancelled:
                    self._proc.terminate()
                    break
                line = line.rstrip()
                if line:
                    self._log(line)
        finally:
            self._proc.wait()
        if not self._cancelled and self._proc.returncode != 0:
            raise RuntimeError(f"tiddl exited with code {self._proc.returncode}")

    # ── Post-processing ──────────────────────────────────────────────────────

    @staticmethod
    def _find_audio(download_path: Path, template: str, track: Track) -> Optional[Path]:
        stem = format_template(template, item=track, with_asterisk_ext=False)
        base = download_path / stem
        for ext in _AUDIO_EXTS:
            p = base.with_name(base.name + ext)
            if p.exists():
                return p
        return None

    @staticmethod
    def _write_m3u(
        download_path: Path, title: str, entries: list[tuple[Path, int, str]]
    ) -> None:
        m3u_dir = download_path / "m3u"
        m3u_dir.mkdir(parents=True, exist_ok=True)
        name = format_template(_safe_segment(title), with_asterisk_ext=False)
        lines = ["#EXTM3U"]
        for path, duration, label in entries:
            lines.append(f"#EXTINF:{duration or -1},{label}")
            lines.append(os.path.relpath(path, m3u_dir))
        (m3u_dir / f"{name}.m3u").write_text("\n".join(lines) + "\n", encoding="utf-8")

    @staticmethod
    def _write_missing(folder: Path, missing: list[ExternalTrack]) -> None:
        report = folder / "_not_found.txt"
        if not missing:
            report.unlink(missing_ok=True)
            return
        folder.mkdir(parents=True, exist_ok=True)
        report.write_text(
            "\n".join(f"{', '.join(t.artists)} - {t.title}" for t in missing) + "\n",
            encoding="utf-8",
        )

    def _record(
        self, url: str, kind: str, title: str, creator: str, provider: str
    ) -> None:
        try:
            with IndexDB(self.task.download_path) as db:
                db.add_downloaded(
                    kind=kind, id=url, url=url, provider=provider,
                    title=title, creator=creator,
                )
        except Exception as exc:
            log.warning("IndexDB record failed for %s: %s", url, exc)
