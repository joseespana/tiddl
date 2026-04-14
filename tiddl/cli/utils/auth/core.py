import os
from pathlib import Path
from logging import getLogger

from tiddl.cli.config import APP_PATH
from .models import AuthData


AUTH_DATA_FILE = APP_PATH / "auth.json"


log = getLogger(__name__)


def load_auth_data(file: Path = AUTH_DATA_FILE) -> AuthData:
    log.debug(f"loading from '{AUTH_DATA_FILE}'")

    try:
        file_content = file.read_text()
    except FileNotFoundError:
        return AuthData()

    auth_data = AuthData.model_validate_json(file_content)

    return auth_data


def save_auth_data(auth_data: AuthData, file: Path = AUTH_DATA_FILE):
    """Persist *auth_data* to *file* atomically with restrictive perms.

    - Parent dir is created (if missing) with mode 0o700.
    - Contents are written to a ``.tmp`` sibling first, then moved into
      place with ``os.replace`` so a crashed write never leaves a
      partial file.
    - On POSIX the final file is chmod'd to 0o600. On Windows (``nt``)
      chmod is a no-op, so we skip it — ACLs already protect the user's
      home directory.
    """
    log.debug(f"saving to '{file}'")

    file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    tmp = file.with_suffix(file.suffix + ".tmp")
    payload = auth_data.model_dump_json()
    with tmp.open("w") as f:
        f.write(payload)
    os.replace(tmp, file)

    if os.name != "nt":
        try:
            os.chmod(file, 0o600)
        except OSError as exc:
            log.warning("chmod 0600 on %s failed: %s", file, exc)
