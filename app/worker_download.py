"""
DownloadWorker — runs ``tiddl download url`` for each selected resource URL.

Uses the QObject + moveToThread pattern instead of QThread subclassing.
"""
import logging
import shutil
import subprocess

from PySide6.QtCore import QObject, Signal

from app.worker_index import record_downloaded

log = logging.getLogger(__name__)


class DownloadWorker(QObject):
    """Runs the tiddl CLI for each URL in sequence and streams log output.

    Signals:
        log_line: Emitted for each non-empty output line from the CLI.
        progress: Emitted after each URL completes as ``(done, total)``.
        finished: Emitted when all URLs have been processed.
        error: Emitted with an error message string on subprocess failure.
    """

    log_line = Signal(str)
    progress = Signal(int, int)
    finished = Signal()
    error = Signal(str)

    def __init__(self, urls: list[str], download_path: str, quality: str) -> None:
        """Initialise the worker.

        Args:
            urls: List of Tidal URLs to download.
            download_path: Destination directory passed to the CLI.
            quality: Quality flag value (e.g. ``"max"``, ``"high"``).
        """
        super().__init__()
        self.urls = urls
        self.download_path = download_path
        self.quality = quality
        self._interrupted = False

    def interrupt(self) -> None:
        """Request the worker to stop at the next iteration checkpoint."""
        self._interrupted = True

    def run(self) -> None:
        """Download each URL in sequence, streaming log lines.

        Called by the owning QThread via ``thread.started`` signal.
        Always emits :attr:`finished` before returning.
        """
        tiddl_bin = shutil.which("tiddl") or "tiddl"
        total = len(self.urls)

        for idx, url in enumerate(self.urls, 1):
            if self._interrupted:
                break

            self.log_line.emit(f"\n▶ Downloading {url}")
            cmd = [
                tiddl_bin,
                "download",
                "-q", self.quality,
                "-p", self.download_path,
                "url", url,
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
                    if self._interrupted:
                        proc.terminate()
                        break
                    line = line.rstrip()
                    if line:
                        self.log_line.emit(line)
            except Exception as exc:
                log.error("Subprocess error for %s: %s", url, exc)
                if proc is not None:
                    proc.wait()
                self.error.emit(str(exc))
                self.finished.emit()
                return
            finally:
                if proc is not None:
                    proc.wait()

            # Record UUID in local index so badge detection works after title changes
            if proc and proc.returncode == 0:
                record_downloaded(self.download_path, url)

            self.progress.emit(idx, total)

        self.finished.emit()
