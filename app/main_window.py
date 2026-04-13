"""
Main application window.
"""
from pathlib import Path

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QFont, QIcon, QPixmap, QColor
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QLabel, QPushButton, QListWidget, QListWidgetItem,
    QFileDialog, QComboBox, QProgressBar, QTextEdit,
    QSplitter, QFrame, QCheckBox, QScrollArea,
    QAbstractItemView, QSizePolicy, QLineEdit,
)
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PySide6.QtCore import QUrl

from app.api_client import build_api, is_authenticated
from app.workers import LibraryWorker, DownloadWorker


QUALITY_OPTIONS = [
    ("Max (up to 24-bit/192kHz FLAC)", "max"),
    ("High (16-bit/44.1kHz FLAC)", "high"),
    ("Normal (320 kbps M4A)", "normal"),
    ("Low (96 kbps M4A)", "low"),
]

SIDEBAR_TABS = [
    ("Playlists", "playlists"),
    ("Albums", "albums"),
    ("Artists", "artists"),
]

ITEM_HEIGHT = 64


class CoverLabel(QLabel):
    """Loads a cover image from URL asynchronously."""

    _manager: QNetworkAccessManager | None = None

    def __init__(self, url: str | None, size: int = 52, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setStyleSheet("background: #222; border-radius: 4px;")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

        if url:
            full_url = f"https://resources.tidal.com/images/{url.replace('-', '/')}/320x320.jpg"
            if CoverLabel._manager is None:
                CoverLabel._manager = QNetworkAccessManager()
            req = QNetworkAccessManager()
            reply = req.get(QNetworkRequest(QUrl(full_url)))
            reply.finished.connect(lambda: self._on_image(reply, size))
            self._reply = reply
            self._req_mgr = req

    def _on_image(self, reply, size):
        data = reply.readAll()
        pm = QPixmap()
        if pm.loadFromData(data):
            self.setPixmap(pm.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                     Qt.TransformationMode.SmoothTransformation))


class LibraryItemWidget(QWidget):
    """One row in the library list."""

    def __init__(self, item_data, parent=None):
        super().__init__(parent)
        self.item_data = item_data
        self.setFixedHeight(ITEM_HEIGHT)

        row = QHBoxLayout(self)
        row.setContentsMargins(8, 6, 8, 6)
        row.setSpacing(12)

        self.checkbox = QCheckBox()
        self.checkbox.setFixedWidth(20)
        row.addWidget(self.checkbox)

        cover_url = self._get_cover(item_data)
        self.cover = CoverLabel(cover_url, size=52)
        row.addWidget(self.cover)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)

        self.title_label = QLabel(self._get_title(item_data))
        self.title_label.setStyleSheet("font-weight: bold; font-size: 13px;")
        self.title_label.setWordWrap(False)
        text_col.addWidget(self.title_label)

        self.sub_label = QLabel(self._get_subtitle(item_data))
        self.sub_label.setStyleSheet("color: #888; font-size: 11px;")
        text_col.addWidget(self.sub_label)

        row.addLayout(text_col, 1)

    def is_checked(self):
        return self.checkbox.isChecked()

    def get_url(self) -> str:
        d = self.item_data
        if hasattr(d, "uuid"):
            return f"https://tidal.com/playlist/{d.uuid}"
        elif hasattr(d, "url") and d.url:
            return d.url
        return ""

    def get_label(self) -> str:
        return self._get_title(self.item_data)

    @staticmethod
    def _get_cover(d) -> str | None:
        if hasattr(d, "squareImage") and d.squareImage:
            return d.squareImage
        if hasattr(d, "cover") and d.cover:
            return d.cover
        if hasattr(d, "picture") and d.picture:
            return d.picture
        return None

    @staticmethod
    def _get_title(d) -> str:
        return getattr(d, "title", getattr(d, "name", "Unknown"))

    @staticmethod
    def _get_subtitle(d) -> str:
        if hasattr(d, "numberOfTracks"):
            artist = ""
            if hasattr(d, "artist") and d.artist:
                artist = f"{d.artist.name} · "
            return f"{artist}{d.numberOfTracks} tracks"
        if hasattr(d, "artistTypes"):
            pop = getattr(d, "popularity", None)
            return f"Popularity: {pop}" if pop else "Artist"
        return ""


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("tiddl")
        self.setMinimumSize(900, 620)
        self.resize(1100, 700)

        self.api = build_api()
        self._library_worker: LibraryWorker | None = None
        self._download_worker: DownloadWorker | None = None
        self._current_tab = "playlists"
        self._item_widgets: list[LibraryItemWidget] = []

        self._build_ui()
        self._apply_dark_theme()
        self._load_tab("playlists")

    # ── UI Construction ──────────────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Sidebar ──────────────────────────────────────────────────────────
        sidebar = QWidget()
        sidebar.setFixedWidth(190)
        sidebar.setStyleSheet("background: #111;")
        sb_layout = QVBoxLayout(sidebar)
        sb_layout.setContentsMargins(12, 20, 12, 20)
        sb_layout.setSpacing(4)

        logo = QLabel("tiddl")
        font = QFont()
        font.setPointSize(22)
        font.setBold(True)
        logo.setFont(font)
        logo.setStyleSheet("color: #0ff; padding-bottom: 12px;")
        sb_layout.addWidget(logo)

        self._tab_buttons: dict[str, QPushButton] = {}
        for label, key in SIDEBAR_TABS:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setStyleSheet(self._tab_btn_style())
            btn.clicked.connect(lambda checked, k=key: self._load_tab(k))
            sb_layout.addWidget(btn)
            self._tab_buttons[key] = btn

        sb_layout.addStretch()

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #333;")
        sb_layout.addWidget(sep)

        logout_btn = QPushButton("Logout")
        logout_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; "
            "color: #666; text-align: left; padding: 6px 8px; }"
            "QPushButton:hover { color: #f66; }"
        )
        logout_btn.clicked.connect(self._logout)
        sb_layout.addWidget(logout_btn)

        root.addWidget(sidebar)

        # ── Content Area ─────────────────────────────────────────────────────
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        # Top bar
        top_bar = QWidget()
        top_bar.setStyleSheet("background: #181818; border-bottom: 1px solid #2a2a2a;")
        top_bar.setFixedHeight(52)
        top_bar_layout = QHBoxLayout(top_bar)
        top_bar_layout.setContentsMargins(16, 0, 16, 0)

        self._tab_title = QLabel("Playlists")
        font2 = QFont()
        font2.setPointSize(15)
        font2.setBold(True)
        self._tab_title.setFont(font2)
        top_bar_layout.addWidget(self._tab_title)

        top_bar_layout.addStretch()

        self._select_all_btn = QPushButton("Select All")
        self._select_all_btn.setStyleSheet(
            "QPushButton { background: transparent; border: 1px solid #444; "
            "border-radius: 4px; padding: 4px 12px; color: #aaa; font-size: 12px; }"
            "QPushButton:hover { border-color: #0ff; color: #0ff; }"
        )
        self._select_all_btn.clicked.connect(self._select_all)
        top_bar_layout.addWidget(self._select_all_btn)

        self._deselect_btn = QPushButton("Deselect All")
        self._deselect_btn.setStyleSheet(self._select_all_btn.styleSheet())
        self._deselect_btn.clicked.connect(self._deselect_all)
        top_bar_layout.addWidget(self._deselect_btn)

        content_layout.addWidget(top_bar)

        # Library list
        self._list_container = QWidget()
        self._list_container.setStyleSheet("background: #1a1a1a;")
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(0)

        self._loading_label = QLabel("Loading…")
        self._loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loading_label.setStyleSheet("color: #666; font-size: 14px; padding: 40px;")
        self._list_layout.addWidget(self._loading_label)
        self._list_layout.addStretch()
        self._loading_label_in_layout = True

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._list_container)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        content_layout.addWidget(scroll, 1)

        # ── Bottom Panel ─────────────────────────────────────────────────────
        bottom = QWidget()
        bottom.setStyleSheet("background: #141414; border-top: 1px solid #2a2a2a;")
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(16, 10, 16, 10)
        bottom_layout.setSpacing(8)

        # Download controls row
        controls_row = QHBoxLayout()
        controls_row.setSpacing(10)

        folder_label = QLabel("Save to:")
        folder_label.setStyleSheet("color: #888; font-size: 12px;")
        controls_row.addWidget(folder_label)

        self._path_edit = QLineEdit(str(Path.home() / "Music" / "tiddl"))
        self._path_edit.setStyleSheet(
            "background: #222; border: 1px solid #333; border-radius: 4px; "
            "padding: 4px 8px; color: #ccc; font-size: 12px;"
        )
        self._path_edit.setMinimumWidth(200)
        controls_row.addWidget(self._path_edit, 1)

        browse_btn = QPushButton("Browse…")
        browse_btn.setStyleSheet(
            "QPushButton { background: #222; border: 1px solid #444; border-radius: 4px; "
            "padding: 4px 12px; color: #aaa; font-size: 12px; }"
            "QPushButton:hover { border-color: #0ff; color: #0ff; }"
        )
        browse_btn.clicked.connect(self._browse_folder)
        controls_row.addWidget(browse_btn)

        self._quality_combo = QComboBox()
        for label, val in QUALITY_OPTIONS:
            self._quality_combo.addItem(label, userData=val)
        self._quality_combo.setStyleSheet(
            "QComboBox { background: #222; border: 1px solid #444; border-radius: 4px; "
            "padding: 4px 8px; color: #ccc; font-size: 12px; min-width: 200px; }"
        )
        controls_row.addWidget(self._quality_combo)

        self._download_btn = QPushButton("Download Selected")
        self._download_btn.setMinimumHeight(36)
        self._download_btn.setStyleSheet(
            "QPushButton { background: rgba(0,255,255,45); border: 1px solid rgba(0,255,255,200); "
            "border-radius: 6px; font-size: 13px; font-weight: bold; padding: 0 20px; }"
            "QPushButton:hover { background: rgba(0,255,255,75); }"
            "QPushButton:disabled { background: #222; color: #555; border-color: #333; }"
        )
        self._download_btn.clicked.connect(self._start_download)
        controls_row.addWidget(self._download_btn)

        bottom_layout.addLayout(controls_row)

        # Progress row
        progress_row = QHBoxLayout()
        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setStyleSheet(
            "QProgressBar { background: #222; border: none; border-radius: 3px; height: 6px; }"
            "QProgressBar::chunk { background: #0ff; border-radius: 3px; }"
        )
        progress_row.addWidget(self._progress_bar)
        bottom_layout.addLayout(progress_row)

        # Log area
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(120)
        self._log.setStyleSheet(
            "background: #0d0d0d; border: 1px solid #222; border-radius: 4px; "
            "font-family: monospace; font-size: 11px; color: #aaa;"
        )
        self._log.setVisible(False)
        bottom_layout.addWidget(self._log)

        content_layout.addWidget(bottom)
        root.addWidget(content, 1)

    def _apply_dark_theme(self):
        self.setStyleSheet(
            "QMainWindow, QWidget { background: #1a1a1a; color: #ddd; }"
            "QScrollBar:vertical { background: #1a1a1a; width: 8px; }"
            "QScrollBar::handle:vertical { background: #333; border-radius: 4px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
        )

    @staticmethod
    def _tab_btn_style() -> str:
        return (
            "QPushButton { background: transparent; border: none; text-align: left; "
            "padding: 8px 12px; border-radius: 6px; color: #aaa; font-size: 13px; }"
            "QPushButton:hover { background: #222; color: #fff; }"
            "QPushButton:checked { background: rgba(0,255,255,30); color: #0ff; font-weight: bold; }"
        )

    # ── Tab Loading ──────────────────────────────────────────────────────────

    def _load_tab(self, tab: str):
        if self._library_worker and self._library_worker.isRunning():
            return

        self._current_tab = tab
        self._tab_title.setText(tab.capitalize())

        for key, btn in self._tab_buttons.items():
            btn.setChecked(key == tab)

        self._clear_list()
        self._loading_label.setVisible(True)

        self._library_worker = LibraryWorker(self.api, tab)
        self._library_worker.item_ready.connect(self._add_item)
        self._library_worker.finished_ok.connect(self._on_library_loaded)
        self._library_worker.error.connect(self._on_library_error)
        self._library_worker.start()

    def _clear_list(self):
        self._item_widgets.clear()
        layout = self._list_layout
        while layout.count():
            child = layout.takeAt(0)
            w = child.widget()
            if w and w is not self._loading_label:
                w.deleteLater()
        # Re-add loading label and stretch
        self._loading_label.setText("Loading…")
        layout.addWidget(self._loading_label)
        layout.addStretch()

    def _add_item(self, item_data):
        # Remove loading label from layout on first real item
        idx = self._list_layout.indexOf(self._loading_label)
        if idx >= 0:
            self._list_layout.takeAt(idx)
            self._loading_label.setVisible(False)

        widget = LibraryItemWidget(item_data)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #252525;")

        self._list_layout.addWidget(widget)
        self._list_layout.addWidget(sep)
        self._item_widgets.append(widget)

    def _on_library_loaded(self):
        if not self._item_widgets:
            self._loading_label.setText(f"No {self._current_tab} found in your favorites.")
            idx = self._list_layout.indexOf(self._loading_label)
            if idx < 0:
                self._list_layout.addWidget(self._loading_label)
            self._loading_label.setVisible(True)

    def _on_library_error(self, msg: str):
        self._loading_label.setText(f"Error: {msg}")
        self._loading_label.setVisible(True)

    # ── Selection ────────────────────────────────────────────────────────────

    def _select_all(self):
        for w in self._item_widgets:
            w.checkbox.setChecked(True)

    def _deselect_all(self):
        for w in self._item_widgets:
            w.checkbox.setChecked(False)

    def _selected_items(self) -> list[LibraryItemWidget]:
        return [w for w in self._item_widgets if w.is_checked()]

    # ── Download ─────────────────────────────────────────────────────────────

    def _browse_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select Download Folder", self._path_edit.text()
        )
        if folder:
            self._path_edit.setText(folder)

    def _start_download(self):
        selected = self._selected_items()
        if not selected:
            self._log_msg("⚠ No items selected.")
            self._log.setVisible(True)
            return

        urls = [w.get_url() for w in selected if w.get_url()]
        if not urls:
            self._log_msg("⚠ Could not build URLs for selected items.")
            return

        download_path = self._path_edit.text().strip()
        quality = self._quality_combo.currentData()

        self._log.clear()
        self._log.setVisible(True)
        self._progress_bar.setVisible(True)
        self._progress_bar.setMaximum(len(urls))
        self._progress_bar.setValue(0)
        self._download_btn.setEnabled(False)
        self._download_btn.setText("Downloading…")

        self._download_worker = DownloadWorker(urls, download_path, quality)
        self._download_worker.log_line.connect(self._log_msg)
        self._download_worker.progress.connect(self._on_download_progress)
        self._download_worker.finished_ok.connect(self._on_download_done)
        self._download_worker.error.connect(self._on_download_error)
        self._download_worker.start()

    def _log_msg(self, text: str):
        self._log.append(text)
        sb = self._log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_download_progress(self, done: int, total: int):
        self._progress_bar.setValue(done)
        self._download_btn.setText(f"Downloading… ({done}/{total})")

    def _on_download_done(self):
        self._download_btn.setEnabled(True)
        self._download_btn.setText("Download Selected")
        self._log_msg("\n✓ All downloads complete.")

    def _on_download_error(self, msg: str):
        self._download_btn.setEnabled(True)
        self._download_btn.setText("Download Selected")
        self._log_msg(f"\n✗ Error: {msg}")

    # ── Logout ───────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        # Stop any running workers before closing
        for worker in [self._library_worker, self._download_worker]:
            if worker and worker.isRunning():
                worker.quit()
                worker.wait(2000)
        super().closeEvent(event)

    def _logout(self):
        from tiddl.cli.utils.auth.core import save_auth_data
        from tiddl.cli.utils.auth.models import AuthData
        save_auth_data(AuthData())
        from app.auth_window import AuthWindow
        dlg = AuthWindow(self)
        if dlg.exec():
            self.api = build_api()
            self._load_tab(self._current_tab)
