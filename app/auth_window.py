"""
Login window — Tidal device-code OAuth flow.
"""
import webbrowser
from time import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QFrame, QMessageBox,
)

from tiddl.core.auth.api import AuthAPI
from tiddl.cli.utils.auth.core import save_auth_data
from tiddl.cli.utils.auth.models import AuthData


class AuthWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("tiddl — Connect to Tidal")
        self.setFixedSize(420, 340)
        self.setModal(True)

        self.auth_api = AuthAPI()
        self._device_resp = None
        self._auth_expires_at = 0
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
        font_code = QFont("Courier")
        font_code.setPointSize(20)
        font_code.setBold(True)
        self._code_box.setFont(font_code)
        self._code_box.setStyleSheet(
            "border: 2px solid #0ff; border-radius: 6px; "
            "padding: 8px; letter-spacing: 4px;"
        )
        code_layout.addWidget(self._code_box)

        self._url_btn = QPushButton()
        self._url_btn.setStyleSheet("color: #4af; text-decoration: underline; border: none;")
        self._url_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._url_btn.clicked.connect(self._open_browser)
        code_layout.addWidget(self._url_btn)

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
        self._url_btn.setText(self._device_resp.verificationUriComplete)
        self._code_frame.setVisible(True)
        self._status_label.setText("Waiting for authorization…")

        # Try opening browser automatically
        webbrowser.open(self._device_resp.verificationUriComplete)

        interval_ms = max(self._device_resp.interval * 1000, 3000)
        self._poll_timer.start(interval_ms)

    def _open_browser(self):
        if self._device_resp:
            webbrowser.open(self._device_resp.verificationUriComplete)

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
        except Exception:
            # Not yet authorized — keep polling
            return

        self._poll_timer.stop()

        auth_data = AuthData(
            token=resp.access_token,
            refresh_token=resp.refresh_token,
            expires_at=resp.expires_in + int(time()),
            user_id=str(resp.user_id),
            country_code=resp.user.countryCode,
        )
        save_auth_data(auth_data)

        self._status_label.setText(f"Connected as {resp.user.username}!")
        self.accept()
