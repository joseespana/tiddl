"""Dispatch helpers: pseudo-URL → provider client."""
from typing import Optional, Union

from app.integrations import apple_music, spotify
from app.integrations.models import Provider

Client = Union[spotify.SpotifyClient, apple_music.AppleMusicClient]


def normalize_url(url: str) -> Optional[str]:
    """Return the pseudo-URL for a Spotify / Apple Music link, else ``None``."""
    return spotify.normalize_url(url) or apple_music.normalize_url(url)


def provider_of(url: str) -> Optional[Provider]:
    if url.startswith("spotify:"):
        return "spotify"
    if url.startswith("applemusic:"):
        return "apple_music"
    return None


def is_external_url(url: str) -> bool:
    return provider_of(url) is not None


def client_for(provider: str) -> Client:
    if provider == "spotify":
        return spotify.SpotifyClient()
    if provider == "apple_music":
        return apple_music.AppleMusicClient()
    raise ValueError(f"Unknown provider: {provider}")
