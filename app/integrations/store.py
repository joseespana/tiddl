"""On-disk credential store for the third-party integrations.

Everything lives in ``~/.tiddl/integrations.json`` (next to tiddl's own
``auth.json``), one top-level key per provider. The file is written with
``0600`` permissions because it holds OAuth refresh tokens.
"""
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

from tiddl.cli.const import APP_PATH

log = logging.getLogger(__name__)

STORE_PATH: Path = APP_PATH / "integrations.json"

_lock = threading.Lock()


def _read_all() -> dict[str, Any]:
    try:
        return json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as exc:
        log.warning("Failed to read %s: %s", STORE_PATH, exc)
        return {}


def _write_all(all_data: dict[str, Any]) -> None:
    tmp = STORE_PATH.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(all_data, f, indent=2)
    os.replace(tmp, STORE_PATH)


def load(provider: str) -> dict[str, Any]:
    """Return the saved settings/tokens for *provider* (empty dict if none)."""
    with _lock:
        return dict(_read_all().get(provider, {}))


def save(provider: str, data: dict[str, Any]) -> None:
    """Merge *data* into the saved entry for *provider*."""
    with _lock:
        all_data = _read_all()
        entry = all_data.get(provider, {})
        entry.update(data)
        all_data[provider] = entry
        _write_all(all_data)


def clear_keys(provider: str, keys: list[str]) -> None:
    """Remove *keys* from the saved entry (used on disconnect)."""
    with _lock:
        all_data = _read_all()
        entry = all_data.get(provider, {})
        for k in keys:
            entry.pop(k, None)
        all_data[provider] = entry
        _write_all(all_data)
