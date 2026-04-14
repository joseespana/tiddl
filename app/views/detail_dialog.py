"""
DetailDialog — modal view of an album's/playlist's tracks or an artist's albums.

Pure UI: reads only the :class:`DetailVM` DTO and never imports any
Tidal core models. Reuses :class:`CoverLabel` from :mod:`main_view` for
network-backed images so the existing requests_cache layer is leveraged.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.models.detail_vm import DetailVM, TrackRow, AlbumRow
from app.views.main_view import CoverLabel

# Dialog dimensions (match existing dark theme).
_DIALOG_W = 720
_DIALOG_H = 540
_LEFT_W = 260
_BIG_COVER = 220
_ALBUM_ROW_COVER = 48
_ROW_H = 36


def _format_duration(total_s: int) -> str:
    if total_s <= 0:
        return ""
    h, rem = divmod(int(total_s), 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _quality_pill_qss() -> str:
    # Matches the downloaded badge look in main_view.
    return (
        "background:rgba(0,200,100,30); color:#0c6;"
        "border:1px solid rgba(0,200,100,120); border-radius:3px;"
        "font-size:9px; font-weight:600; padding:1px 5px; letter-spacing:0.5px;"
    )


def _row_frame_qss() -> str:
    return (
        "QFrame#DetailRow{background:transparent;border-bottom:1px solid #222;}"
        "QFrame#DetailRow:hover{background:#252525;}"
    )


class DetailDialog(QDialog):
    """Modal dialog that shows either track list or album list for a card."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowType.Dialog, True)
        self.setModal(True)
        self.setFixedSize(_DIALOG_W, _DIALOG_H)
        self.setStyleSheet("QDialog{background:#1a1a1a;} QLabel{color:#ddd;}")

        self._vm: Optional[DetailVM] = None

        root = QHBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(16)

        # ── Left column ──────────────────────────────────────────────────────
        left = QWidget(self)
        left.setFixedWidth(_LEFT_W)
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(10)

        self._cover = CoverLabel(None, size=_BIG_COVER, parent=left)
        self._cover.setStyleSheet("background:#0a0a0a; border-radius:8px;")
        left_lay.addWidget(self._cover, 0, Qt.AlignmentFlag.AlignHCenter)

        self._title_lbl = QLabel("", left)
        self._title_lbl.setWordWrap(True)
        self._title_lbl.setStyleSheet(
            "color:#eee; font-weight:bold; font-size:16px; background:transparent;"
        )
        self._title_lbl.setMaximumWidth(_LEFT_W)
        left_lay.addWidget(self._title_lbl)

        self._sub_lbl = QLabel("", left)
        self._sub_lbl.setWordWrap(True)
        self._sub_lbl.setStyleSheet(
            "color:#888; font-size:11px; background:transparent;"
        )
        self._sub_lbl.setMaximumWidth(_LEFT_W)
        left_lay.addWidget(self._sub_lbl)

        left_lay.addStretch()

        close_btn = QPushButton("Close", left)
        close_btn.setStyleSheet(
            "QPushButton{background:#222;border:1px solid #383838;border-radius:4px;"
            "padding:6px 14px;color:#aaa;font-size:12px;}"
            "QPushButton:hover{border-color:#0ff;color:#0ff;}"
        )
        close_btn.clicked.connect(self.accept)
        left_lay.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignLeft)

        root.addWidget(left)

        # ── Right column (scroll area with content swapped in show_vm) ───────
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet(
            "QScrollArea{background:#141414;border:1px solid #222;border-radius:6px;}"
            "QWidget#DetailContent{background:#141414;}"
        )
        self._content = QWidget()
        self._content.setObjectName("DetailContent")
        self._content_lay = QVBoxLayout(self._content)
        self._content_lay.setContentsMargins(0, 0, 0, 0)
        self._content_lay.setSpacing(0)
        self._scroll.setWidget(self._content)
        root.addWidget(self._scroll, 1)

        self._loading_lbl: Optional[QLabel] = None
        self._show_loading_placeholder()

    # ── Public API ────────────────────────────────────────────────────────────

    @classmethod
    def open_for(
        cls,
        parent,
        title: str,
        kind: str,
        cover_url: Optional[str],
    ) -> "DetailDialog":
        """Construct a dialog pre-populated with loading state and ``show()`` it.

        The presenter typically calls this synchronously, then fills in the
        actual content via :meth:`show_vm` once the worker emits ``ready``.
        """
        dlg = cls(parent)
        dlg.show_loading(title, kind)
        if cover_url:
            # Rebuild the left cover with the real URL — CoverLabel kicks off
            # the async fetch in its constructor.
            dlg._replace_cover(cover_url)
        dlg.show()
        return dlg

    def show_loading(self, title: str, kind: str) -> None:
        """Display the header placeholder before the detail data arrives."""
        self._title_lbl.setText(title or "")
        label = {
            "album": "Loading album\u2026",
            "playlist": "Loading playlist\u2026",
            "artist": "Loading artist\u2026",
        }.get(kind, "Loading\u2026")
        self._sub_lbl.setText(label)
        self._show_loading_placeholder()

    def show_vm(self, vm: DetailVM) -> None:
        """Render the full detail view for *vm*."""
        self._vm = vm
        self._title_lbl.setText(vm.title or "")
        self._sub_lbl.setText(vm.subtitle or "")
        if vm.cover_url:
            self._replace_cover(vm.cover_url)
        self._clear_content()

        if vm.kind == "artist":
            self._render_albums(vm.albums)
        else:
            self._render_tracks(vm.tracks)

        self._content_lay.addStretch(1)

    def show_error(self, msg: str) -> None:
        """Replace the content area with a red warning label."""
        self._clear_content()
        lbl = QLabel(f"\u26a0 {msg}", self._content)
        lbl.setStyleSheet(
            "color:#f66; font-size:12px; background:transparent; padding:20px;"
        )
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setWordWrap(True)
        self._content_lay.addWidget(lbl)
        self._content_lay.addStretch(1)

    # ── Rendering helpers ─────────────────────────────────────────────────────

    def _show_loading_placeholder(self) -> None:
        self._clear_content()
        lbl = QLabel("Loading\u2026", self._content)
        lbl.setStyleSheet(
            "color:#888; font-size:13px; background:transparent; padding:28px;"
        )
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loading_lbl = lbl
        self._content_lay.addWidget(lbl)
        self._content_lay.addStretch(1)

    def _clear_content(self) -> None:
        while self._content_lay.count():
            item = self._content_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._loading_lbl = None

    def _replace_cover(self, cover_url: str) -> None:
        """Swap the big left cover for one that fetches *cover_url*."""
        parent = self._cover.parentWidget()
        layout = parent.layout() if parent is not None else None
        if layout is None:
            return
        idx = layout.indexOf(self._cover)
        if idx < 0:
            return
        layout.takeAt(idx)
        self._cover.deleteLater()
        new_cover = CoverLabel(cover_url, size=_BIG_COVER, parent=parent)
        new_cover.setStyleSheet("background:#0a0a0a; border-radius:8px;")
        layout.insertWidget(idx, new_cover, 0, Qt.AlignmentFlag.AlignHCenter)
        self._cover = new_cover

    def _render_tracks(self, tracks: list[TrackRow]) -> None:
        if not tracks:
            self._content_lay.addWidget(self._empty_label("No tracks."))
            return
        for row in tracks:
            self._content_lay.addWidget(self._make_track_row(row))

    def _render_albums(self, albums: list[AlbumRow]) -> None:
        if not albums:
            self._content_lay.addWidget(self._empty_label("No albums."))
            return
        for row in albums:
            self._content_lay.addWidget(self._make_album_row(row))

    def _empty_label(self, text: str) -> QLabel:
        lbl = QLabel(text, self._content)
        lbl.setStyleSheet(
            "color:#888; font-size:12px; background:transparent; padding:20px;"
        )
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return lbl

    def _make_track_row(self, row: TrackRow) -> QFrame:
        frame = QFrame(self._content)
        frame.setObjectName("DetailRow")
        frame.setFixedHeight(_ROW_H)
        frame.setStyleSheet(_row_frame_qss())
        lay = QHBoxLayout(frame)
        lay.setContentsMargins(10, 0, 10, 0)
        lay.setSpacing(10)

        num = QLabel(str(row.number), frame)
        num.setFixedWidth(26)
        num.setStyleSheet("color:#666; font-size:11px; background:transparent;")
        num.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(num)

        title = QLabel(frame)
        title.setStyleSheet("color:#eee; font-size:12px; background:transparent;")
        title_font = title.font()
        fm = QFontMetrics(title_font)
        title.setText(fm.elidedText(row.title, Qt.TextElideMode.ElideRight, 260))
        title.setToolTip(row.title)
        title.setFixedWidth(270)
        lay.addWidget(title)

        artist = QLabel(frame)
        artist.setStyleSheet("color:#888; font-size:11px; background:transparent;")
        fm2 = QFontMetrics(artist.font())
        artist.setText(fm2.elidedText(row.artist, Qt.TextElideMode.ElideRight, 150))
        artist.setToolTip(row.artist)
        lay.addWidget(artist, 1)

        dur = QLabel(_format_duration(row.duration_s), frame)
        dur.setStyleSheet("color:#888; font-size:11px; background:transparent;")
        dur.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        dur.setFixedWidth(56)
        lay.addWidget(dur)

        if row.quality:
            q = QLabel(row.quality, frame)
            q.setStyleSheet(_quality_pill_qss())
            lay.addWidget(q)

        return frame

    def _make_album_row(self, row: AlbumRow) -> QFrame:
        frame = QFrame(self._content)
        frame.setObjectName("DetailRow")
        frame.setFixedHeight(_ALBUM_ROW_COVER + 12)
        frame.setStyleSheet(_row_frame_qss())
        lay = QHBoxLayout(frame)
        lay.setContentsMargins(8, 6, 10, 6)
        lay.setSpacing(10)

        cover = CoverLabel(row.cover_url, size=_ALBUM_ROW_COVER, parent=frame)
        cover.setStyleSheet("background:#0a0a0a; border-radius:4px;")
        lay.addWidget(cover)

        title = QLabel(frame)
        title.setStyleSheet(
            "color:#eee; font-size:12px; font-weight:bold; background:transparent;"
        )
        fm = QFontMetrics(title.font())
        title.setText(fm.elidedText(row.title, Qt.TextElideMode.ElideRight, 280))
        title.setToolTip(row.title)
        title.setFixedWidth(290)
        lay.addWidget(title)

        year = QLabel(row.year or "", frame)
        year.setStyleSheet("color:#888; font-size:11px; background:transparent;")
        year.setFixedWidth(48)
        lay.addWidget(year)

        tracks = QLabel(
            f"{row.num_tracks} tracks" if row.num_tracks else "",
            frame,
        )
        tracks.setStyleSheet("color:#888; font-size:11px; background:transparent;")
        lay.addWidget(tracks, 1)

        return frame
