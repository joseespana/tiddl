"""Main presenter — orchestrates view, workers and download manager."""
import logging
from typing import Optional

from PySide6.QtCore import QObject, QThread

from app.views.main_view import MainView
from app.worker_library import LibraryWorker
from app.worker_downloaded import DownloadedWorker
from app.worker_search import SearchWorker
from app.models.disk_cache import DiskCache
from app.downloads.download_manager import DownloadManager, DownloadStatus
from app.api_client import build_api

log = logging.getLogger(__name__)


class MainPresenter(QObject):
    """Presenter for the main window.

    Owns the worker lifecycle, the DownloadManager, and all
    coordination between the view and the Tidal API.

    Args:
        view: The MainView instance to drive.
    """

    def __init__(self, view: MainView) -> None:
        super().__init__()
        self._view = view
        self._api = build_api()
        self._current_tab = "playlists"
        self._disk_cache: Optional[DiskCache] = None

        # Library/search worker pairs (worker + thread)
        self._lib_worker: Optional[LibraryWorker] = None
        self._lib_thread: Optional[QThread] = None
        self._downloaded_worker: Optional[DownloadedWorker] = None
        self._downloaded_thread: Optional[QThread] = None
        self._search_worker: Optional[SearchWorker] = None
        self._search_thread: Optional[QThread] = None

        # Threads that have been asked to stop but haven't finished yet.
        # We keep Python references here so the C++ QThread isn't destroyed
        # while still running (which triggers an abort).
        self._stopping_threads: list = []

        # Download manager shared for the whole session
        self._dl_manager = DownloadManager(self)

        self._connect_view()
        self._connect_download_manager()
        self._rebuild_cache()
        self.load_tab("playlists")

    # ── Signal wiring ─────────────────────────────────────────────────────────

    def _connect_view(self) -> None:
        self._view.tab_requested.connect(self.load_tab)
        self._view.tidal_search_requested.connect(self._run_tidal_search)
        self._view.download_selected_requested.connect(self._start_download)
        self._view.download_url_requested.connect(self._download_url)
        self._view.logout_requested.connect(self._logout)
        self._view.browse_requested.connect(self._browse_folder)
        self._view.path_changed.connect(self._on_path_changed)
        self._view.filter_changed.connect(self._filter_list)
        self._view.select_all_toggled.connect(self._toggle_select_all)

    def _connect_download_manager(self) -> None:
        self._dl_manager.log_line.connect(self._on_dl_log_line)
        self._dl_manager.task_updated.connect(self._on_task_updated)
        self._dl_manager.all_done.connect(self._on_all_downloads_done)

    # ── Thread-safe item / log slots ──────────────────────────────────────────
    # These are proper QObject methods so Qt uses QueuedConnection when the
    # emitting worker lives in a different thread, keeping all widget access
    # on the main thread.

    def _on_item_ready(self, item: object) -> None:
        """Deliver one library item to the view (always runs in main thread)."""
        self._view.add_item(item, self._disk_cache)

    def _on_dl_log_line(self, _task_id: str, line: str) -> None:
        """Forward a download log line to the view."""
        self._view.append_log(line)

    # ── Worker lifecycle helpers ──────────────────────────────────────────────

    def _start_worker(self, worker: QObject, thread: QThread) -> None:
        """Wire *worker* to *thread* using the QObject + moveToThread pattern."""
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _stop_worker(self, worker, thread) -> None:
        """Request interruption of *worker* and park *thread* in the zombie list.

        We keep a Python reference to every stopping thread in
        ``_stopping_threads`` so the C++ QThread isn't destroyed while still
        running.  The reference is released when the thread emits ``finished``.
        """
        try:
            if worker is not None:
                worker.interrupt()
        except RuntimeError:
            pass
        if thread is None:
            return
        try:
            if not thread.isRunning():
                return
        except RuntimeError:
            return
        # Park the thread so Python won't GC it before the C++ side exits.
        self._stopping_threads.append(thread)
        try:
            thread.finished.connect(
                lambda t=thread: self._stopping_threads.remove(t)
                if t in self._stopping_threads else None
            )
            thread.quit()
        except RuntimeError:
            try:
                self._stopping_threads.remove(thread)
            except ValueError:
                pass

    # ── Tab loading ───────────────────────────────────────────────────────────

    def load_tab(self, tab: str) -> None:
        """Switch to *tab*, stopping any in-flight workers first.

        Args:
            tab: Sidebar key (``"playlists"``, ``"albums"``, ``"artists"``,
                ``"downloaded"``, or ``"search"``).
        """
        if tab == self._current_tab and self._view.item_widgets:
            return

        self._stop_worker(self._lib_worker, self._lib_thread)
        self._stop_worker(self._downloaded_worker, self._downloaded_thread)

        self._current_tab = tab
        titles = {"search": "Search Tidal", "downloaded": "Downloaded"}
        self._view.set_tab_active(tab)
        self._view.set_tab_title(titles.get(tab, tab.capitalize()))
        self._view.clear_list()
        self._view.show_search_panel(tab == "search")

        if tab == "search":
            self._view.set_loading_text("Enter a search term and press Search.")
            self._view.focus_tidal_search()
            return

        if tab == "downloaded":
            worker = DownloadedWorker(self._api, self._view.get_download_path())
            thread = QThread()
            worker.item_ready.connect(self._on_item_ready)
            worker.finished.connect(self._on_library_loaded)
            worker.error.connect(self._on_library_error)
            self._downloaded_worker = worker
            self._downloaded_thread = thread
            self._start_worker(worker, thread)
            return

        worker = LibraryWorker(self._api, tab)
        thread = QThread()
        worker.item_ready.connect(self._on_item_ready)
        worker.finished.connect(self._on_library_loaded)
        worker.error.connect(self._on_library_error)
        self._lib_worker = worker
        self._lib_thread = thread
        self._start_worker(worker, thread)

    # ── Search ────────────────────────────────────────────────────────────────

    def _run_tidal_search(self, query: str, search_type: str) -> None:
        self._stop_worker(self._search_worker, self._search_thread)
        self._view.clear_list()
        self._view.set_loading_text(f'Searching "{query}"…')
        worker = SearchWorker(self._api, query, search_type)
        thread = QThread()
        worker.item_ready.connect(self._on_item_ready)
        worker.finished.connect(self._on_library_loaded)
        worker.error.connect(self._on_library_error)
        self._search_worker = worker
        self._search_thread = thread
        self._start_worker(worker, thread)

    # ── Downloads ─────────────────────────────────────────────────────────────

    def _start_download(self) -> None:
        """Download all visible, checked items via the DownloadManager."""
        urls = self._view.get_checked_urls()
        no_url_names = self._view.get_checked_items_without_url()
        if no_url_names:
            self._view.append_log(f"⚠ Skipped (no URL): {', '.join(no_url_names)}")
            self._view.show_log()
        if not urls:
            self._view.append_log("⚠ No items selected.")
            self._view.show_log()
            return
        self._dl_manager.clear()
        self._view.show_progress_bar(len(urls))
        self._view.show_log()
        self._view.set_download_btn_enabled(False)
        self._view.set_download_btn_text(f"Downloading… (0/{len(urls)})")
        self._dl_manager.enqueue(
            urls, self._view.get_download_path(), self._view.get_quality()
        )

    def _download_url(self, url: str) -> None:
        """Download a single direct URL via the DownloadManager.

        Args:
            url: Tidal URL entered in the direct-URL field.
        """
        if not url:
            return
        self._dl_manager.clear()
        self._view.show_progress_bar(1)
        self._view.show_log()
        self._view.set_download_btn_enabled(False)
        self._dl_manager.enqueue(
            [url], self._view.get_download_path(), self._view.get_quality()
        )

    def _on_task_updated(self, task) -> None:
        tasks = self._dl_manager.get_tasks()
        total = len(tasks)
        done = sum(
            1
            for t in tasks
            if t.status in (
                DownloadStatus.DONE,
                DownloadStatus.FAILED,
                DownloadStatus.CANCELLED,
            )
        )
        self._view.set_download_progress(done, total)
        self._view.set_download_btn_text(f"Downloading… ({done}/{total})")

    def _on_all_downloads_done(self) -> None:
        self._view.set_download_btn_enabled(True)
        self._view.update_select_btn()
        self._view.append_log("✓ All downloads complete.")
        self._rebuild_cache()

    # ── Library worker callbacks ──────────────────────────────────────────────

    def _on_library_loaded(self) -> None:
        if not self._view.item_widgets:
            msgs = {
                "search": "No results found.",
                "downloaded": (
                    "No downloads recorded yet.\n"
                    "Future downloads will appear here automatically."
                ),
            }
            msg = msgs.get(
                self._current_tab,
                f"No {self._current_tab} in your favorites.",
            )
            self._view.set_loading_text(msg)

    def _on_library_error(self, msg: str) -> None:
        self._view.set_loading_text(f"⚠ {msg}")

    # ── Filter & selection ────────────────────────────────────────────────────

    def _filter_list(self, text: str) -> None:
        q = text.strip().lower()
        for w in self._view.item_widgets:
            visible = not q or q in w._title_cache or q in w._sub_cache
            w.setVisible(visible)
            if w._sep:
                w._sep.setVisible(visible)
        self._view.update_select_btn()

    def _toggle_select_all(self, checked: bool) -> None:
        for w in self._view.item_widgets:
            if w.isVisible():
                w.checkbox.setChecked(checked)
        self._view.update_select_btn()

    # ── Cache & path ──────────────────────────────────────────────────────────

    def _rebuild_cache(self) -> None:
        self._disk_cache = DiskCache(self._view.get_download_path())
        self._view.refresh_badges(self._disk_cache)

    def _on_path_changed(self, _: str) -> None:
        self._rebuild_cache()

    # ── Folder browse ─────────────────────────────────────────────────────────

    def _browse_folder(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        folder = QFileDialog.getExistingDirectory(
            self._view,
            "Select Download Folder",
            self._view.get_download_path(),
        )
        if folder:
            self._view.set_download_path(folder)

    # ── Logout ────────────────────────────────────────────────────────────────

    def _logout(self) -> None:
        from app.auth_window import AuthWindow

        dlg = AuthWindow(self._view)
        if dlg.exec():
            self._api = build_api()
            # Force reload of current tab by clearing the sentinel
            saved_tab = self._current_tab
            self._current_tab = ""
            self.load_tab(saved_tab)

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def on_close(self) -> None:
        """Interrupt all running workers and cancel downloads.

        Called by MainWindow.closeEvent before delegating to super().
        """
        for worker, thread in [
            (self._lib_worker, self._lib_thread),
            (self._downloaded_worker, self._downloaded_thread),
            (self._search_worker, self._search_thread),
        ]:
            try:
                if worker:
                    worker.interrupt()
            except RuntimeError:
                pass
            try:
                if thread and thread.isRunning():
                    thread.quit()
                    thread.wait(1500)
            except RuntimeError:
                pass
        self._dl_manager.cancel_all()
