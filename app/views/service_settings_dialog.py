"""Credentials dialog for the Spotify / Apple Music integrations."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.integrations import store
from app.integrations.apple_music import AppleMusicClient
from app.integrations.spotify import REDIRECT_URI, SpotifyClient

_QSS = (
    "QDialog{background:#1a1a1a;color:#ddd;}"
    "QLabel{color:#bbb;font-size:12px;}"
    "QLineEdit{background:#222;border:1px solid #333;border-radius:4px;"
    "padding:5px 8px;color:#ccc;font-size:12px;}"
    "QPushButton{background:#222;border:1px solid #383838;border-radius:4px;"
    "padding:5px 14px;color:#aaa;font-size:12px;}"
    "QPushButton:hover{border-color:#0ff;color:#0ff;}"
)


def _help(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setTextFormat(Qt.TextFormat.RichText)
    lbl.setOpenExternalLinks(True)
    lbl.setStyleSheet("color:#888;font-size:11px;")
    return lbl


class ServiceSettingsDialog(QDialog):
    """Edit the credentials tiddl needs before it can sign in to *provider*."""

    def __init__(self, provider: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._provider = provider
        self.setStyleSheet(_QSS)
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        form = QFormLayout()
        form.setSpacing(8)
        cfg = store.load(provider)

        if provider == "spotify":
            self.setWindowTitle("Spotify settings")
            lay.addWidget(_help(
                "1. Create an app at "
                "<a style='color:#0ff' href='https://developer.spotify.com/dashboard'>"
                "developer.spotify.com/dashboard</a> (Web API).<br>"
                f"2. Add <b>{REDIRECT_URI}</b> as a Redirect URI.<br>"
                "3. Paste the app's Client ID below. No client secret is needed (PKCE)."
            ))
            self._client_id = QLineEdit(cfg.get("client_id", ""))
            self._client_id.setPlaceholderText("32-character Client ID")
            form.addRow("Client ID", self._client_id)
        else:
            self.setWindowTitle("Apple Music settings")
            lay.addWidget(_help(
                "Apple Music needs a MusicKit developer token from an "
                "<a style='color:#0ff' href='https://developer.apple.com/account/resources/authkeys/list'>"
                "Apple Developer account</a>. Either paste a ready-made token, "
                "or fill Team ID + Key ID + the .p8 key file and tiddl signs one "
                "locally (valid 6 months, renewed automatically)."
            ))
            self._dev_token = QLineEdit(cfg.get("developer_token", ""))
            self._dev_token.setPlaceholderText("eyJhbGciOiJFUzI1NiIs…  (optional)")
            self._dev_token.setEchoMode(QLineEdit.EchoMode.Password)
            form.addRow("Developer token", self._dev_token)
            self._team_id = QLineEdit(cfg.get("team_id", ""))
            self._team_id.setPlaceholderText("10-character Team ID")
            form.addRow("Team ID", self._team_id)
            self._key_id = QLineEdit(cfg.get("key_id", ""))
            self._key_id.setPlaceholderText("10-character Key ID")
            form.addRow("Key ID", self._key_id)
            p8_row = QHBoxLayout()
            self._p8 = QLineEdit(cfg.get("p8_path", ""))
            self._p8.setPlaceholderText("AuthKey_XXXXXXXXXX.p8")
            p8_row.addWidget(self._p8, 1)
            browse = QPushButton("Browse…")
            browse.clicked.connect(self._browse_p8)
            p8_row.addWidget(browse)
            form.addRow("Private key", p8_row)

        lay.addLayout(form)

        self._error = QLabel("")
        self._error.setStyleSheet("color:#f66;font-size:11px;")
        lay.addWidget(self._error)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _browse_p8(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "MusicKit private key", "", "Private key (*.p8);;All files (*)"
        )
        if path:
            self._p8.setText(path)

    def _save(self) -> None:
        if self._provider == "spotify":
            cid = self._client_id.text().strip()
            if not cid:
                self._error.setText("Client ID is required.")
                return
            if cid != SpotifyClient().client_id:
                # Tokens belong to the old app — force a fresh sign-in.
                SpotifyClient.disconnect()
            SpotifyClient.set_client_id(cid)
        else:
            token = self._dev_token.text().strip()
            team, key, p8 = (
                self._team_id.text().strip(),
                self._key_id.text().strip(),
                self._p8.text().strip(),
            )
            if not token and not (team and key and p8):
                self._error.setText(
                    "Paste a developer token, or fill Team ID, Key ID and the .p8 file."
                )
                return
            AppleMusicClient.save_settings(token, team, key, p8)
        self.accept()
