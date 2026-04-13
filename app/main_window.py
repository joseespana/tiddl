"""
Main application window.
"""
import re
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QLabel, QPushButton, QFileDialog, QComboBox,
    QProgressBar, QTextEdit, QFrame, QCheckBox,
    QScrollArea, QLineEdit,
)
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PySide6.QtCore import QUrl

from app.api_client import build_api
from app.workers import LibraryWorker, DownloadWorker


# ── Quality options ──────────────────────────────────────────────────────────
# "max" already auto-falls-back to the best quality each track supports.
QUALITY_OPTIONS = [
    ("Best Available per track (auto)", "max"),
    ("FLAC 16-bit / 44.1 kHz  (CD quality)", "high"),
    ("AAC 320 kbps", "normal"),
    ("AAC 96 kbps", "low"),
]

SIDEBAR_TABS = [
    ("Playlists", "playlists"),
    ("Albums",    "albums"),
    ("Artists",   "artists"),
]

ITEM_HEIGHT = 68


def _sanitize(s: str) -> str:
    return re.sub(r'[\\/:"*?<>|]+', "", s)


# ── Cover image ──────────────────────────────────────────────────────────────

class CoverLabel(QLabel):
    def __init__(self, url: str | None, size: int = 52, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setStyleSheet("background: #222; border-radius: 4px;")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if url:
            full = f"https://resources.tidal.com/images/{url.replace('-', '/')}/320x320.jpg"
            mgr = QNetworkAccessManager(self)
            reply = mgr.get(QNetworkRequest(QUrl(full)))
            reply.finished.connect(lambda: self._loaded(reply, size))
            self._reply = reply

    def _loaded(self, reply, size):
        pm = QPixmap()
        if pm.loadFromData(reply.readAll()):
            self.setPixmap(pm.scaled(size, size,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation))


# ── Library row ──────────────────────────────────────────────────────────────

class LibraryItemWidget(QWidget):
    check_changed = Signal()

    def __init__(self, item_data, download_path: str, parent=None):
        super().__init__(parent)
        self.item_data = item_data
        self.setFixedHeight(ITEM_HEIGHT)

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 6, 16, 6)
        row.setSpacing(12)

        self.checkbox = QCheckBox()
        self.checkbox.setFixedWidth(20)
        self.checkbox.stateChanged.connect(self.check_changed)
        row.addWidget(self.checkbox)

        cover_url = self._cover(item_data)
        row.addWidget(CoverLabel(cover_url, size=52))

        text_col = QVBoxLayout()
        text_col.setSpacing(3)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)

        self._title_lbl = QLabel(self._title(item_data))
        self._title_lbl.setStyleSheet("font-weight: bold; font-size: 13px;")
        title_row.addWidget(self._title_lbl)

        self._badge = QLabel("✓ Downloaded")
        self._badge.setStyleSheet(
            "background: rgba(0,200,100,40); color: #0c6; "
            "border: 1px solid rgba(0,200,100,100); border-radius: 3px; "
            "font-size: 10px; padding: 1px 6px;"
        )
        self._badge.setVisible(False)
        title_row.addWidget(self._badge)
        title_row.addStretch()
        text_col.addLayout(title_row)

        self._sub_lbl = QLabel(self._subtitle(item_data))
        self._sub_lbl.setStyleSheet("color: #888; font-size: 11px;")
        text_col.addWidget(self._sub_lbl)

        row.addLayout(text_col, 1)

        self.refresh_downloaded(download_path)

    # ── helpers ──────────────────────────────────────────────────────────────

    def is_checked(self) -> bool:
        return self.checkbox.isChecked()

    def get_url(self) -> str:
        d = self.item_data
        if hasattr(d, "uuid"):
            return f"https://tidal.com/playlist/{d.uuid}"
        if hasattr(d, "url") and d.url:
            return d.url
        return ""

    def refresh_downloaded(self, download_path: str):
        self._badge.setVisible(self._check_downloaded(download_path))

    def _check_downloaded(self, download_path: str) -> bool:
        if not download_path:
            return False
        base = Path(download_path)
        d = self.item_data

        # Playlist → look for its M3U file
        if hasattr(d, "uuid"):
            m3u = base / "m3u" / f"{_sanitize(d.title)}.m3u"
            return m3u.exists()

        # Album → look for its directory with at least one audio file
        if hasattr(d, "numberOfTracks") and hasattr(d, "releaseDate"):
            artist = _sanitize(d.artist.name) if getattr(d, "artist", None) else ""
            album = _sanitize(d.title)
            folder = base / artist / album
            if folder.exists():
                return any(folder.glob("*.flac")) or any(folder.glob("*.m4a"))

        # Artist → look for their root folder
        if hasattr(d, "artistTypes") or (hasattr(d, "name") and not hasattr(d, "title")):
            folder = base / _sanitize(d.name)
            return folder.exists()

        return False

    @staticmethod
    def _cover(d) -> str | None:
        for attr in ("squareImage", "cover", "picture"):
            v = getattr(d, attr, None)
            if v:
                return v
        return None

    @staticmethod
    def _title(d) -> str:
        return getattr(d, "title", getattr(d, "name", "Unknown"))

    @staticmethod
    def _subtitle(d) -> str:
        if hasattr(d, "numberOfTracks"):
            artist = (d.artist.name + " · ") if getattr(d, "artist", None) else ""
            return f"{artist}{d.numberOfTracks} tracks"
        if hasattr(d, "artistTypes") or not hasattr(d, "title"):
            pop = getattr(d, "popularity", None)
            return f"Popularity: {pop}" if pop else "Artist"
        return ""


# ── Main window ──────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("tiddl")
        self.setMinimumSize(900, 640)
        self.resize(1120, 720)

        self.api = build_api()
        self._library_worker: LibraryWorker | None = None
        self._download_worker: DownloadWorker | None = None
        self._current_tab = "playlists"
        self._item_widgets: list[LibraryItemWidget] = []

        self._build_ui()
        self._apply_theme()
        self._load_tab("playlists")

    # ── Build UI ─────────────────────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._make_sidebar())
        root.addWidget(self._make_content(), 1)

    def _make_sidebar(self) -> QWidget:
        sb = QWidget()
        sb.setFixedWidth(190)
        sb.setStyleSheet("background: #111;")
        lay = QVBoxLayout(sb)
        lay.setContentsMargins(12, 22, 12, 20)
        lay.setSpacing(4)

        logo = QLabel("tiddl")
        f = QFont(); f.setPointSize(22); f.setBold(True)
        logo.setFont(f)
        logo.setStyleSheet("color: #0ff; padding-bottom: 14px;")
        lay.addWidget(logo)

        self._tab_buttons: dict[str, QPushButton] = {}
        for label, key in SIDEBAR_TABS:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setStyleSheet(_tab_btn_style())
            btn.clicked.connect(lambda _, k=key: self._load_tab(k))
            lay.addWidget(btn)
            self._tab_buttons[key] = btn

        lay.addStretch()

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #2a2a2a;")
        lay.addWidget(sep)

        logout = QPushButton("Logout")
        logout.setStyleSheet(
            "QPushButton{background:transparent;border:none;color:#555;"
            "text-align:left;padding:6px 8px;}"
            "QPushButton:hover{color:#f66;}"
        )
        logout.clicked.connect(self._logout)
        lay.addWidget(logout)

        return sb

    def _make_content(self) -> QWidget:
        content = QWidget()
        lay = QVBoxLayout(content)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # ── Top bar ───────────────────────────────────────────────────────────
        top = QWidget()
        top.setFixedHeight(52)
        top.setStyleSheet("background:#181818; border-bottom:1px solid #2a2a2a;")
        top_lay = QHBoxLayout(top)
        top_lay.setContentsMargins(16, 0, 16, 0)
        top_lay.setSpacing(8)

        self._tab_title = QLabel("Playlists")
        f2 = QFont(); f2.setPointSize(15); f2.setBold(True)
        self._tab_title.setFont(f2)
        top_lay.addWidget(self._tab_title)
        top_lay.addStretch()

        # Select All toggles into Deselect All
        self._select_btn = QPushButton("Select All")
        self._select_btn.setCheckable(True)
        self._select_btn.setStyleSheet(_action_btn_style())
        self._select_btn.clicked.connect(self._toggle_select_all)
        top_lay.addWidget(self._select_btn)

        lay.addWidget(top)

        # ── List ─────────────────────────────────────────────────────────────
        self._list_container = QWidget()
        self._list_container.setStyleSheet("background:#1a1a1a;")
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(0)

        self._loading_label = QLabel("Loading…")
        self._loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loading_label.setStyleSheet("color:#555; font-size:14px; padding:50px;")
        self._list_layout.addWidget(self._loading_label)
        self._list_layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._list_container)
        scroll.setStyleSheet("QScrollArea{border:none;}")
        lay.addWidget(scroll, 1)

        # ── Bottom panel ──────────────────────────────────────────────────────
        lay.addWidget(self._make_bottom())

        return content

    def _make_bottom(self) -> QWidget:
        bottom = QWidget()
        bottom.setStyleSheet("background:#141414; border-top:1px solid #252525;")
        lay = QVBoxLayout(bottom)
        lay.setContentsMargins(16, 10, 16, 10)
        lay.setSpacing(8)

        # Row 1: path + quality + download button
        row1 = QHBoxLayout()
        row1.setSpacing(10)

        lbl = QLabel("Save to:")
        lbl.setStyleSheet("color:#777; font-size:12px;")
        row1.addWidget(lbl)

        self._path_edit = QLineEdit(_default_download_path())
        self._path_edit.setStyleSheet(
            "background:#222; border:1px solid #333; border-radius:4px;"
            "padding:4px 8px; color:#ccc; font-size:12px;"
        )
        self._path_edit.setMinimumWidth(180)
        self._path_edit.textChanged.connect(self._on_path_changed)
        row1.addWidget(self._path_edit, 1)

        browse = QPushButton("Browse…")
        browse.setStyleSheet(_action_btn_style())
        browse.clicked.connect(self._browse_folder)
        row1.addWidget(browse)

        self._quality_combo = QComboBox()
        for label, val in QUALITY_OPTIONS:
            self._quality_combo.addItem(label, userData=val)
        self._quality_combo.setStyleSheet(
            "QComboBox{background:#222; border:1px solid #333; border-radius:4px;"
            "padding:4px 8px; color:#ccc; font-size:12px; min-width:240px;}"
            "QComboBox QAbstractItemView{background:#222; color:#ccc; border:1px solid #444;}"
        )
        row1.addWidget(self._quality_combo)

        self._download_btn = QPushButton("Download Selected")
        self._download_btn.setMinimumHeight(36)
        self._download_btn.setStyleSheet(
            "QPushButton{background:rgba(0,255,255,45);border:1px solid rgba(0,255,255,180);"
            "border-radius:6px;font-size:13px;font-weight:bold;padding:0 20px;}"
            "QPushButton:hover{background:rgba(0,255,255,75);}"
            "QPushButton:disabled{background:#1e1e1e;color:#444;border-color:#2a2a2a;}"
        )
        self._download_btn.clicked.connect(self._start_download)
        row1.addWidget(self._download_btn)

        lay.addLayout(row1)

        # Row 2: progress bar (hidden until download)
        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        self._progress_bar.setFixedHeight(5)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setStyleSheet(
            "QProgressBar{background:#1e1e1e;border:none;border-radius:2px;}"
            "QProgressBar::chunk{background:#0ff;border-radius:2px;}"
        )
        lay.addWidget(self._progress_bar)

        # Row 3: log (hidden until download)
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(110)
        self._log.setVisible(False)
        self._log.setStyleSheet(
            "background:#0d0d0d; border:1px solid #1e1e1e; border-radius:4px;"
            "font-family:monospace; font-size:11px; color:#999;"
        )
        lay.addWidget(self._log)

        return bottom

    def _apply_theme(self):
        self.setStyleSheet(
            "QMainWindow,QWidget{background:#1a1a1a;color:#ddd;}"
            "QScrollBar:vertical{background:#1a1a1a;width:7px;border-radius:3px;}"
            "QScrollBar::handle:vertical{background:#2e2e2e;border-radius:3px;}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}"
            "QCheckBox::indicator{width:16px;height:16px;border-radius:3px;"
            "border:1px solid #444;background:#1e1e1e;}"
            "QCheckBox::indicator:checked{background:#0ff;border-color:#0ff;}"
        )

    # ── Tab loading ───────────────────────────────────────────────────────────

    def _load_tab(self, tab: str):
        if self._library_worker and self._library_worker.isRunning():
            return

        self._current_tab = tab
        self._tab_title.setText(tab.capitalize())

        for key, btn in self._tab_buttons.items():
            btn.setChecked(key == tab)

        # Reset select button
        self._select_btn.setChecked(False)
        self._select_btn.setText("Select All")

        self._clear_list()

        self._library_worker = LibraryWorker(self.api, tab)
        self._library_worker.item_ready.connect(self._add_item)
        self._library_worker.finished_ok.connect(self._on_library_loaded)
        self._library_worker.error.connect(self._on_library_error)
        self._library_worker.start()

    def _clear_list(self):
        self._item_widgets.clear()
        while self._list_layout.count():
            child = self._list_layout.takeAt(0)
            w = child.widget()
            if w and w is not self._loading_label:
                w.deleteLater()
        self._loading_label.setText("Loading…")
        self._loading_label.setVisible(True)
        self._list_layout.addWidget(self._loading_label)
        self._list_layout.addStretch()
        self._update_download_btn()

    def _add_item(self, item_data):
        # Hide loading label on first item
        idx = self._list_layout.indexOf(self._loading_label)
        if idx >= 0:
            self._list_layout.takeAt(idx)
            self._loading_label.setVisible(False)

        widget = LibraryItemWidget(item_data, self._path_edit.text())
        widget.check_changed.connect(self._update_download_btn)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background:#252525; max-height:1px;")

        # Insert before the trailing stretch
        pos = max(self._list_layout.count() - 1, 0)
        self._list_layout.insertWidget(pos, widget)
        self._list_layout.insertWidget(pos + 1, sep)
        self._item_widgets.append(widget)

    def _on_library_loaded(self):
        if not self._item_widgets:
            self._loading_label.setText(f"No {self._current_tab} in your favorites.")
            idx = self._list_layout.indexOf(self._loading_label)
            if idx < 0:
                self._list_layout.insertWidget(0, self._loading_label)
            self._loading_label.setVisible(True)

    def _on_library_error(self, msg: str):
        self._loading_label.setText(f"⚠ {msg}")
        self._loading_label.setVisible(True)

    # ── Selection ─────────────────────────────────────────────────────────────

    def _toggle_select_all(self, checked: bool):
        if checked:
            self._select_btn.setText("Deselect All")
            for w in self._item_widgets:
                w.checkbox.setChecked(True)
        else:
            self._select_btn.setText("Select All")
            for w in self._item_widgets:
                w.checkbox.setChecked(False)

    def _update_download_btn(self):
        n = sum(1 for w in self._item_widgets if w.is_checked())
        if n:
            self._download_btn.setText(f"Download Selected  ({n})")
        else:
            self._download_btn.setText("Download Selected")
        # If user manually unchecked everything, reset the toggle button
        if n == 0 and self._select_btn.isChecked():
            self._select_btn.blockSignals(True)
            self._select_btn.setChecked(False)
            self._select_btn.setText("Select All")
            self._select_btn.blockSignals(False)
        elif n == len(self._item_widgets) and self._item_widgets and not self._select_btn.isChecked():
            self._select_btn.blockSignals(True)
            self._select_btn.setChecked(True)
            self._select_btn.setText("Deselect All")
            self._select_btn.blockSignals(False)

    # ── Download ──────────────────────────────────────────────────────────────

    def _browse_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select Download Folder", self._path_edit.text()
        )
        if folder:
            self._path_edit.setText(folder)

    def _on_path_changed(self, path: str):
        for w in self._item_widgets:
            w.refresh_downloaded(path)

    def _start_download(self):
        selected = [w for w in self._item_widgets if w.is_checked()]
        if not selected:
            self._log_msg("⚠ No items selected.")
            self._log.setVisible(True)
            return

        urls = [w.get_url() for w in selected if w.get_url()]
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
        self._download_worker.progress.connect(self._on_progress)
        self._download_worker.finished_ok.connect(self._on_download_done)
        self._download_worker.error.connect(self._on_download_error)
        self._download_worker.start()

    def _log_msg(self, text: str):
        self._log.append(text)
        self._log.verticalScrollBar().setValue(
            self._log.verticalScrollBar().maximum()
        )

    def _on_progress(self, done: int, total: int):
        self._progress_bar.setValue(done)
        self._download_btn.setText(f"Downloading… ({done}/{total})")

    def _on_download_done(self):
        self._download_btn.setEnabled(True)
        self._update_download_btn()
        self._log_msg("✓ All downloads complete.")
        # Refresh badges now that more files exist on disk
        path = self._path_edit.text()
        for w in self._item_widgets:
            w.refresh_downloaded(path)

    def _on_download_error(self, msg: str):
        self._download_btn.setEnabled(True)
        self._update_download_btn()
        self._log_msg(f"✗ Error: {msg}")

    # ── Cleanup / Logout ──────────────────────────────────────────────────────

    def closeEvent(self, event):
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


# ── Style helpers ─────────────────────────────────────────────────────────────

def _tab_btn_style() -> str:
    return (
        "QPushButton{background:transparent;border:none;text-align:left;"
        "padding:9px 12px;border-radius:6px;color:#999;font-size:13px;}"
        "QPushButton:hover{background:#1e1e1e;color:#ddd;}"
        "QPushButton:checked{background:rgba(0,255,255,30);color:#0ff;font-weight:bold;}"
    )


def _action_btn_style() -> str:
    return (
        "QPushButton{background:#222;border:1px solid #383838;border-radius:4px;"
        "padding:4px 12px;color:#aaa;font-size:12px;}"
        "QPushButton:hover{border-color:#0ff;color:#0ff;}"
        "QPushButton:checked{background:rgba(0,255,255,25);border-color:#0ff;color:#0ff;}"
    )


def _default_download_path() -> str:
    """Read download_path from ~/.tiddl/config.toml, fall back to ~/Music/tiddl."""
    try:
        from tiddl.cli.config import CONFIG
        p = CONFIG.download.download_path
        if p:
            return str(p)
    except Exception:
        pass
    return str(Path.home() / "Music" / "tiddl")
