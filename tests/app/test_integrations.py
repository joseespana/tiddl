from types import SimpleNamespace

import pytest

from app.integrations import apple_music, spotify
from app.integrations.matcher import match_track
from app.integrations.models import ExternalTrack
from app.integrations.registry import normalize_url, provider_of


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=abc",
         "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M"),
        ("https://open.spotify.com/intl-es/album/4aawyAB9vmqN3uQ7FjRGTy",
         "spotify:album:4aawyAB9vmqN3uQ7FjRGTy"),
        ("spotify:track:11dFghVXANMlKmJXsNCbNl", "spotify:track:11dFghVXANMlKmJXsNCbNl"),
        ("https://tidal.com/browse/album/1", None),
    ],
)
def test_spotify_normalize_url(url, expected):
    assert spotify.normalize_url(url) == expected


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://music.apple.com/us/playlist/todays-hits/pl.f4d106fed2bd41149aaacabb233eb5eb",
         "applemusic:catalog-playlist:us:pl.f4d106fed2bd41149aaacabb233eb5eb"),
        ("https://music.apple.com/es/album/abbey-road/1441164426",
         "applemusic:catalog-album:es:1441164426"),
        ("https://music.apple.com/es/album/abbey-road/1441164426?i=1441164589",
         "applemusic:catalog-song:es:1441164589"),
        ("https://music.apple.com/us/song/yesterday/1441164589",
         "applemusic:catalog-song:us:1441164589"),
        ("https://open.spotify.com/album/x", None),
    ],
)
def test_apple_normalize_url(url, expected):
    assert apple_music.normalize_url(url) == expected


def test_registry():
    assert normalize_url("https://open.spotify.com/track/abc") == "spotify:track:abc"
    assert provider_of("spotify:liked") == "spotify"
    assert provider_of("applemusic:library-songs") == "apple_music"
    assert provider_of("https://tidal.com/browse/track/1") is None


def _track(tid, title, artists, duration=200, isrc="", version=None):
    return SimpleNamespace(
        id=tid, title=title, version=version, duration=duration, isrc=isrc,
        allowStreaming=True,
        artists=[SimpleNamespace(name=a) for a in artists],
    )


class _FakeApi:
    def __init__(self, results):
        self.results = results
        self.queries = []

    def get_search(self, query):
        self.queries.append(query)
        return SimpleNamespace(tracks=SimpleNamespace(items=self.results))


def test_match_prefers_isrc():
    api = _FakeApi([
        _track(1, "Yesterday", ["The Beatles"], isrc="AAA"),
        _track(2, "Yesterday", ["The Beatles"], isrc="GBAYE0601477"),
    ])
    ext = ExternalTrack("Yesterday", ["The Beatles"], isrc="gbaye0601477")
    assert match_track(api, ext).id == 2


def test_match_fuzzy_title_and_duration():
    api = _FakeApi([
        _track(1, "Yesterday", ["Some Cover Band"], duration=150),
        _track(2, "Yesterday", ["The Beatles"], duration=125, version="Remastered 2009"),
    ])
    ext = ExternalTrack("Yesterday - Remastered 2009", ["The Beatles"], duration_ms=125_000)
    assert match_track(api, ext).id == 2


def test_match_rejects_unrelated():
    api = _FakeApi([_track(1, "Completely Different", ["Nobody"])])
    assert match_track(api, ExternalTrack("Yesterday", ["The Beatles"])) is None


def test_youtube_match_filter():
    from app.downloads.youtube_source import _match_filter

    flt = _match_filter(ExternalTrack("Song", ["A"], duration_ms=200_000))
    assert "duration>=185" in flt and "duration<=215" in flt
    assert "karaoke" in flt and "live" in flt
    # Words the original title contains must not be excluded.
    assert "live" not in _match_filter(ExternalTrack("Song (Live)", ["A"]))
