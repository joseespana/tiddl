"""
Index helpers — load/save/record downloaded Tidal resources.

Tracks which Tidal UUIDs / IDs have been downloaded so badges work
even when playlist/album titles change on Tidal.
"""
import json
import re
from pathlib import Path

_TIDAL_URL_RE = re.compile(r'(playlist|album|artist)/([a-zA-Z0-9\-]+)')

# Local index file stored inside the download folder.
INDEX_FILENAME = ".tiddl_index.json"


def load_index(download_path: str) -> dict:
    """Load the local index from the download directory.

    Args:
        download_path: Path to the download directory.

    Returns:
        Parsed index dict, or empty dict on any error.
    """
    f = Path(download_path) / INDEX_FILENAME
    try:
        return json.loads(f.read_text())
    except Exception:
        return {}


def save_index(download_path: str, index: dict) -> None:
    """Persist the index to disk.

    Args:
        download_path: Path to the download directory.
        index: Index dict to write.
    """
    f = Path(download_path) / INDEX_FILENAME
    try:
        f.write_text(json.dumps(index, indent=2))
    except Exception:
        pass


def record_downloaded(download_path: str, url: str) -> None:
    """Add a Tidal URL's resource ID and full URL to the local index.

    Args:
        download_path: Path to the download directory.
        url: Full Tidal URL (e.g. https://tidal.com/playlist/uuid).
    """
    m = _TIDAL_URL_RE.search(url)
    if not m:
        return
    rtype, rid = m.groups()
    index = load_index(download_path)
    bucket = index.setdefault(rtype, [])
    if rid not in bucket:
        bucket.append(rid)
    # Store full URL so the Downloaded tab can reconstruct items via API
    urls = index.setdefault("urls", [])
    if url not in urls:
        urls.append(url)
    save_index(download_path, index)
