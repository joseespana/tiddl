"""SoundCloud download runnable — yt-dlp subprocess + m3u + index entry.

Mirrors the on-disk layout used by tiddl for Tidal so the existing
``DiskCache`` and Downloaded-tab plumbing pick the result up unchanged:

    {download_path}/
      {uploader}/
        {playlist_title}/
          01. Track One.mp3
          02. Track Two.mp3
      m3u/
        {playlist_title}.m3u
      .tiddl_index.db   (row with provider='soundcloud')

Single-track URLs land in ``{uploader}/Singles/{title}.mp3`` and skip
the m3u step.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QRunnable

from app.downloads.download_manager import DownloadTask, _RunnableSignals
from app.models.index_db import IndexDB

log = logging.getLogger(__name__)

# Sentinels emitted via yt-dlp's --print flag so we can parse track events
# out of the otherwise-noisy stdout stream.
_FILE_TAG = "TIDDL_SC_FILE|"
_PL_TAG = "TIDDL_SC_PL|"


def is_soundcloud_url(url: str) -> bool:
    """Return True when *url* points at soundcloud.com."""
    return "soundcloud.com" in url.lower()


def _is_playlist_url(url: str) -> bool:
    """SoundCloud playlists/sets always live under ``/sets/``."""
    u = url.lower()
    return "soundcloud.com" in u and "/sets/" in u


def _sanitize_segment(s: str) -> str:
    """Match yt-dlp's ``--restrict-filenames``-free behaviour.

    yt-dlp by default replaces ``/`` and other unsafe characters in the
    output template — we mirror that here so the m3u writer can find
    the same folder yt-dlp produced.
    """
    # Replace path separators and a few characters yt-dlp also replaces
    # by default to keep the folder name in sync.
    return re.sub(r'[\\/:"*?<>|]+', "_", s).strip().rstrip(".")


class _SoundCloudRunnable(QRunnable):
    """Run ``yt-dlp`` for a SoundCloud URL inside the worker pool.

    Produces the same per-track ``Downloaded ...`` log lines the Tidal
    runnable emits, so :class:`MainPresenter._on_dl_log_line` updates
    the status card without changes.
    """

    def __init__(self, task: DownloadTask) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self.task = task
        self.signals = _RunnableSignals()
        self._cancelled = False

        # Captured from yt-dlp stdout — we use these to write the m3u
        # and the IndexDB row after the process exits cleanly.
        self._playlist_title: Optional[str] = None
        self._uploader: Optional[str] = None
        self._track_files: list[str] = []

    def cancel(self) -> None:
        self._cancelled = True

    # ── Run ──────────────────────────────────────────────────────────────────

    def run(self) -> None:  # noqa: D102
        if self._cancelled:
            self.signals.finished.emit(self.task.id)
            return

        ytdlp = shutil.which("yt-dlp")
        if ytdlp is None:
            self.signals.failed.emit(
                self.task.id,
                "yt-dlp is not installed. Install it with "
                "`pip install yt-dlp` or `brew install yt-dlp`.",
            )
            return

        download_path = Path(self.task.download_path)
        download_path.mkdir(parents=True, exist_ok=True)

        is_pl = _is_playlist_url(self.task.url)
        if is_pl:
            out_tpl = (
                "%(uploader)s/%(playlist)s/"
                "%(playlist_index)02d. %(title)s.%(ext)s"
            )
        else:
            out_tpl = "%(uploader)s/Singles/%(title)s.%(ext)s"

        cmd = [
            ytdlp,
            "--no-overwrites",
            "--no-progress",
            "--newline",
            "--ignore-errors",
            # Audio extraction
            "--extract-audio",
            "--audio-format", "mp3",
            "--audio-quality", "0",
            "--embed-thumbnail",
            "--add-metadata",
            # Layout
            "--paths", str(download_path),
            "--output", out_tpl,
            # Resume across runs without redownloading
            "--download-archive", str(download_path / ".sc_archive.txt"),
            # Machine-readable markers we parse below
            "--print", f"playlist:{_PL_TAG}%(playlist|)s|%(uploader|)s",
            "--print", f"after_video:{_FILE_TAG}%(title)s|%(filepath)s",
            "--",
            self.task.url,
        ]

        proc = None
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            for line in proc.stdout:
                if self._cancelled:
                    proc.terminate()
                    break
                line = line.rstrip()
                if not line:
                    continue
                self._handle_line(line)
        except Exception as exc:
            log.error("yt-dlp subprocess error for %s: %s", self.task.url, exc)
            if proc is not None:
                proc.wait()
            self.signals.failed.emit(self.task.id, str(exc))
            return
        finally:
            if proc is not None:
                proc.wait()

        if self._cancelled:
            self.signals.finished.emit(self.task.id)
            return

        rc = proc.returncode if proc else -1
        if rc != 0 and not self._track_files:
            self.signals.failed.emit(
                self.task.id, f"yt-dlp exited with code {rc}",
            )
            return

        # ── Post-processing: m3u + IndexDB ──────────────────────────────────
        try:
            if is_pl:
                self._write_m3u(download_path)
                self._record_in_index(download_path)
        except Exception as exc:
            # Don't fail the task over a m3u/index hiccup — files are
            # already on disk. Just surface a warning line.
            log.warning("post-processing for %s failed: %s", self.task.url, exc)
            self.signals.log_line.emit(
                self.task.id, f"⚠ Post-process: {exc}",
            )

        self.signals.finished.emit(self.task.id)

    # ── Stdout parsing ───────────────────────────────────────────────────────

    def _handle_line(self, line: str) -> None:
        """Forward raw lines to the manager + update SC-specific state."""
        if line.startswith(_PL_TAG):
            payload = line[len(_PL_TAG):]
            parts = payload.split("|", 1)
            self._playlist_title = (parts[0] or "").strip() or None
            if len(parts) > 1:
                self._uploader = (parts[1] or "").strip() or None
            return

        if line.startswith(_FILE_TAG):
            payload = line[len(_FILE_TAG):]
            parts = payload.split("|", 1)
            title = (parts[0] or "").strip()
            filepath = (parts[1] or "").strip() if len(parts) > 1 else ""
            if filepath:
                self._track_files.append(filepath)
            # Emit a "Downloaded ..." line in the same shape tiddl uses
            # so MainPresenter._on_dl_log_line increments the counter.
            self.signals.log_line.emit(
                self.task.id, f"Downloaded {title}  mp3",
            )
            return

        # Non-marker lines: still surface to the manager log so the user
        # sees yt-dlp progress in the debug log if they enable it.
        self.signals.log_line.emit(self.task.id, line)

    # ── m3u writer ───────────────────────────────────────────────────────────

    def _write_m3u(self, download_path: Path) -> None:
        """Write ``m3u/{playlist}.m3u`` referencing every downloaded track.

        Only runs for playlist URLs. The file uses paths relative to the
        m3u folder so it's portable along with the download tree.
        """
        if not self._playlist_title or not self._uploader:
            return
        if not self._track_files:
            return

        m3u_dir = download_path / "m3u"
        m3u_dir.mkdir(parents=True, exist_ok=True)
        m3u_path = m3u_dir / f"{_sanitize_segment(self._playlist_title)}.m3u"

        # Sort by leading "NN. " prefix so playlist order is preserved.
        files_sorted = sorted(self._track_files, key=lambda p: Path(p).name)
        try:
            with m3u_path.open("w", encoding="utf-8") as f:
                f.write("#EXTM3U\n")
                for fp in files_sorted:
                    p = Path(fp)
                    if not p.exists():
                        continue
                    rel = self._relative_to(p, m3u_dir)
                    title = p.stem
                    # No reliable duration without re-reading tags; use -1.
                    f.write(f"#EXTINF:-1,{title}\n{rel}\n")
        except Exception as exc:
            log.warning("failed to write %s: %s", m3u_path, exc)

    @staticmethod
    def _relative_to(track_path: Path, m3u_dir: Path) -> str:
        """Return ``track_path`` written relative to ``m3u_dir``.

        Falls back to the absolute path on cross-volume layouts.
        """
        try:
            import os
            return os.path.relpath(track_path, m3u_dir)
        except Exception:
            return str(track_path)

    # ── Index ────────────────────────────────────────────────────────────────

    def _record_in_index(self, download_path: Path) -> None:
        """Insert a SoundCloud playlist entry in the local IndexDB.

        Uses the source URL as the row id so the Downloaded-tab worker
        can rebuild a synthetic card from local files alone (no API
        round-trip). ``provider='soundcloud'`` is what triggers that
        local-only path.
        """
        if not self._playlist_title:
            return
        try:
            with IndexDB(str(download_path)) as db:
                db.add_downloaded(
                    kind="playlist",
                    id=self.task.url,
                    url=self.task.url,
                    provider="soundcloud",
                    title=self._playlist_title,
                    creator=self._uploader or "SoundCloud",
                )
        except Exception as exc:
            log.warning("IndexDB record failed for %s: %s", self.task.url, exc)
