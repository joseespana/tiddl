"""Download manager — concurrent task queue backed by QThreadPool."""
import logging
import shutil
import subprocess
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from app.worker_index import record_downloaded

log = logging.getLogger(__name__)


class DownloadStatus(Enum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class DownloadTask:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    url: str = ""
    title: str = ""
    download_path: str = ""
    quality: str = "max"
    status: DownloadStatus = DownloadStatus.QUEUED
    log_lines: List[str] = field(default_factory=list)
    error: str = ""
    # When True, pass -r (--rewrite-metadata) to tiddl so existing
    # audio files are kept but their tags are refreshed from the API.
    # Used by the "Sync Metadata" flow.
    rewrite_metadata: bool = False


class _RunnableSignals(QObject):
    """Carries signals for DownloadRunnable (QRunnable cannot have signals directly)."""
    log_line = Signal(str, str)   # task_id, line
    finished = Signal(str)        # task_id
    failed = Signal(str, str)     # task_id, error_msg


class _DownloadRunnable(QRunnable):
    """Runs ``tiddl download url`` for a single URL in the thread pool."""

    def __init__(self, task: DownloadTask) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self.task = task
        self.signals = _RunnableSignals()
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:  # noqa: D102 — called by QThreadPool
        if self._cancelled:
            self.signals.finished.emit(self.task.id)
            return

        tiddl_bin = shutil.which("tiddl") or "tiddl"
        cmd = [
            tiddl_bin, "download",
            "-q", self.task.quality,
            "-p", self.task.download_path,
        ]
        if self.task.rewrite_metadata:
            # Re-tag existing files without re-downloading audio.
            cmd.append("-r")
        cmd += ["url", self.task.url]
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
                if line:
                    self.signals.log_line.emit(self.task.id, line)
        except Exception as exc:
            log.error("Subprocess error for %s: %s", self.task.url, exc)
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

        if proc and proc.returncode == 0:
            record_downloaded(self.task.download_path, self.task.url)
            self.signals.finished.emit(self.task.id)
        else:
            rc = proc.returncode if proc else -1
            self.signals.failed.emit(self.task.id, f"tiddl exited with code {rc}")


class DownloadManager(QObject):
    """Manages a concurrent download queue backed by QThreadPool.

    The UI only needs to connect to :attr:`task_updated` and :attr:`all_done`.

    Signals:
        task_updated: Emitted whenever a task's status changes.
        log_line: Emitted for each log line from a running task (task_id, line).
        all_done: Emitted when the last queued task completes.
    """

    task_updated = Signal(object)      # DownloadTask
    log_line = Signal(str, str)        # task_id, line
    all_done = Signal()
    paused_changed = Signal(bool)      # True when paused, False when running

    MAX_CONCURRENT: int = 3

    def __init__(self, parent: QObject = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool()
        # We manage concurrency ourselves via ``_maybe_start_next`` so
        # pause/resume can hold a long queue without flooding the pool.
        # Keep the pool's max equal to MAX_CONCURRENT just as an extra
        # safety net.
        self._pool.setMaxThreadCount(self.MAX_CONCURRENT)
        self._tasks: Dict[str, DownloadTask] = {}
        self._runnables: Dict[str, _DownloadRunnable] = {}
        # Runnables built but not yet handed to the pool — drained by
        # _maybe_start_next as slots free up.
        self._pending: List[_DownloadRunnable] = []
        self._active: int = 0
        self._paused: bool = False

    # ── Public API ────────────────────────────────────────────────────────────

    def enqueue(
        self,
        urls: List[str],
        download_path: str,
        quality: str,
        rewrite_metadata: bool = False,
    ) -> List[str]:
        """Add *urls* to the queue and start up to MAX_CONCURRENT immediately.

        Args:
            urls: Tidal URLs to download.
            download_path: Destination folder.
            quality: Quality flag (``"max"``, ``"high"``, ``"normal"``, ``"low"``).
            rewrite_metadata: When True, passes ``-r`` to tiddl so each
                URL's existing audio files are kept and only their tags
                get refreshed. Used by the Sync Metadata flow.

        Returns:
            List of task IDs in the same order as *urls*.
        """
        ids = []
        for url in urls:
            task = DownloadTask(
                url=url,
                download_path=download_path,
                quality=quality,
                rewrite_metadata=rewrite_metadata,
            )
            self._tasks[task.id] = task
            ids.append(task.id)
            self._start_runnable(task)
        return ids

    def cancel_all(self) -> None:
        """Terminate every in-flight subprocess and drop every queued task."""
        # Drop the pending queue first — those haven't been started yet.
        self._pending.clear()
        for runnable in self._runnables.values():
            runnable.cancel()
        for task in self._tasks.values():
            if task.status not in (DownloadStatus.DONE, DownloadStatus.FAILED):
                task.status = DownloadStatus.CANCELLED
                self.task_updated.emit(task)
        self._runnables.clear()
        self._active = 0
        # A cancel also un-pauses so the next enqueue works normally.
        if self._paused:
            self._paused = False
            self.paused_changed.emit(False)
        self._check_all_done()

    def pause(self) -> None:
        """Stop starting new tasks; let the running ones finish naturally.

        Queued items stay in ``_pending``; call :meth:`resume` to drain
        them into the pool once the user wants to continue.
        """
        if self._paused:
            return
        self._paused = True
        self.paused_changed.emit(True)

    def resume(self) -> None:
        """Resume starting queued tasks after a :meth:`pause`."""
        if not self._paused:
            return
        self._paused = False
        self.paused_changed.emit(False)
        self._maybe_start_next()

    def is_paused(self) -> bool:
        """Return True when the manager is currently paused."""
        return self._paused

    def has_work(self) -> bool:
        """Return True while at least one task is running or queued."""
        return self._active > 0 or bool(self._pending)

    def get_tasks(self) -> List[DownloadTask]:
        return list(self._tasks.values())

    def clear(self) -> None:
        """Remove all finished tasks from the internal registry."""
        done = [tid for tid, t in self._tasks.items()
                if t.status in (DownloadStatus.DONE, DownloadStatus.FAILED, DownloadStatus.CANCELLED)]
        for tid in done:
            self._tasks.pop(tid, None)
            self._runnables.pop(tid, None)

    # ── Internal ─────────────────────────────────────────────────────────────

    def _start_runnable(self, task: DownloadTask) -> None:
        """Build a runnable for *task*, park it pending, then pump the queue."""
        runnable = _DownloadRunnable(task)
        runnable.signals.log_line.connect(self._on_log_line)
        runnable.signals.finished.connect(self._on_finished)
        runnable.signals.failed.connect(self._on_failed)
        self._pending.append(runnable)
        self._maybe_start_next()

    def _maybe_start_next(self) -> None:
        """Submit pending runnables until we hit the concurrency cap."""
        if self._paused:
            return
        while self._active < self.MAX_CONCURRENT and self._pending:
            runnable = self._pending.pop(0)
            task = runnable.task
            self._runnables[task.id] = runnable
            task.status = DownloadStatus.DOWNLOADING
            self.task_updated.emit(task)
            self._active += 1
            self._pool.start(runnable)

    def _on_log_line(self, task_id: str, line: str) -> None:
        task = self._tasks.get(task_id)
        if task:
            task.log_lines.append(line)
        self.log_line.emit(task_id, line)

    def _on_finished(self, task_id: str) -> None:
        task = self._tasks.get(task_id)
        if task and task.status != DownloadStatus.CANCELLED:
            task.status = DownloadStatus.DONE
            self.task_updated.emit(task)
        self._runnables.pop(task_id, None)
        self._active = max(0, self._active - 1)
        self._maybe_start_next()
        self._check_all_done()

    def _on_failed(self, task_id: str, error: str) -> None:
        task = self._tasks.get(task_id)
        if task:
            # Surface the error on the log-line signal so the presenter
            # (and anyone else hooked up) can react. Keep it to a single
            # compact line — the view now flashes this on the status card.
            desc = (task.title or task.url or task.id)[:80]
            self.log_line.emit(task_id, f"\u26a0 Failed: {desc}: {error}")
            task.status = DownloadStatus.FAILED
            task.error = error
            self.task_updated.emit(task)
        self._runnables.pop(task_id, None)
        self._active = max(0, self._active - 1)
        self._maybe_start_next()
        log.error("Download failed for task %s: %s", task_id, error)
        self._check_all_done()

    def _check_all_done(self) -> None:
        # "Done" means neither running nor waiting. Pausing does not count
        # as done: tasks held in _pending are still our responsibility.
        if self._active == 0 and not self._pending:
            unfinished = [
                t for t in self._tasks.values()
                if t.status in (DownloadStatus.QUEUED, DownloadStatus.DOWNLOADING)
            ]
            if not unfinished:
                self.all_done.emit()
