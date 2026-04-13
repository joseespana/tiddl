"""Centralised token lifecycle manager."""
import time
import logging
from typing import Optional

from tiddl.cli.utils.auth.core import load_auth_data, save_auth_data
from tiddl.cli.utils.auth.models import AuthData

log = logging.getLogger(__name__)

_EXPIRY_MARGIN = 60  # seconds before actual expiry to consider token stale


class TokenManager:
    """Singleton that owns the current session token.

    Wraps ``tiddl``'s existing ``AuthData`` / ``load_auth_data`` /
    ``save_auth_data`` so every part of the app gets tokens from one place.
    """
    _instance: "Optional[TokenManager]" = None

    def __new__(cls) -> "TokenManager":
        if cls._instance is None:
            inst = super().__new__(cls)
            inst._auth: Optional[AuthData] = None
            cls._instance = inst
        return cls._instance

    # ── Load / persist ────────────────────────────────────────────────────────

    def load_from_disk(self) -> None:
        """Populate from the on-disk ``~/.tiddl/auth.json`` (or equivalent)."""
        self._auth = load_auth_data()

    def save(
        self,
        access_token: str,
        refresh_token: str,
        expires_at: float,   # unix timestamp
        user_id: str,
        country_code: str,
    ) -> None:
        """Persist a new set of credentials."""
        auth = AuthData(
            token=access_token,
            refresh_token=refresh_token,
            expires_at=int(expires_at),
            user_id=user_id,
            country_code=country_code,
        )
        self._auth = auth
        save_auth_data(auth)

    def update_tokens(
        self,
        access_token: str,
        refresh_token: str,
        expires_in: int,
    ) -> None:
        """Update access/refresh tokens after a successful refresh."""
        if self._auth is None:
            log.warning("TokenManager.update_tokens called before load_from_disk")
            return
        self._auth.token = access_token
        self._auth.refresh_token = refresh_token
        self._auth.expires_at = int(time.time()) + expires_in
        save_auth_data(self._auth)

    def clear(self) -> None:
        """Wipe the in-memory token and remove on-disk credentials."""
        self._auth = None
        save_auth_data(AuthData())

    # ── Getters ───────────────────────────────────────────────────────────────

    def get_access_token(self) -> Optional[str]:
        """Return the access token if present and not expired."""
        if self._auth and self._auth.token and not self.is_expired():
            return self._auth.token
        return None

    def get_refresh_token(self) -> Optional[str]:
        return self._auth.refresh_token if self._auth else None

    def get_user_id(self) -> Optional[str]:
        return self._auth.user_id if self._auth else None

    def get_country_code(self) -> Optional[str]:
        return self._auth.country_code if self._auth else None

    def is_authenticated(self) -> bool:
        return bool(self._auth and self._auth.token and self._auth.user_id)

    def is_expired(self) -> bool:
        if not self._auth or not self._auth.expires_at:
            return True
        return time.time() >= self._auth.expires_at - _EXPIRY_MARGIN
