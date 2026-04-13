"""
DiskCache — scans the download directory once and provides O(1) lookups.

Avoids per-item filesystem calls during list rendering.
"""
import re
from pathlib import Path

from app.worker_index import load_index


def _sanitize(s: str) -> str:
    """Remove filesystem-unsafe characters from a string.

    Args:
        s: Input string.

    Returns:
        String with characters ``\\ / : " * ? < > |`` stripped.
    """
    return re.sub(r'[\\/:"*?<>|]+', "", s)


def _norm(s: str) -> str:
    """Lowercase + strip for case-insensitive comparison.

    Args:
        s: Input string.

    Returns:
        Sanitized, lowercased and stripped string.
    """
    return _sanitize(s).lower().strip()


class DiskCache:
    """Scans the download directory once and provides O(1) lookups.

    Attributes:
        m3u_stems: Normalised playlist title stems found as .m3u files.
        albums: Set of (artist_lower, album_lower) tuples from folder structure.
        artists: Set of artist folder names (lowercased).
        pl_uuids: Playlist UUIDs from the local index file.
        album_ids: Album IDs (as strings) from the local index file.
        artist_ids: Artist IDs (as strings) from the local index file.
    """

    _SKIP_DIRS = {
        "m3u", "mixes", "playlists",
        ".spotlight-v100", ".fseventsd", ".trashes", ".temporaryitems",
    }

    def __init__(self, path: str) -> None:
        """Initialise and populate the cache by scanning *path*.

        Args:
            path: Absolute path to the download directory.
        """
        self.m3u_stems: set[str] = set()
        self.albums: set[tuple[str, str]] = set()
        self.artists: set[str] = set()
        self.pl_uuids: set[str] = set()
        self.album_ids: set[str] = set()
        self.artist_ids: set[str] = set()

        base = Path(path)
        if not base.exists():
            return

        # UUID/ID-based index (reliable even after title changes)
        idx = load_index(path)
        self.pl_uuids = set(idx.get("playlist", []))
        self.album_ids = set(str(i) for i in idx.get("album", []))
        self.artist_ids = set(str(i) for i in idx.get("artist", []))

        # M3U stems (for playlists without UUID in index)
        m3u_dir = base / "m3u"
        if m3u_dir.exists():
            self.m3u_stems = {_norm(f.stem) for f in m3u_dir.glob("*.m3u")}

        # Artist / album folder structure
        try:
            for artist_dir in base.iterdir():
                if not artist_dir.is_dir():
                    continue
                if artist_dir.name.lower() in self._SKIP_DIRS:
                    continue
                aname = _norm(artist_dir.name)
                found_album = False
                for album_dir in artist_dir.iterdir():
                    if album_dir.is_dir():
                        has_audio = (
                            any(album_dir.glob("*.flac"))
                            or any(album_dir.glob("*.m4a"))
                        )
                        if has_audio:
                            self.albums.add((aname, _norm(album_dir.name)))
                            found_album = True
                if found_album:
                    self.artists.add(aname)
        except Exception:
            pass

    def has_playlist(self, title: str, uuid: str = "") -> bool:
        """Return True if the playlist is recorded as downloaded.

        Args:
            title: Playlist title (used as fallback stem match).
            uuid: Tidal playlist UUID for index-based lookup.

        Returns:
            True when the playlist is present in the cache.
        """
        if uuid and uuid in self.pl_uuids:
            return True
        return _norm(title) in self.m3u_stems

    def has_album(self, artist: str, album: str, album_id: str = "") -> bool:
        """Return True if the album is recorded as downloaded.

        Args:
            artist: Artist name (used as fallback folder match).
            album: Album title (used as fallback folder match).
            album_id: Tidal album ID for index-based lookup.

        Returns:
            True when the album is present in the cache.
        """
        if album_id and str(album_id) in self.album_ids:
            return True
        return (_norm(artist), _norm(album)) in self.albums

    def has_artist(self, name: str, artist_id: str = "") -> bool:
        """Return True if the artist is recorded as downloaded.

        Args:
            name: Artist name (used as fallback folder match).
            artist_id: Tidal artist ID for index-based lookup.

        Returns:
            True when the artist is present in the cache.
        """
        if artist_id and str(artist_id) in self.artist_ids:
            return True
        return _norm(name) in self.artists
