from logging import getLogger
from typing import Literal, TypeAlias

from requests_cache import DO_NOT_CACHE, EXPIRE_IMMEDIATELY

from .client import TidalClient
from .models.base import (
    AlbumItems,
    AlbumItemsCredits,
    ArtistAlbumsItems,
    ArtistVideosItems,
    Favorites,
    MixItems,
    PlaylistItems,
    Search,
    SessionResponse,
    TrackLyrics,
    TrackStream,
    UserPlaylistsItems,
    VideoStream,
)
from .models.resources import (
    Album,
    Artist,
    Playlist,
    StreamVideoQuality,
    Track,
    TrackQuality,
    Video,
)
from .models.review import AlbumReview

ID: TypeAlias = str | int

log = getLogger(__name__)

# Genres rarely (if ever) change — cache them for a week.
_GENRE_EXPIRE_AFTER = 604800


def _parse_genre_names(payload: dict) -> list[str]:
    """Extract genre names from a Tidal v2 JSON:API genres response.

    Preserves order and de-duplicates. Returns an empty list on any
    unexpected shape; never raises.
    """

    try:
        included = payload.get("included") or []
        if not isinstance(included, list):
            return []

        names: list[str] = []
        for entry in included:
            if not isinstance(entry, dict):
                continue
            if entry.get("type") != "genres":
                continue
            attrs = entry.get("attributes") or {}
            if not isinstance(attrs, dict):
                continue
            name = attrs.get("name")
            if isinstance(name, str) and name:
                names.append(name)

        # preserve order, drop empties, de-dup
        return list(dict.fromkeys(g for g in names if g))
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("genre parse failed: %s", exc)
        return []


class Limits:
    # TODO test every max limit

    ARTIST_ALBUMS = 10
    ARTIST_ALBUMS_MAX = 100

    ARTIST_VIDEOS = 10
    ARTIST_VIDEOS_MAX = 100

    ALBUM_ITEMS = 20
    ALBUM_ITEMS_MAX = 100

    PLAYLIST_ITEMS = 20
    PLAYLIST_ITEMS_MAX = 100

    MIX_ITEMS = 20
    MIX_ITEMS_MAX = 100


class TidalAPI:
    client: TidalClient
    user_id: str
    country_code: str

    def __init__(self, client: TidalClient, user_id: str, country_code: str) -> None:
        self.client = client
        self.user_id = user_id
        self.country_code = country_code

    def get_album(self, album_id: ID):
        return self.client.fetch(
            Album,
            f"albums/{album_id}",
            {"countryCode": self.country_code},
            expire_after=3600,
        )

    def get_album_items(
        self, album_id: ID, limit: int = Limits.ALBUM_ITEMS, offset: int = 0
    ):
        return self.client.fetch(
            AlbumItems,
            f"albums/{album_id}/items",
            {
                "countryCode": self.country_code,
                "limit": min(limit, Limits.ALBUM_ITEMS_MAX),
                "offset": offset,
            },
            expire_after=3600,
        )

    def get_album_items_credits(
        self, album_id: ID, limit: int = Limits.ALBUM_ITEMS, offset: int = 0
    ):
        return self.client.fetch(
            AlbumItemsCredits,
            f"albums/{album_id}/items/credits",
            {
                "countryCode": self.country_code,
                "limit": min(limit, Limits.ALBUM_ITEMS_MAX),
                "offset": offset,
            },
            expire_after=3600,
        )

    def get_album_review(self, album_id: ID):
        return self.client.fetch(
            AlbumReview,
            f"albums/{album_id}/review",
            {"countryCode": self.country_code},
            expire_after=3600,
        )

    def get_artist(self, artist_id: ID):
        return self.client.fetch(
            Artist,
            f"artists/{artist_id}",
            {"countryCode": self.country_code},
            expire_after=3600,
        )

    def get_artist_videos(
        self,
        artist_id: ID,
        limit: int = Limits.ARTIST_VIDEOS,
        offset: int = 0,
    ):
        return self.client.fetch(
            ArtistVideosItems,
            f"artists/{artist_id}/videos",
            {
                "countryCode": self.country_code,
                "limit": limit,
                "offset": offset,
            },
            expire_after=3600,
        )

    def get_artist_albums(
        self,
        artist_id: ID,
        limit: int = Limits.ARTIST_ALBUMS,
        offset: int = 0,
        filter: Literal["ALBUMS", "EPSANDSINGLES"] = "ALBUMS",
    ):
        return self.client.fetch(
            ArtistAlbumsItems,
            f"artists/{artist_id}/albums",
            {
                "countryCode": self.country_code,
                "limit": min(limit, Limits.ARTIST_ALBUMS_MAX),
                "offset": offset,
                "filter": filter,
            },
            expire_after=3600,
        )

    def get_mix_items(
        self,
        mix_id: str,
        limit: int = Limits.MIX_ITEMS,
        offset: int = 0,
    ):
        return self.client.fetch(
            MixItems,
            f"mixes/{mix_id}/items",
            {
                "countryCode": self.country_code,
                "limit": min(limit, Limits.MIX_ITEMS_MAX),
                "offset": offset,
            },
            expire_after=3600,
        )

    def get_favorites(self):
        return self.client.fetch(
            Favorites,
            f"users/{self.user_id}/favorites/ids",
            {"countryCode": self.country_code},
            expire_after=EXPIRE_IMMEDIATELY,
        )

    def get_user_playlists(self, limit: int = 50, offset: int = 0):
        """Fetch the playlists the user created (paginated)."""
        return self.client.fetch(
            UserPlaylistsItems,
            f"users/{self.user_id}/playlists",
            {
                "countryCode": self.country_code,
                "limit": min(limit, 50),
                "offset": offset,
            },
            expire_after=EXPIRE_IMMEDIATELY,
        )

    def get_playlist(self, playlist_uuid: str):
        return self.client.fetch(
            Playlist,
            f"playlists/{playlist_uuid}",
            {"countryCode": self.country_code},
            expire_after=EXPIRE_IMMEDIATELY,
        )

    def get_playlist_items(
        self, playlist_uuid: str, limit: int = Limits.PLAYLIST_ITEMS, offset: int = 0
    ):
        return self.client.fetch(
            PlaylistItems,
            f"playlists/{playlist_uuid}/items",
            {
                "countryCode": self.country_code,
                "limit": min(limit, Limits.PLAYLIST_ITEMS_MAX),
                "offset": offset,
            },
            expire_after=EXPIRE_IMMEDIATELY,
        )

    def get_search(self, query: str):
        return self.client.fetch(
            Search,
            "search",
            {"countryCode": self.country_code, "query": query},
            expire_after=DO_NOT_CACHE,
        )

    def get_session(self):
        return self.client.fetch(SessionResponse, "sessions", expire_after=DO_NOT_CACHE)

    def get_track_lyrics(self, track_id: ID):
        return self.client.fetch(
            TrackLyrics,
            f"tracks/{track_id}/lyrics",
            {"countryCode": self.country_code},
            expire_after=3600,
        )

    def get_track(self, track_id: ID):
        return self.client.fetch(
            Track,
            f"tracks/{track_id}",
            {"countryCode": self.country_code},
            expire_after=3600,
        )

    def get_track_stream(self, track_id: ID, quality: TrackQuality):
        return self.client.fetch(
            TrackStream,
            f"tracks/{track_id}/playbackinfopostpaywall",
            {
                "audioquality": quality,
                "playbackmode": "STREAM",
                "assetpresentation": "FULL",
            },
            expire_after=DO_NOT_CACHE,
        )

    def get_video(self, video_id: ID):
        return self.client.fetch(
            Video,
            f"videos/{video_id}",
            {"countryCode": self.country_code},
            expire_after=3600,
        )

    def get_album_genres(self, album_id: ID) -> list[str]:
        """Return the genre names for the given album via Tidal's v2 API.

        Uses the JSON:API endpoint:
            GET /v2/albums/{id}/relationships/genres
                ?countryCode={cc}&locale=en_US&include=genres

        Response shape::

            {
              "data": [{"id": "123", "type": "genres"}, ...],
              "included": [
                {"id": "123", "type": "genres", "attributes": {"name": "Pop"}},
                ...
              ]
            }

        Returns an empty list when the call fails, the response is malformed,
        or no genres are linked. Only parsing errors are swallowed here;
        transport / auth errors surface to the caller.
        """

        try:
            data = self.client.fetch_v2(
                f"albums/{album_id}/relationships/genres",
                {
                    "countryCode": self.country_code,
                    "locale": "en_US",
                    "include": "genres",
                },
                expire_after=_GENRE_EXPIRE_AFTER,
            )
        except Exception as exc:
            log.debug("get_album_genres(%s) fetch failed: %s", album_id, exc)
            return []

        return _parse_genre_names(data)

    def get_track_genres(self, track_id: ID) -> list[str]:
        """Return the genre names for the given track via Tidal's v2 API.

        Same JSON:API shape as :meth:`get_album_genres` but hits
        ``/v2/tracks/{id}/relationships/genres``.
        """

        try:
            data = self.client.fetch_v2(
                f"tracks/{track_id}/relationships/genres",
                {
                    "countryCode": self.country_code,
                    "locale": "en_US",
                    "include": "genres",
                },
                expire_after=_GENRE_EXPIRE_AFTER,
            )
        except Exception as exc:
            log.debug("get_track_genres(%s) fetch failed: %s", track_id, exc)
            return []

        return _parse_genre_names(data)

    def get_video_stream(self, video_id: ID, quality: StreamVideoQuality):
        return self.client.fetch(
            VideoStream,
            f"videos/{video_id}/playbackinfopostpaywall",
            {
                "videoquality": quality,
                "playbackmode": "STREAM",
                "assetpresentation": "FULL",
            },
            expire_after=DO_NOT_CACHE,
        )
