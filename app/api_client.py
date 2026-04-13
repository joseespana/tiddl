"""
Builds a TidalAPI instance from saved auth data.
"""
from pathlib import Path

from tiddl.core.api import TidalClient, TidalAPI
from tiddl.core.auth import AuthAPI
from tiddl.cli.config import APP_PATH

from app.auth.token_manager import TokenManager


def build_api() -> TidalAPI:
    token_manager = TokenManager()
    token_manager.load_from_disk()

    assert token_manager.get_access_token() or (
        token_manager._auth and token_manager._auth.token
    ), "Not authenticated"
    assert token_manager.get_user_id()
    assert token_manager.get_country_code()
    assert token_manager.get_refresh_token()

    auth_api = AuthAPI()
    refresh_token = token_manager.get_refresh_token()

    def on_token_expiry() -> str | None:
        resp = auth_api.refresh_token(refresh_token)
        token_manager.update_tokens(
            resp.access_token,
            resp.refresh_token,
            resp.expires_in,
        )
        return resp.access_token

    client = TidalClient(
        token=token_manager._auth.token,
        cache_name=APP_PATH / "api_cache",
        omit_cache=False,
        debug_path=None,
        on_token_expiry=on_token_expiry,
    )
    return TidalAPI(client, token_manager.get_user_id(), token_manager.get_country_code())


def is_authenticated() -> bool:
    tm = TokenManager()
    tm.load_from_disk()
    return tm.is_authenticated()
