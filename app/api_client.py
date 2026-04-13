"""
Builds a TidalAPI instance from saved auth data.
"""
from time import time
from pathlib import Path

from tiddl.core.api import TidalClient, TidalAPI
from tiddl.core.auth import AuthAPI
from tiddl.cli.config import APP_PATH
from tiddl.cli.utils.auth.core import load_auth_data, save_auth_data


def build_api() -> TidalAPI:
    auth_data = load_auth_data()
    assert auth_data.token, "Not authenticated"
    assert auth_data.user_id
    assert auth_data.country_code
    assert auth_data.refresh_token

    auth_api = AuthAPI()
    refresh_token = auth_data.refresh_token

    def on_token_expiry() -> str | None:
        resp = auth_api.refresh_token(refresh_token)
        auth_data.token = resp.access_token
        auth_data.expires_at = resp.expires_in + int(time())
        save_auth_data(auth_data)
        return resp.access_token

    client = TidalClient(
        token=auth_data.token,
        cache_name=APP_PATH / "api_cache",
        omit_cache=False,
        debug_path=None,
        on_token_expiry=on_token_expiry,
    )
    return TidalAPI(client, auth_data.user_id, auth_data.country_code)


def is_authenticated() -> bool:
    data = load_auth_data()
    return bool(data.token and data.user_id)
