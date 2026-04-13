"""
Background workers (QThread) for library loading and downloading.
"""
import subprocess
import sys
from typing import Literal

from PySide6.QtCore import QThread, Signal

from tiddl.core.api.api import TidalAPI
from tiddl.core.api.models import Playlist, Album, Artist
from tiddl.core.api.models.base import Favorites

LibraryTab = Literal["playlists", "albums", "artists"]


class LibraryWorker(QThread):
    """Loads user's playlists / albums / artists from Tidal API."""

    item_ready = Signal(object)   # emits Playlist | Album | Artist
    finished_ok = Signal()
    error = Signal(str)

    def __init__(self, api: TidalAPI, tab: LibraryTab):
        super().__init__()
        self.api = api
        self.tab = tab

    def run(self):
        try:
            favorites: Favorites = self.api.get_favorites()

            if self.tab == "playlists":
                for uuid in favorites.PLAYLIST:
                    pl = self.api.get_playlist(playlist_uuid=uuid)
                    self.item_ready.emit(pl)

            elif self.tab == "albums":
                for album_id in favorites.ALBUM:
                    try:
                        al = self.api.get_album(album_id=album_id)
                        self.item_ready.emit(al)
                    except Exception:
                        pass

            elif self.tab == "artists":
                for artist_id in favorites.ARTIST:
                    try:
                        ar = self.api.get_artist(artist_id=artist_id)
                        self.item_ready.emit(ar)
                    except Exception:
                        pass

            self.finished_ok.emit()
        except Exception as e:
            self.error.emit(str(e))


class DownloadWorker(QThread):
    """Runs `tiddl download url` for each selected resource URL."""

    log_line = Signal(str)
    progress = Signal(int, int)   # (done, total)
    finished_ok = Signal()
    error = Signal(str)

    def __init__(self, urls: list[str], download_path: str, quality: str):
        super().__init__()
        self.urls = urls
        self.download_path = download_path
        self.quality = quality

    def run(self):
        total = len(self.urls)
        for idx, url in enumerate(self.urls, 1):
            self.log_line.emit(f"\n▶ Downloading {url}")
            cmd = [
                sys.executable, "-m", "tiddl",
                "download",
                "-q", self.quality,
                "-p", self.download_path,
                "url", url,
            ]
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                for line in proc.stdout:
                    line = line.rstrip()
                    if line:
                        self.log_line.emit(line)
                proc.wait()
            except Exception as e:
                self.error.emit(str(e))
                return
            self.progress.emit(idx, total)

        self.finished_ok.emit()
