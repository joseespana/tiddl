"""
Login window — Tidal device-code OAuth flow.
"""
import logging
import os
import subprocess
import sys
from time import time
from urllib.parse import urlparse

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QFont, QGuiApplication, QDesktopServices
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QFrame, QMessageBox,
)

log = logging.getLogger(__name__)

from tiddl.core.auth.api import AuthAPI
from tiddl.core.auth.exceptions import AuthClientError


_ALLOWED_HOSTS = {
    "tidal.com",
    "auth.tidal.com",
    "link.tidal.com",
    "listen.tidal.com",
}


def _normalize_url(url: str) -> str:
    """Ensure *url* has an https:// scheme (Tidal sometimes omits it)."""
    if url and not url.startswith(("http://", "https://")):
        return "https://" + url
    return url


def _is_safe_tidal_url(url: str) -> bool:
    """Return True iff *url* uses http/https and points at a Tidal host.

    Allows any subdomain of ``tidal.com`` plus the explicit allow-list
    above. Anything else (missing scheme, ``file://``, ``javascript:``,
    unrelated hosts, …) is rejected.
    """
    if not url:
        return False
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    if host in _ALLOWED_HOSTS:
        return True
    if host == "tidal.com" or host.endswith(".tidal.com"):
        return True
    return False


def _open_url(url: str) -> None:
    """Open *url* in the default browser, reliably on every platform.

    Refuses to launch anything that isn't a Tidal http(s) URL.
    """
    url = _normalize_url(url)
    if not _is_safe_tidal_url(url):
        log.warning("Refusing to open non-Tidal URL: %r", url)
        return
    if sys.platform == "darwin":
        subprocess.Popen(["open", url])
    elif sys.platform == "win32":
        # Use os.startfile (no shell) — avoids shell-injection risk
        # from subprocess.Popen([..., url], shell=True).
        try:
            os.startfile(url)  # type: ignore[attr-defined]
        except OSError as exc:
            log.warning("os.startfile failed for %r: %s", url, exc)
    else:
        QDesktopServices.openUrl(QUrl(url))
from tiddl.cli.utils.auth.core import save_auth_data
from tiddl.cli.utils.auth.models import AuthData


class AuthWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("tiddl — Connect to Tidal")
        self.setFixedSize(460, 390)
        self.setModal(True)

        self.auth_api = AuthAPI()
        self._device_resp = None
        self._auth_expires_at = 0
        self._verification_url = ""
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_auth)

        self._build_ui()

    # ── UI ──────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(36, 32, 36, 32)
        root.setSpacing(16)

        title = QLabel("tiddl")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = QFont()
        font.setPointSize(28)
        font.setBold(True)
        title.setFont(font)
        root.addWidget(title)

        sub = QLabel("Download your Tidal music in the best quality")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setStyleSheet("color: #888;")
        root.addWidget(sub)

        root.addSpacing(8)

        self._status_label = QLabel("Click below to connect your Tidal account.")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_label.setWordWrap(True)
        root.addWidget(self._status_label)

        # Code box (hidden until device flow starts)
        self._code_frame = QFrame()
        self._code_frame.setVisible(False)
        code_layout = QVBoxLayout(self._code_frame)
        code_layout.setContentsMargins(0, 0, 0, 0)
        code_layout.setSpacing(6)

        code_hint = QLabel("Enter this code at Tidal:")
        code_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        code_hint.setStyleSheet("color: #888; font-size: 12px;")
        code_layout.addWidget(code_hint)

        self._code_box = QLineEdit()
        self._code_box.setReadOnly(True)
        self._code_box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font_code = QFont("Courier New")
        font_code.setPointSize(20)
        font_code.setBold(True)
        font_code.setStyleHint(QFont.StyleHint.Monospace)
        self._code_box.setFont(font_code)
        self._code_box.setStyleSheet(
            "border: 2px solid #0ff; border-radius: 6px; "
            "padding: 8px; letter-spacing: 4px;"
        )
        code_layout.addWidget(self._code_box)

        # URL display (read-only, selectable)
        self._url_display = QLineEdit()
        self._url_display.setReadOnly(True)
        self._url_display.setStyleSheet(
            "background: #1a1a1a; border: 1px solid #333; border-radius: 4px;"
            "padding: 4px 8px; color: #4af; font-size: 11px;"
        )
        code_layout.addWidget(self._url_display)

        # Open + Copy buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self._url_btn = QPushButton("Open in Browser")
        self._url_btn.setMinimumHeight(32)
        self._url_btn.setStyleSheet(
            "QPushButton{background:rgba(0,255,255,45);border:1px solid rgba(0,255,255,180);"
            "border-radius:6px;font-size:12px;font-weight:bold;padding:0 12px;}"
            "QPushButton:hover{background:rgba(0,255,255,75);}"
        )
        self._url_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._url_btn.clicked.connect(self._open_browser)
        btn_row.addWidget(self._url_btn)

        self._copy_btn = QPushButton("Copy Link")
        self._copy_btn.setMinimumHeight(32)
        self._copy_btn.setStyleSheet(
            "QPushButton{background:#222;border:1px solid #444;border-radius:6px;"
            "font-size:12px;padding:0 12px;color:#aaa;}"
            "QPushButton:hover{border-color:#0ff;color:#0ff;}"
        )
        self._copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._copy_btn.clicked.connect(self._copy_link)
        btn_row.addWidget(self._copy_btn)

        code_layout.addLayout(btn_row)

        root.addWidget(self._code_frame)

        # Main action button
        self._action_btn = QPushButton("Connect with Tidal")
        self._action_btn.setMinimumHeight(44)
        self._action_btn.setStyleSheet(
            "QPushButton {"
            "  background: rgba(0,255,255,45); border: 1px solid rgba(0,255,255,200);"
            "  border-radius: 8px; font-size: 14px; font-weight: bold;"
            "  padding: 0 20px;"
            "}"
            "QPushButton:hover { background: rgba(0,255,255,75); }"
            "QPushButton:disabled { background: #333; color: #666; border-color: #444; }"
        )
        self._action_btn.clicked.connect(self._start_login)
        root.addWidget(self._action_btn)

        root.addStretch()

    # ── Actions ─────────────────────────────────────────────────────────────

    def _start_login(self):
        self._action_btn.setEnabled(False)
        self._action_btn.setText("Waiting for Tidal…")
        self._status_label.setText("Opening browser…")

        try:
            self._device_resp = self.auth_api.get_device_auth()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not reach Tidal:\n{e}")
            self._action_btn.setEnabled(True)
            self._action_btn.setText("Connect with Tidal")
            return

        self._auth_expires_at = time() + self._device_resp.expiresIn
        self._code_box.setText(self._device_resp.userCode)
        candidate_url = _normalize_url(self._device_resp.verificationUriComplete)
        if not _is_safe_tidal_url(candidate_url):
            log.warning(
                "Tidal returned suspicious verification URL %r — refusing",
                candidate_url,
            )
            QMessageBox.critical(
                self, "Auth Error",
                "Tidal returned an unexpected verification URL.\nPlease try again.",
            )
            self._action_btn.setEnabled(True)
            self._action_btn.setText("Connect with Tidal")
            return
        self._verification_url = candidate_url
        self._url_display.setText(self._verification_url)
        self._code_frame.setVisible(True)
        self._status_label.setText("Waiting for authorization…")

        # Try opening browser automatically
        _open_url(self._verification_url)

        interval_ms = max(self._device_resp.interval * 1000, 3000)
        self._poll_timer.start(interval_ms)

    def _open_browser(self):
        if self._device_resp:
            _open_url(self._verification_url)

    def _copy_link(self):
        if self._device_resp:
            QGuiApplication.clipboard().setText(self._verification_url)
            self._copy_btn.setText("Copied!")
            QTimer.singleShot(2000, lambda: self._copy_btn.setText("Copy Link"))

    def _poll_auth(self):
        if not self._device_resp:
            return

        if time() > self._auth_expires_at:
            self._poll_timer.stop()
            self._status_label.setText("Authorization timed out. Please try again.")
            self._action_btn.setEnabled(True)
            self._action_btn.setText("Connect with Tidal")
            self._code_frame.setVisible(False)
            self._device_resp = None
            return

        try:
            resp = self.auth_api.get_auth(self._device_resp.deviceCode)
        except AuthClientError as e:
            # authorization_pending is normal — keep polling silently
            if e.error != "authorization_pending":
                log.warning("Auth poll error: %s", e)
            return
        except Exception as e:
            # Transient network error — log but keep polling
            log.warning("Auth poll network error: %s", e)
            return

        # ── Authorization succeeded ───────────────────────────────────────────
        self._poll_timer.stop()
        try:
            auth_data = AuthData(
                token=resp.access_token,
                refresh_token=resp.refresh_token,
                expires_at=resp.expires_in + int(time()),
                user_id=str(resp.user_id),
                country_code=resp.user.countryCode,
            )
            save_auth_data(auth_data)
        except Exception as e:
            QMessageBox.critical(self, "Auth Error", f"Failed to save credentials:\n{e}")
            log.error("save_auth_data failed: %s", e)
            return

        self._status_label.setText(f"Connected as {resp.user.username}!")
        self.accept()
