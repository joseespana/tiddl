"""
Pure-UI view for the main window.

Contains all widget construction, style helpers, and UI-only logic.
No API calls, no worker creation, no business state — only signals and
public methods that the presenter drives.
"""
import html as _html
import re
import unicodedata
from pathlib import Path

from PySide6.QtCore import (
    Qt, Signal, QUrl, QPropertyAnimation, QEasingCurve,
)
from PySide6.QtGui import QFont, QFontMetrics, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.models.card_vm import CardVM

# ── Quality options ───────────────────────────────────────────────────────────
QUALITY_OPTIONS = [
    ("Best Available per track (auto)", "max"),
    ("FLAC 16-bit / 44.1 kHz  (CD quality)", "high"),
    ("AAC 320 kbps", "normal"),
    ("AAC 96 kbps", "low"),
]

SIDEBAR_TABS = [
    ("Playlists",    "playlists"),
    ("Albums",       "albums"),
    ("Artists",      "artists"),
    ("Downloaded",   "downloaded"),
    ("Search Tidal", "search"),
]

ITEM_HEIGHT = 68


# ── String helpers ────────────────────────────────────────────────────────────

def _sanitize(s: str) -> str:
    """Remove filesystem-unsafe characters from *s*."""
    return re.sub(r'[\\/:"*?<>|]+', "", s)


def _norm(s: str) -> str:
    """Lowercase + strip for case-insensitive comparison."""
    return _sanitize(s).lower().strip()


# Collapse any sequence of whitespace/separator/punctuation into a single space.
# Kept as a module constant so the regex is compiled once.
_SEARCH_SEP_RE = re.compile(r"[\s\-_.,;:!?¡¿'\"`´()\[\]{}/\\|]+")


def _search_key(s: str) -> str:
    """Fold a string to a diacritic-insensitive, case-insensitive lookup key.

    - Unicode NFKD decomposes accented characters (``é`` → ``e`` + U+0301).
    - Combining marks (category ``Mn``) are dropped so ``café`` and
      ``cafe`` both produce ``cafe``.
    - ``ñ`` decomposes to ``n`` + combining tilde, so ``peña`` and
      ``pena`` both produce ``pena``.
    - ``ß``/``ø``/``ł`` are preserved (they're not combining forms); the
      user can still search by their ASCII approximation if desired.
    - Lowercased, whitespace/punctuation collapsed to single spaces,
      stripped.
    """
    if not s:
        return ""
    decomposed = unicodedata.normalize("NFKD", s)
    no_marks = "".join(
        ch for ch in decomposed if not unicodedata.combining(ch)
    )
    collapsed = _SEARCH_SEP_RE.sub(" ", no_marks).strip().lower()
    return collapsed


def _matches_query(query_key: str, haystack_key: str) -> bool:
    """Return True if every whitespace-separated token in *query_key*
    appears as a substring in *haystack_key*.

    Tokens are AND-combined and order-independent so ``"daft disco"``
    finds a ``"Discovery — Daft Punk"`` entry.
    Both inputs must already be pre-folded via :func:`_search_key`.
    """
    if not query_key:
        return True
    return all(tok in haystack_key for tok in query_key.split())


# ── Network manager singleton ─────────────────────────────────────────────────

_net_manager: QNetworkAccessManager | None = None


def _get_net_manager(parent=None) -> QNetworkAccessManager:
    global _net_manager
    if _net_manager is None:
        _net_manager = QNetworkAccessManager(parent)
    return _net_manager


# ── Default download path ─────────────────────────────────────────────────────

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


def _input_style(font_size: int = 12) -> str:
    return (
        f"background:#222; border:1px solid #333; border-radius:4px;"
        f"padding:4px 8px; color:#ccc; font-size:{font_size}px;"
    )


# ── Type-badge styles ─────────────────────────────────────────────────────────

_BADGE_QSS = {
    "playlist": (
        "background:rgba(0,200,255,18);color:#00aacc;"
        "border:1px solid rgba(0,200,255,55);border-radius:3px;"
        "font-size:9px;font-weight:600;padding:1px 5px;letter-spacing:0.5px;"
    ),
    "album": (
        "background:rgba(180,100,255,18);color:#b46eff;"
        "border:1px solid rgba(180,100,255,55);border-radius:3px;"
        "font-size:9px;font-weight:600;padding:1px 5px;letter-spacing:0.5px;"
    ),
    "artist": (
        "background:rgba(255,160,50,18);color:#e8900a;"
        "border:1px solid rgba(255,160,50,55);border-radius:3px;"
        "font-size:9px;font-weight:600;padding:1px 5px;letter-spacing:0.5px;"
    ),
}

_BADGE_LABEL = {"playlist": "PLAYLIST", "album": "ALBUM", "artist": "ARTIST"}


# ── Log color helper ──────────────────────────────────────────────────────────

def _log_html(text: str) -> str:
    """Return an HTML snippet for *text* with the appropriate color."""
    t = text.strip()
    if t.startswith("▶") or "Downloading " in t:
        color = "#00cccc"
    elif t.startswith("✓"):
        color = "#00c864"
    elif t.startswith("Downloaded "):
        color = "#00c864"
    elif t.startswith("/") or t.startswith("~") or t.startswith("\\"):
        color = "#444"
    elif "expires in" in t or "token" in t.lower():
        color = "#f0a500"
    elif t.startswith("⚠") or t.lower().startswith("skipped"):
        color = "#f06060"
    else:
        color = "#777"
    escaped = _html.escape(text)
    return (
        f'<span style="color:{color};font-family:\'SF Mono\',\'Fira Code\','
        f"monospace;font-size:11px;line-height:1.5;\">{escaped}</span>"
    )


# ── Cover image widget ────────────────────────────────────────────────────────

class CoverLabel(QLabel):
    """QLabel that asynchronously loads a Tidal cover image by UUID."""

    def __init__(self, url: str | None, size: int = 52, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setStyleSheet("background: #222; border-radius: 4px;")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._reply = None
        if url:
            full = (
                f"https://resources.tidal.com/images/"
                f"{url.replace('-', '/')}/320x320.jpg"
            )
            mgr = _get_net_manager()
            reply = mgr.get(QNetworkRequest(QUrl(full)))
            reply.finished.connect(lambda: self._loaded(reply, size))
            self._reply = reply

    def _loaded(self, reply, size: int) -> None:
        try:
            pm = QPixmap()
            if pm.loadFromData(reply.readAll()):
                self.setPixmap(
                    pm.scaled(
                        size,
                        size,
                        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
        except RuntimeError:
            # Widget was deleted before the reply arrived (e.g. tab switched).
            pass
        finally:
            reply.deleteLater()
            self._reply = None

    def hideEvent(self, event) -> None:  # noqa: N802
        """Abort in-flight request when the widget is hidden/removed."""
        if self._reply is not None:
            try:
                self._reply.abort()
            except RuntimeError:
                pass
        super().hideEvent(event)


# ── Library card widget ───────────────────────────────────────────────────────

CARD_W = 178
CARD_H = 268
COVER_SIZE = 178
CARD_SPACING_H = 14
CARD_SPACING_V = 18
GRID_MARGIN = 16


class LibraryItemWidget(QFrame):
    """A square card for a library item (Tidal/Spotify-style).

    The card is populated from a pure-data ``CardVM`` DTO — it no longer
    imports Tidal core models or does ``isinstance`` checks.
    """

    check_changed = Signal()
    detail_requested = Signal(CardVM)

    def __init__(
        self,
        card_vm: CardVM,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._vm = card_vm
        self._sep = None  # presenter checks `if w._sep:` — always falsy now
        self._source: str = card_vm.source
        self.setFixedSize(CARD_W, CARD_H)
        self.setObjectName("LibraryItemWidget")
        self._base_qss = (
            "LibraryItemWidget{background:#1e1e1e;border:1px solid #252525;"
            "border-radius:8px;}"
            "LibraryItemWidget:hover{background:#252525;border-color:#333;}"
        )
        self.setStyleSheet(self._base_qss)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Cover area (178×178) with absolutely-positioned overlays ──────────
        self._cover_container = QWidget(self)
        self._cover_container.setFixedSize(COVER_SIZE, COVER_SIZE)
        self._cover_container.setStyleSheet(
            "background:#0a0a0a;"
            "border-top-left-radius:8px;"
            "border-top-right-radius:8px;"
        )
        outer.addWidget(self._cover_container)

        cover_url = card_vm.cover_url
        self._cover_lbl = CoverLabel(cover_url, size=COVER_SIZE, parent=self._cover_container)
        self._cover_lbl.setStyleSheet(
            "background:#0a0a0a;"
            "border-top-left-radius:8px;"
            "border-top-right-radius:8px;"
        )
        self._cover_lbl.move(0, 0)

        # Dim overlay for downloaded items (hidden by default)
        self._dim_overlay = QLabel(self._cover_container)
        self._dim_overlay.setFixedSize(COVER_SIZE, COVER_SIZE)
        self._dim_overlay.setStyleSheet(
            "background:rgba(0,0,0,120);"
            "border-top-left-radius:8px;"
            "border-top-right-radius:8px;"
        )
        self._dim_overlay.move(0, 0)
        self._dim_overlay.setVisible(False)

        # Checkbox overlay (top-left)
        self.checkbox = QCheckBox(self._cover_container)
        self.checkbox.setFixedSize(22, 22)
        self.checkbox.setStyleSheet(
            "QCheckBox{background:rgba(0,0,0,150);border-radius:4px;padding:2px;}"
            "QCheckBox::indicator{width:14px;height:14px;border-radius:3px;"
            "border:1px solid #888;background:#1e1e1e;}"
            "QCheckBox::indicator:checked{background:#0ff;border-color:#0ff;}"
        )
        self.checkbox.move(8, 8)
        self.checkbox.stateChanged.connect(self.check_changed)
        self.checkbox.raise_()

        # ── Text + badges block (below cover) ─────────────────────────────────
        text_wrap = QWidget(self)
        text_wrap.setStyleSheet("background:transparent;")
        text_lay = QVBoxLayout(text_wrap)
        text_lay.setContentsMargins(8, 8, 8, 8)
        text_lay.setSpacing(4)

        title_text = card_vm.title
        sub_text = card_vm.subtitle
        # Pre-folded, diacritic-insensitive search index. Legacy
        # _title_cache / _sub_cache kept as aliases pointing to the
        # same key so any external caller keeps working.
        self._search_key = _search_key(f"{title_text} {sub_text}")
        self._title_cache = self._search_key
        self._sub_cache = self._search_key

        self._title_lbl = QLabel(text_wrap)
        self._title_lbl.setStyleSheet(
            "color:#eee; font-weight:bold; font-size:13px; background:transparent;"
        )
        self._title_lbl.setWordWrap(False)
        self._title_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self._title_lbl.setMaximumWidth(162)
        self._title_lbl.setText(self._elide(title_text, self._title_lbl.font(), 162))
        self._title_lbl.setToolTip(title_text)
        text_lay.addWidget(self._title_lbl)

        self._sub_lbl = QLabel(text_wrap)
        self._sub_lbl.setStyleSheet(
            "color:#888; font-size:11px; background:transparent;"
        )
        self._sub_lbl.setWordWrap(False)
        self._sub_lbl.setMaximumWidth(162)
        self._sub_lbl.setText(self._elide(sub_text, self._sub_lbl.font(), 162))
        self._sub_lbl.setToolTip(sub_text)
        text_lay.addWidget(self._sub_lbl)

        # Badge row (type pill + downloaded/pending badge) — under the text
        badge_row = QHBoxLayout()
        badge_row.setContentsMargins(0, 2, 0, 0)
        badge_row.setSpacing(6)

        itype = card_vm.kind
        self._type_badge = QLabel(_BADGE_LABEL.get(itype, ""), text_wrap)
        self._type_badge.setStyleSheet(_BADGE_QSS.get(itype, ""))
        self._type_badge.setVisible(bool(_BADGE_LABEL.get(itype)))
        badge_row.addWidget(self._type_badge)

        self._badge = QLabel("✓ Downloaded", text_wrap)
        self._badge.setStyleSheet(
            "background:rgba(0,200,100,30); color:#0c6;"
            "border:1px solid rgba(0,200,100,120); border-radius:3px;"
            "font-size:9px; font-weight:600; padding:1px 5px; letter-spacing:0.5px;"
        )
        self._badge.setVisible(False)
        badge_row.addWidget(self._badge)
        badge_row.addStretch()
        text_lay.addLayout(badge_row)
        text_lay.addStretch()

        outer.addWidget(text_wrap, 1)

        self.refresh_downloaded(card_vm.is_downloaded)

        # Fade-in animation: 0→100% opacity over 240ms with a soft ease-out.
        # IMPORTANT: QGraphicsOpacityEffect renders the widget into an
        # off-screen pixmap and eats mouse events for child widgets (known
        # Qt issue QTBUG-59187). We remove the effect as soon as the fade
        # completes so the checkbox and other interactive overlays start
        # receiving clicks again.
        self._opacity_fx = QGraphicsOpacityEffect(self)
        self._opacity_fx.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity_fx)
        self._fade_anim = QPropertyAnimation(self._opacity_fx, b"opacity", self)
        self._fade_anim.setDuration(240)
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.finished.connect(self._on_fade_done)

    def _on_fade_done(self) -> None:
        """Drop the opacity effect once the fade is over so clicks work."""
        self.setGraphicsEffect(None)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        """Clicking anywhere on the card toggles the checkbox.

        Clicks that land directly on the checkbox are handled by the
        checkbox itself before this method runs (Qt delivers events to
        the topmost widget first), so we don't double-toggle.
        Downloaded cards have the checkbox hidden and are inert.
        """
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.checkbox.isVisible()
        ):
            self.checkbox.setChecked(not self.checkbox.isChecked())
        super().mousePressEvent(event)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        """Double-click opens the detail dialog for this card.

        The checkbox already consumed the first click so the card state
        toggled on single-press is undone here — we flip it back so the
        double-click is net-neutral for selection.
        """
        if event.button() == Qt.MouseButton.LeftButton:
            # Undo the selection-toggle that ran on the first mousePressEvent.
            if self.checkbox.isVisible():
                self.checkbox.setChecked(not self.checkbox.isChecked())
            self.detail_requested.emit(self._vm)
        super().mouseDoubleClickEvent(event)

    def play_fade_in(self) -> None:
        """Trigger the entry fade-in animation."""
        self._fade_anim.start()

    @staticmethod
    def _elide(text: str, font, max_w: int) -> str:
        fm = QFontMetrics(font)
        return fm.elidedText(text, Qt.TextElideMode.ElideRight, max_w)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def is_checked(self) -> bool:
        """Return True when the card checkbox is checked."""
        return self.checkbox.isChecked()

    def get_url(self) -> str:
        """Return the Tidal URL for this item, or empty string if unavailable."""
        return self._vm.url

    def get_title(self) -> str:
        """Return the card's title text (used by the presenter for warnings)."""
        return self._vm.title

    def refresh_downloaded(self, is_downloaded: bool) -> None:
        """Update downloaded badge, dim overlay, checkbox visibility + cursor.

        Args:
            is_downloaded: Whether this card should render as "already
                downloaded" (hides the checkbox, dims the cover, shows
                the green badge).
        """
        self._badge.setVisible(is_downloaded)
        if is_downloaded:
            # Hide the checkbox entirely (cannot be re-selected)
            self.checkbox.setChecked(False)
            self.checkbox.setVisible(False)
            self._dim_overlay.setVisible(True)
            self._dim_overlay.raise_()
            self.setCursor(Qt.CursorShape.ArrowCursor)
        else:
            self.checkbox.setVisible(True)
            self.checkbox.raise_()
            self._dim_overlay.setVisible(False)
            self.setCursor(Qt.CursorShape.PointingHandCursor)


# ── Grid container that forwards resize events ────────────────────────────────

class _GridContainer(QWidget):
    """QWidget that calls back into the main view on resize so the
    grid column count can adapt to the available width.
    """

    def __init__(self, view: "MainView") -> None:
        super().__init__()
        self._view = view

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        try:
            self._view._on_grid_resize()
        except Exception:
            pass


# ── Main view ─────────────────────────────────────────────────────────────────

class MainView(QMainWindow):
    """Pure-UI main window.

    Exposes signals for user interactions and public methods for the presenter
    to drive state changes. Contains no business logic.

    Signals:
        tab_requested: Emitted with the sidebar tab key when user clicks a tab.
        tidal_search_requested: Emitted with (query, search_type) when user
            triggers a Tidal search.
        download_selected_requested: Emitted when the Download Selected button
            is clicked (presenter calls get_checked_urls / get_checked_items_without_url).
        download_url_requested: Emitted with the raw URL string from the direct
            URL field.
        logout_requested: Emitted when the Logout button is clicked.
        browse_requested: Emitted when the Browse button is clicked.
        path_changed: Emitted with the new path text whenever it changes.
        filter_changed: Emitted with the filter text whenever it changes.
        select_all_toggled: Emitted with the checked state of the Select All
            button.
    """

    tab_requested = Signal(str)
    tidal_search_requested = Signal(str, str)
    download_selected_requested = Signal()
    download_url_requested = Signal(str)
    logout_requested = Signal()
    browse_requested = Signal()
    path_changed = Signal(str)
    filter_changed = Signal(str)
    select_all_toggled = Signal(bool)
    resync_requested = Signal()
    detail_requested = Signal(CardVM)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("tiddl")
        self.setMinimumSize(900, 640)
        self.resize(1120, 720)

        # Public list of item widgets — presenter reads this directly.
        self.item_widgets: list[LibraryItemWidget] = []

        self._build_ui()
        self._apply_theme()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
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
        f = QFont()
        f.setPointSize(22)
        f.setBold(True)
        logo.setFont(f)
        logo.setStyleSheet("color: #0ff; padding-bottom: 14px;")
        lay.addWidget(logo)

        self._tab_buttons: dict[str, QPushButton] = {}
        for label, key in SIDEBAR_TABS:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setStyleSheet(_tab_btn_style())
            btn.clicked.connect(lambda _, k=key: self.tab_requested.emit(k))
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
        logout.clicked.connect(self.logout_requested)
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
        f2 = QFont()
        f2.setPointSize(15)
        f2.setBold(True)
        self._tab_title.setFont(f2)
        top_lay.addWidget(self._tab_title)

        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText("Search…")
        self._search_box.setClearButtonEnabled(True)
        self._search_box.setFixedWidth(200)
        self._search_box.setStyleSheet(_input_style())
        self._search_box.textChanged.connect(self.filter_changed)
        top_lay.addWidget(self._search_box)
        top_lay.addStretch()

        self._resync_btn = QPushButton("↻ Resync")
        self._resync_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._resync_btn.setToolTip(
            "Re-scan the download folder to refresh ✓ Downloaded badges"
        )
        self._resync_btn.setStyleSheet(_action_btn_style())
        self._resync_btn.clicked.connect(self.resync_requested)
        top_lay.addWidget(self._resync_btn)

        self._select_btn = QPushButton("Select All")
        self._select_btn.setCheckable(True)
        self._select_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._select_btn.setStyleSheet(_action_btn_style())
        self._select_btn.clicked.connect(
            lambda checked: self.select_all_toggled.emit(checked)
        )
        top_lay.addWidget(self._select_btn)

        lay.addWidget(top)

        # ── Downloaded dashboard (visible only on Downloaded tab) ────────────
        lay.addWidget(self._make_downloaded_dashboard())

        # ── Playlist sub-tabs (All / Your playlists / Liked) ─────────────────
        lay.addWidget(self._make_playlist_subtabs())

        # ── Tidal search panel (visible only on Search tab) ───────────────────
        lay.addWidget(self._make_search_panel())

        # ── Grid of cards ────────────────────────────────────────────────────
        self._grid_columns = 4
        self._list_container = _GridContainer(self)
        self._list_container.setStyleSheet("background:#1a1a1a;")
        self._list_layout = QGridLayout(self._list_container)
        self._list_layout.setContentsMargins(
            GRID_MARGIN, GRID_MARGIN, GRID_MARGIN, GRID_MARGIN
        )
        self._list_layout.setHorizontalSpacing(CARD_SPACING_H)
        self._list_layout.setVerticalSpacing(CARD_SPACING_V)
        self._list_layout.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )

        self._loading_label = QLabel("Loading…")
        self._loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loading_label.setStyleSheet(
            "color:#555; font-size:14px; padding:50px;"
        )
        self._list_layout.addWidget(
            self._loading_label, 0, 0, 1, self._grid_columns
        )

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._list_container)
        scroll.setStyleSheet("QScrollArea{border:none;}")
        lay.addWidget(scroll, 1)

        # ── Bottom panel ──────────────────────────────────────────────────────
        lay.addWidget(self._make_bottom())

        return content

    def _make_downloaded_dashboard(self) -> QWidget:
        """Mini counters strip for the Downloaded tab.

        Four stat columns (Playlists · Albums · Artists · Tracks) inside
        a rounded dark card. Hidden unless the Downloaded tab is active.
        """
        self._dl_dashboard = QFrame()
        self._dl_dashboard.setStyleSheet(
            "background:#141414; border-bottom:1px solid #2a2a2a;"
        )
        self._dl_dashboard.setVisible(False)

        outer = QHBoxLayout(self._dl_dashboard)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(10)

        self._dl_stat_labels: dict[str, QLabel] = {}

        def _make_tile(key: str, caption: str, accent: str) -> QWidget:
            tile = QFrame()
            tile.setStyleSheet(
                "QFrame{background:#1b1b1b;border:1px solid #252525;"
                "border-radius:8px;}"
                "QFrame:hover{border-color:#333;}"
            )
            v = QVBoxLayout(tile)
            v.setContentsMargins(14, 10, 14, 10)
            v.setSpacing(2)
            num = QLabel("0")
            nf = QFont()
            nf.setPointSize(18)
            nf.setBold(True)
            num.setFont(nf)
            num.setStyleSheet(f"color:{accent}; background:transparent;")
            cap = QLabel(caption)
            cap.setStyleSheet(
                "color:#888; font-size:10px; background:transparent;"
                "letter-spacing:0.5px;"
            )
            v.addWidget(num)
            v.addWidget(cap)
            self._dl_stat_labels[key] = num
            return tile

        outer.addWidget(_make_tile("playlists", "PLAYLISTS", "#00aacc"), 1)
        outer.addWidget(_make_tile("albums", "ALBUMS", "#b46eff"), 1)
        outer.addWidget(_make_tile("artists", "ARTISTS", "#e8900a"), 1)
        outer.addWidget(_make_tile("tracks", "TRACKS", "#0c6"), 1)

        return self._dl_dashboard

    def set_downloaded_stats(self, stats: dict[str, int]) -> None:
        """Update the dashboard numbers.

        Args:
            stats: ``{"playlists":int, "albums":int, "artists":int,
                "tracks":int}`` — extra keys are ignored.
        """
        for key, lbl in self._dl_stat_labels.items():
            n = int(stats.get(key, 0))
            lbl.setText(f"{n:,}")

    def _make_playlist_subtabs(self) -> QWidget:
        self._pl_subtabs = QFrame()
        self._pl_subtabs.setStyleSheet(
            "background:#181818; border-bottom:1px solid #2a2a2a;"
        )
        self._pl_subtabs.setVisible(False)
        row = QHBoxLayout(self._pl_subtabs)
        row.setContentsMargins(16, 6, 16, 6)
        row.setSpacing(6)

        self._pl_subtab_btns: dict[str, QPushButton] = {}
        for label, key in [
            ("All", "all"),
            ("Your Playlists", "owned"),
            ("Liked", "liked"),
        ]:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(
                "QPushButton{background:transparent;border:1px solid transparent;"
                "border-radius:14px;padding:4px 14px;color:#888;font-size:12px;}"
                "QPushButton:hover{color:#ddd;}"
                "QPushButton:checked{background:rgba(0,255,255,25);"
                "border-color:rgba(0,255,255,120);color:#0ff;font-weight:bold;}"
            )
            btn.clicked.connect(
                lambda _checked, k=key: self._on_subtab_clicked(k)
            )
            row.addWidget(btn)
            self._pl_subtab_btns[key] = btn
        row.addStretch()

        self._pl_subtab_btns["all"].setChecked(True)
        self._current_subtab = "all"
        return self._pl_subtabs

    def _on_subtab_clicked(self, key: str) -> None:
        if self._current_subtab == key:
            self._pl_subtab_btns[key].setChecked(True)
            return
        for k, b in self._pl_subtab_btns.items():
            b.setChecked(k == key)
        self._current_subtab = key
        self._apply_subtab_filter()

    def _apply_subtab_filter(self) -> None:
        """Hide/show rows based on the active sub-tab (all / owned / liked)."""
        key = self._current_subtab
        qkey = _search_key(self._search_box.text())
        for w in self.item_widgets:
            src = getattr(w, "_source", "")
            subtab_ok = key == "all" or src == key
            search_ok = _matches_query(qkey, w._search_key)
            visible = subtab_ok and search_ok
            w.setVisible(visible)
            if w._sep:
                w._sep.setVisible(visible)
        self.update_select_btn()

    def _make_search_panel(self) -> QWidget:
        self._search_panel = QFrame()
        self._search_panel.setStyleSheet(
            "background:#181818; border-bottom:1px solid #2a2a2a;"
        )
        self._search_panel.setVisible(False)
        sp = QHBoxLayout(self._search_panel)
        sp.setContentsMargins(16, 10, 16, 10)
        sp.setSpacing(8)

        self._tidal_query = QLineEdit()
        self._tidal_query.setPlaceholderText("Search Tidal…")
        self._tidal_query.setStyleSheet(_input_style(13))
        self._tidal_query.returnPressed.connect(self._emit_tidal_search)
        sp.addWidget(self._tidal_query, 1)

        self._search_type_combo = QComboBox()
        for label, val in [
            ("Playlists", "playlists"),
            ("Albums", "albums"),
            ("Artists", "artists"),
        ]:
            self._search_type_combo.addItem(label, userData=val)
        self._search_type_combo.setStyleSheet(
            "QComboBox{background:#222;border:1px solid #333;border-radius:4px;"
            "padding:4px 8px;color:#ccc;font-size:12px;min-width:100px;}"
            "QComboBox QAbstractItemView{background:#222;color:#ccc;border:1px solid #444;}"
        )
        sp.addWidget(self._search_type_combo)

        search_btn = QPushButton("Search")
        search_btn.setMinimumHeight(32)
        search_btn.setStyleSheet(
            "QPushButton{background:rgba(0,255,255,45);border:1px solid rgba(0,255,255,180);"
            "border-radius:6px;font-size:12px;font-weight:bold;padding:0 16px;}"
            "QPushButton:hover{background:rgba(0,255,255,75);}"
        )
        search_btn.clicked.connect(self._emit_tidal_search)
        sp.addWidget(search_btn)

        return self._search_panel

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
        self._path_edit.textChanged.connect(self.path_changed)
        row1.addWidget(self._path_edit, 1)

        browse = QPushButton("Browse…")
        browse.setStyleSheet(_action_btn_style())
        browse.clicked.connect(self.browse_requested)
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
        self._download_btn.clicked.connect(self.download_selected_requested)
        row1.addWidget(self._download_btn)

        lay.addLayout(row1)

        # Row 1b: direct URL input
        row1b = QHBoxLayout()
        row1b.setSpacing(10)

        lbl2 = QLabel("Direct URL:")
        lbl2.setStyleSheet("color:#777; font-size:12px;")
        row1b.addWidget(lbl2)

        self._url_edit = QLineEdit()
        self._url_edit.setPlaceholderText(
            "Paste a Tidal URL (playlist/album/artist)…"
        )
        self._url_edit.setStyleSheet(_input_style())
        self._url_edit.returnPressed.connect(self._emit_download_url)
        row1b.addWidget(self._url_edit, 1)

        dl_url_btn = QPushButton("Download URL")
        dl_url_btn.setMinimumHeight(36)
        dl_url_btn.setStyleSheet(
            "QPushButton{background:#222;border:1px solid #383838;border-radius:4px;"
            "padding:0 12px;color:#aaa;font-size:12px;}"
            "QPushButton:hover{border-color:#0ff;color:#0ff;}"
        )
        dl_url_btn.clicked.connect(self._emit_download_url)
        row1b.addWidget(dl_url_btn)

        lay.addLayout(row1b)

        # Row 2: progress bar (hidden until a download starts)
        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        self._progress_bar.setFixedHeight(5)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setStyleSheet(
            "QProgressBar{background:#1e1e1e;border:none;border-radius:2px;}"
            "QProgressBar::chunk{background:#0ff;border-radius:2px;}"
        )
        lay.addWidget(self._progress_bar)

        # Row 3: download status card (hidden until a download starts)
        self._dl_status_card = QFrame()
        self._dl_status_card.setVisible(False)
        self._dl_status_card.setStyleSheet(
            "background:#111; border:1px solid #1e1e1e; border-radius:6px; padding:0;"
        )
        card_lay = QVBoxLayout(self._dl_status_card)
        card_lay.setContentsMargins(8, 6, 8, 6)
        card_lay.setSpacing(0)

        # Row A — current track
        row_a = QHBoxLayout()
        row_a.setSpacing(8)

        self._dl_arrow = QLabel("↓")
        self._dl_arrow.setStyleSheet("color:#0ff; font-size:16px; font-weight:bold;")
        row_a.addWidget(self._dl_arrow)

        self._dl_track_lbl = QLabel("Preparing\u2026")
        self._dl_track_lbl.setStyleSheet("color:#ddd; font-size:12px;")
        self._dl_track_lbl.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        row_a.addWidget(self._dl_track_lbl)

        self._dl_quality_lbl = QLabel("")
        self._dl_quality_lbl.setStyleSheet("color:#555; font-size:11px;")
        row_a.addWidget(self._dl_quality_lbl)

        card_lay.addLayout(row_a)

        # 1px separator
        sep_line = QFrame()
        sep_line.setStyleSheet("background:#1e1e1e; max-height:1px;")
        sep_line.setMaximumHeight(1)
        card_lay.addWidget(sep_line)

        # Row B — counters
        row_b = QHBoxLayout()
        row_b.setSpacing(8)

        self._dl_task_lbl = QLabel("")
        self._dl_task_lbl.setStyleSheet("color:#777; font-size:11px;")
        row_b.addWidget(self._dl_task_lbl)

        row_b.addStretch()

        self._dl_count_lbl = QLabel("")
        self._dl_count_lbl.setStyleSheet(
            "color:#0c6; font-size:11px; font-weight:bold;"
        )
        row_b.addWidget(self._dl_count_lbl)

        card_lay.addLayout(row_b)

        lay.addWidget(self._dl_status_card)

        return bottom

    def _apply_theme(self) -> None:
        self.setStyleSheet(
            "QMainWindow,QWidget{background:#1a1a1a;color:#ddd;}"
            "QScrollBar:vertical{background:#1a1a1a;width:7px;border-radius:3px;}"
            "QScrollBar::handle:vertical{background:#2e2e2e;border-radius:3px;}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}"
            "QCheckBox::indicator{width:16px;height:16px;border-radius:3px;"
            "border:1px solid #444;background:#1e1e1e;}"
            "QCheckBox::indicator:checked{background:#0ff;border-color:#0ff;}"
        )

    # ── Internal signal forwarders ────────────────────────────────────────────

    def _emit_tidal_search(self) -> None:
        query = self._tidal_query.text().strip()
        if query:
            self.tidal_search_requested.emit(
                query, self._search_type_combo.currentData()
            )

    def _emit_download_url(self) -> None:
        self.download_url_requested.emit(self._url_edit.text().strip())
        self._url_edit.clear()

    # ── Public API (called by presenter) ─────────────────────────────────────

    def set_tab_active(self, tab: str) -> None:
        """Highlight *tab* in the sidebar and reset filter/select state."""
        for key, btn in self._tab_buttons.items():
            btn.setChecked(key == tab)
        self._select_btn.setChecked(False)
        self._select_btn.setText("Select All")
        self._search_box.blockSignals(True)
        self._search_box.clear()
        self._search_box.blockSignals(False)
        # Playlist sub-tabs are only meaningful on the Playlists tab
        self._pl_subtabs.setVisible(tab == "playlists")
        # Downloaded dashboard strip only on the Downloaded tab
        self._dl_dashboard.setVisible(tab == "downloaded")
        if tab == "playlists":
            # Reset to "All" when (re)entering the tab
            for k, b in self._pl_subtab_btns.items():
                b.setChecked(k == "all")
            self._current_subtab = "all"

    def set_tab_title(self, title: str) -> None:
        """Set the large title label in the top bar."""
        self._tab_title.setText(title)

    def clear_list(self) -> None:
        """Remove all item widgets from the grid and show the loading label."""
        self.item_widgets.clear()
        # Take everything; delete card widgets, keep the loading label.
        i = self._list_layout.count() - 1
        while i >= 0:
            item = self._list_layout.itemAt(i)
            w = item.widget() if item else None
            if w is None:
                self._list_layout.takeAt(i)
            elif w is not self._loading_label:
                self._list_layout.takeAt(i)
                w.setParent(None)
                w.deleteLater()
            else:
                self._list_layout.takeAt(i)
            i -= 1
        self._loading_label.setText("Loading…")
        self._loading_label.setVisible(True)
        self._list_layout.addWidget(
            self._loading_label, 0, 0, 1, self._grid_columns
        )
        self.update_select_btn()

    def add_item(self, vm: CardVM) -> None:
        """Append a library item card to the grid.

        Args:
            vm: Fully-populated :class:`CardVM` DTO; the view does not
                import or inspect any Tidal core models.
        """
        # Hide loading label on first item
        idx = self._list_layout.indexOf(self._loading_label)
        if idx >= 0:
            self._list_layout.removeWidget(self._loading_label)
            self._loading_label.setVisible(False)

        widget = LibraryItemWidget(vm)
        widget._sep = None  # presenter checks `if w._sep:` — always falsy now
        widget.check_changed.connect(self.update_select_btn)
        widget.detail_requested.connect(self.detail_requested)

        n = len(self.item_widgets)
        row, col = divmod(n, self._grid_columns)
        self._list_layout.addWidget(widget, row, col)
        self.item_widgets.append(widget)
        widget.play_fade_in()

        # Apply any active search filter + sub-tab filter immediately
        qkey = _search_key(self._search_box.text())
        source = vm.source
        subtab_ok = (
            getattr(self, "_current_subtab", "all") in ("all", source)
        ) if source else True
        search_ok = _matches_query(qkey, widget._search_key)
        visible = subtab_ok and search_ok
        if not visible:
            widget.setVisible(False)

    def set_loading_text(self, msg: str) -> None:
        """Set text on the loading/empty-state label and make it visible.

        If the label was previously removed from the layout it is re-inserted.
        """
        self._loading_label.setText(msg)
        idx = self._list_layout.indexOf(self._loading_label)
        if idx < 0:
            self._list_layout.addWidget(
                self._loading_label, 0, 0, 1, self._grid_columns
            )
        self._loading_label.setVisible(True)

    # ── Responsive grid ──────────────────────────────────────────────────────

    def _on_grid_resize(self) -> None:
        """Recompute grid column count on container resize."""
        avail = self._list_container.width() - (GRID_MARGIN * 2)
        col_w = CARD_W + CARD_SPACING_H
        new_cols = max(1, avail // col_w)
        if new_cols != self._grid_columns:
            self._grid_columns = int(new_cols)
            self._relayout_grid()

    def _relayout_grid(self) -> None:
        """Re-place every card widget at its new (row, col) after a column
        count change. Also re-spans the loading label.
        """
        # Detach every card from the layout
        for w in self.item_widgets:
            self._list_layout.removeWidget(w)
        loading_visible = self._loading_label.isVisible()
        if self._list_layout.indexOf(self._loading_label) >= 0:
            self._list_layout.removeWidget(self._loading_label)

        # Re-add cards at new grid positions
        for i, w in enumerate(self.item_widgets):
            row, col = divmod(i, self._grid_columns)
            self._list_layout.addWidget(w, row, col)

        # Re-add loading label spanning the full row if it was visible
        if loading_visible:
            row = (len(self.item_widgets) + self._grid_columns - 1) // self._grid_columns
            self._list_layout.addWidget(
                self._loading_label, row, 0, 1, self._grid_columns
            )
            self._loading_label.setVisible(True)

    def show_search_panel(self, visible: bool) -> None:
        """Show or hide the Tidal search input panel."""
        self._search_panel.setVisible(visible)

    def show_progress_bar(self, total: int) -> None:
        """Reset and show the progress bar with *total* steps."""
        self._progress_bar.setMaximum(total)
        self._progress_bar.setValue(0)
        self._progress_bar.setVisible(True)
        self._dl_status_card.setVisible(True)
        self._dl_track_lbl.setText("Preparing\u2026")
        self._dl_quality_lbl.setText("")
        self._dl_task_lbl.setText(f"0 / {total} items")
        self._dl_count_lbl.setText("0 tracks")

    def set_download_progress(self, done: int, total: int) -> None:
        """Update the progress bar value."""
        self._progress_bar.setMaximum(total)
        self._progress_bar.setValue(done)
        self._dl_task_lbl.setText(f"{done} / {total} items")

    def append_log(self, text: str) -> None:  # noqa: ARG002
        """No-op: log widget replaced by status card."""
        pass

    def show_log(self) -> None:
        """No-op: log widget replaced by status card."""
        pass

    def set_current_track(self, name: str, quality: str = "") -> None:
        """Show which track is currently downloading."""
        self._dl_track_lbl.setText(name)
        self._dl_quality_lbl.setText(quality)

    def set_track_count(self, done: int) -> None:
        """Update the downloaded track counter."""
        self._dl_count_lbl.setText(f"{done} track{'s' if done != 1 else ''}")

    def show_download_error(self, message: str) -> None:
        """Flash a red failure indicator on the download status card.

        Sets the track label to a short "Failed" summary with a tooltip
        containing the full error message (cleared after 10s). Also
        paints the down-arrow red for the same duration so the user
        can't miss that something went wrong.
        """
        from PySide6.QtCore import QTimer  # local import — keeps top clean
        # Make sure the card is visible even if a prior success hid it.
        self._dl_status_card.setVisible(True)
        short = message.strip()
        # Keep it to one line; the full text lives in the tooltip.
        first_line = short.splitlines()[0] if short else "Download failed"
        self._dl_track_lbl.setText(first_line[:120])
        self._dl_track_lbl.setToolTip(short)
        self._dl_track_lbl.setStyleSheet("color:#f06060; font-size:12px;")
        self._dl_arrow.setText("\u26a0")
        self._dl_arrow.setStyleSheet(
            "color:#f06060; font-size:16px; font-weight:bold;"
        )

        def _clear() -> None:
            try:
                self._dl_track_lbl.setToolTip("")
                self._dl_track_lbl.setStyleSheet("color:#ddd; font-size:12px;")
            except RuntimeError:
                pass

        QTimer.singleShot(10_000, _clear)

    def hide_download_status(self) -> None:
        """Hide the download status card and progress bar after completion."""
        # Don't hide immediately — leave visible so user can see the final state.
        self._dl_track_lbl.setText("Done")
        self._dl_arrow.setText("\u2713")
        self._dl_arrow.setStyleSheet("color:#0c6; font-size:16px; font-weight:bold;")

    def set_download_btn_text(self, text: str) -> None:
        """Set the text of the Download Selected button."""
        self._download_btn.setText(text)

    def set_download_btn_enabled(self, enabled: bool) -> None:
        """Enable or disable the Download Selected button."""
        self._download_btn.setEnabled(enabled)

    def set_select_btn_text(self, text: str) -> None:
        """Set the text of the Select All / Deselect All button."""
        self._select_btn.setText(text)

    def get_download_path(self) -> str:
        """Return the current value of the download path field."""
        return self._path_edit.text().strip()

    def set_download_path(self, path: str) -> None:
        """Set the download path field to *path*."""
        self._path_edit.setText(path)

    def get_quality(self) -> str:
        """Return the currently selected quality value."""
        return self._quality_combo.currentData()

    def get_checked_urls(self) -> list[str]:
        """Return URLs for all visible, checked items that have a URL."""
        return [
            w.get_url()
            for w in self.item_widgets
            if w.isVisible() and w.is_checked() and w.get_url()
        ]

    def get_checked_items_without_url(self) -> list[str]:
        """Return titles of visible, checked items that have no URL."""
        return [
            w.get_title()
            for w in self.item_widgets
            if w.isVisible() and w.is_checked() and not w.get_url()
        ]

    def focus_tidal_search(self) -> None:
        """Give keyboard focus to the Tidal search query field."""
        self._tidal_query.setFocus()

    def get_tidal_query(self) -> str:
        """Return the current text of the Tidal search field."""
        return self._tidal_query.text().strip()

    def get_search_type(self) -> str:
        """Return the currently selected search type value."""
        return self._search_type_combo.currentData()

    def update_select_btn(self) -> None:
        """Synchronise the Download Selected button count and the Select All
        toggle button state based on the current visible selection.
        """
        visible_widgets = [w for w in self.item_widgets if w.isVisible()]
        checked_widgets = [w for w in visible_widgets if w.is_checked()]
        n = len(checked_widgets)
        total_visible = len(visible_widgets)

        # Update download button label
        if n:
            self._download_btn.setText(f"Download Selected  ({n})")
        else:
            self._download_btn.setText("Download Selected")

        # Sync select/deselect toggle without triggering the signal
        self._select_btn.blockSignals(True)
        if n == 0:
            self._select_btn.setChecked(False)
            self._select_btn.setText("Select All")
        elif n == total_visible and total_visible > 0:
            self._select_btn.setChecked(True)
            self._select_btn.setText("Deselect All")
        # Partial selection: leave the button state as-is
        self._select_btn.blockSignals(False)

    def refresh_badges(self, cache) -> None:
        """Refresh the downloaded badge on every item widget.

        Recomputes ``is_downloaded`` per card from the DTO the card was
        built from. Kept here (rather than the presenter) for back-compat
        with any caller that already passes a DiskCache in.

        Args:
            cache: Updated DiskCache instance, or None to clear all badges.
        """
        from app.models.card_mapper import compute_downloaded
        for w in self.item_widgets:
            w.refresh_downloaded(compute_downloaded(w._vm, cache))
