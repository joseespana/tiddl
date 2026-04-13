"""
Compatibility shim — re-exports from the refactored worker modules.

All public names from the original workers.py are preserved here so that
any existing import of ``app.workers`` continues to work without changes.
"""
from app.worker_index import (
    load_index,
    save_index,
    record_downloaded,
    INDEX_FILENAME,
    _TIDAL_URL_RE,
)
from app.worker_library import LibraryWorker
from app.worker_downloaded import DownloadedWorker
from app.worker_search import SearchWorker
from app.worker_download import DownloadWorker

__all__ = [
    "load_index",
    "save_index",
    "record_downloaded",
    "INDEX_FILENAME",
    "_TIDAL_URL_RE",
    "LibraryWorker",
    "DownloadedWorker",
    "SearchWorker",
    "DownloadWorker",
]
