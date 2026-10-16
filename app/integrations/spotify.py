"""Spotify Web API client — Authorization Code + PKCE, library reads only.

Setup (one time): create an app at https://developer.spotify.com/dashboard,
add ``http://127.0.0.1:8888/callback`` as a Redirect URI and paste the
app's Client ID in tiddl. PKCE means no client secret is needed.
"""
import base64
import hashlib
import logging
import os
import re
import secrets
import threading
import time
import webbrowser
from typing import Any, Iterator, Optional
from urllib.parse import urlencode

import requests

from app.integrations import store
from app.integrations.loopback import LoopbackServer, done_page
from app.integrations.models import (
    CollectionContents,
    ExternalCollection,
    ExternalTrack,
)

log = logging.getLogger(__name__)

PROVIDER = "spotify"
AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_URL = "https://api.spotify.com/v1"
REDIRECT_PORT = 8888
REDIRECT_URI = f"http://127.0.0.1:{REDIRECT_PORT}/callback"
SCOPES = "playlist-read-private playlist-read-collaborative user-library-read"

LIKED_URL = "spotify:liked"

_SHARE_RE = re.compile(
    r"open\.spotify\.com/(?:intl-[a-z-]+/)?(playlist|album|track)/([A-Za-z0-9]+)"
)
_URI_RE = re.compile(r"^spotify:(playlist|album|track):([A-Za-z0-9]+)$")


class SpotifyError(RuntimeError):
    pass


def normalize_url(url: str) -> Optional[str]:
    """Map a share link / URI to our pseudo-URL, or ``None`` if not Spotify."""
    url = url.strip()
    if url == LIKED_URL:
        return url
    m = _SHARE_RE.search(url) or _URI_RE.match(url)
    if m:
        return f"spotify:{m.group(1)}:{m.group(2)}"
    return None


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _image(images: Any) -> Optional[str]:
    if not images:
        return None
    # Spotify lists the largest first; pick a mid-size one for 178px cards.
    pick = images[1] if len(images) > 1 else images[0]
    return pick.get("url")


def _to_track(obj: dict) -> Optional[ExternalTrack]:
    if not obj or obj.get("type", "track") != "track" or obj.get("is_local"):
        return None
    return ExternalTrack(
        title=obj.get("name") or "",
        artists=[a.get("name", "") for a in obj.get("artists") or []],
        album=(obj.get("album") or {}).get("name", ""),
        isrc=(obj.get("external_ids") or {}).get("isrc", "") or "",
        duration_ms=int(obj.get("duration_ms") or 0),
        cover_url=_image((obj.get("album") or {}).get("images")),
        track_number=int(obj.get("track_number") or 0),
    )


class SpotifyClient:
    """Thread-safe enough for our use: each worker builds its own instance."""

    def __init__(self) -> None:
        self._cfg = store.load(PROVIDER)
        self._session = requests.Session()
        self._refresh_lock = threading.Lock()

    # ── Settings / state ─────────────────────────────────────────────────────

    @property
    def client_id(self) -> str:
        # TIDDL_SPOTIFY_CLIENT_ID lets a packaged build ship its own app id
        # so end users only have to click Connect.
        return self._cfg.get("client_id") or os.environ.get(
            "TIDDL_SPOTIFY_CLIENT_ID", ""
        )

    def is_configured(self) -> bool:
        return bool(self.client_id)

    def is_connected(self) -> bool:
        return bool(self._cfg.get("refresh_token"))

    @property
    def account_name(self) -> str:
        return self._cfg.get("display_name", "")

    @staticmethod
    def set_client_id(client_id: str) -> None:
        store.save(PROVIDER, {"client_id": client_id.strip()})

    @staticmethod
    def disconnect() -> None:
        store.clear_keys(
            PROVIDER,
            ["access_token", "refresh_token", "expires_at", "display_name"],
        )

    # ── OAuth ────────────────────────────────────────────────────────────────

    def login(self, cancel: Optional[threading.Event] = None) -> str:
        """Run the browser PKCE flow; returns the Spotify display name."""
        if not self.client_id:
            raise SpotifyError("Set your Spotify Client ID first.")

        verifier, challenge = _pkce_pair()
        state = secrets.token_urlsafe(16)

        def callback(_method: str, query: dict, _body: bytes) -> tuple:
            if query.get("state") != state:
                return 400, "text/html", done_page(
                    "Sign-in failed", "State mismatch — try again.", ok=False
                ), None
            if "error" in query:
                return 200, "text/html", done_page(
                    "Sign-in cancelled", query["error"], ok=False
                ), {"error": query["error"]}
            return 200, "text/html", done_page(
                "Spotify connected", "You can close this tab and return to tiddl."
            ), {"code": query.get("code", "")}

        server = LoopbackServer(REDIRECT_PORT, {"/callback": callback})
        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": REDIRECT_URI,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
            "state": state,
            "scope": SCOPES,
        }
        webbrowser.open(f"{AUTH_URL}?{urlencode(params)}")
        result = server.wait(cancel=cancel)
        assert isinstance(result, dict)
        if result.get("error"):
            raise SpotifyError(f"Spotify sign-in failed: {result['error']}")

        resp = self._session.post(TOKEN_URL, data={
            "grant_type": "authorization_code",
            "code": result["code"],
            "redirect_uri": REDIRECT_URI,
            "client_id": self.client_id,
            "code_verifier": verifier,
        }, timeout=20)
        if resp.status_code != 200:
            raise SpotifyError(f"Token exchange failed: {resp.text[:200]}")
        self._store_tokens(resp.json())

        me = self._get("/me")
        name = me.get("display_name") or me.get("id") or "Spotify user"
        store.save(PROVIDER, {"display_name": name})
        self._cfg["display_name"] = name
        return name

    def _store_tokens(self, data: dict) -> None:
        update = {
            "access_token": data["access_token"],
            "expires_at": int(time.time()) + int(data.get("expires_in", 3600)),
        }
        # Spotify may or may not rotate the refresh token.
        if data.get("refresh_token"):
            update["refresh_token"] = data["refresh_token"]
        store.save(PROVIDER, update)
        self._cfg.update(update)

    def _access_token(self) -> str:
        with self._refresh_lock:
            if self._cfg.get("access_token") and \
                    time.time() < self._cfg.get("expires_at", 0) - 60:
                return self._cfg["access_token"]
            refresh = self._cfg.get("refresh_token")
            if not refresh:
                raise SpotifyError("Not connected to Spotify.")
            resp = self._session.post(TOKEN_URL, data={
                "grant_type": "refresh_token",
                "refresh_token": refresh,
                "client_id": self.client_id,
            }, timeout=20)
            if resp.status_code != 200:
                raise SpotifyError(
                    "Spotify session expired — reconnect from the Spotify tab."
                )
            self._store_tokens(resp.json())
            return self._cfg["access_token"]

    # ── HTTP ─────────────────────────────────────────────────────────────────

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        url = path if path.startswith("http") else f"{API_URL}{path}"
        for _attempt in range(5):
            resp = self._session.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {self._access_token()}"},
                timeout=30,
            )
            if resp.status_code == 429:
                time.sleep(min(int(resp.headers.get("Retry-After", "2")), 30))
                continue
            if resp.status_code == 401:
                self._cfg["expires_at"] = 0  # force refresh on next attempt
                continue
            if resp.status_code != 200:
                raise SpotifyError(
                    f"Spotify API {resp.status_code} on {path}: {resp.text[:200]}"
                )
            return resp.json()
        raise SpotifyError(f"Spotify API kept failing on {path}")

    def _paged(self, path: str, params: Optional[dict] = None) -> Iterator[dict]:
        data = self._get(path, params)
        while True:
            yield from data.get("items") or []
            nxt = data.get("next")
            if not nxt:
                return
            data = self._get(nxt)

    # ── Library ──────────────────────────────────────────────────────────────

    def iter_library(self) -> Iterator[ExternalCollection]:
        """Liked Songs, then the user's playlists, then saved albums."""
        liked = self._get("/me/tracks", {"limit": 1})
        yield ExternalCollection(
            provider=PROVIDER, kind="playlist", url=LIKED_URL,
            title="Liked Songs", creator=self.account_name,
            track_count=int(liked.get("total") or 0), source="liked",
        )

        me_id = self._get("/me").get("id")
        for pl in self._paged("/me/playlists", {"limit": 50}):
            if not pl:
                continue
            owner = pl.get("owner") or {}
            counts = pl.get("tracks") or pl.get("items") or {}
            yield ExternalCollection(
                provider=PROVIDER, kind="playlist",
                url=f"spotify:playlist:{pl['id']}",
                title=pl.get("name") or "Untitled",
                creator=owner.get("display_name") or owner.get("id") or "",
                cover_url=_image(pl.get("images")),
                track_count=int(counts.get("total") or 0),
                source="owned" if owner.get("id") == me_id else "liked",
            )

        for item in self._paged("/me/albums", {"limit": 50}):
            album = item.get("album") or {}
            if not album:
                continue
            yield ExternalCollection(
                provider=PROVIDER, kind="album",
                url=f"spotify:album:{album['id']}",
                title=album.get("name") or "",
                creator=", ".join(a.get("name", "") for a in album.get("artists") or []),
                cover_url=_image(album.get("images")),
                track_count=int(album.get("total_tracks") or 0),
            )

    def get_contents(self, url: str) -> CollectionContents:
        """Resolve a pseudo-URL to its metadata + full track list."""
        if url == LIKED_URL:
            tracks = [
                t for t in (_to_track(i.get("track")) for i in self._paged(
                    "/me/tracks", {"limit": 50}
                )) if t
            ]
            return CollectionContents(ExternalCollection(
                provider=PROVIDER, kind="playlist", url=url,
                title="Liked Songs", creator=self.account_name,
                track_count=len(tracks),
            ), tracks)

        m = _URI_RE.match(url)
        if not m:
            raise SpotifyError(f"Not a Spotify URL: {url}")
        kind, sid = m.groups()

        if kind == "track":
            t = self._get(f"/tracks/{sid}")
            track = _to_track(t)
            return CollectionContents(ExternalCollection(
                provider=PROVIDER, kind="track", url=url,
                title=t.get("name", ""),
                creator=", ".join(track.artists) if track else "",
                cover_url=_image((t.get("album") or {}).get("images")),
                track_count=1,
            ), [track] if track else [])

        if kind == "album":
            a = self._get(f"/albums/{sid}")
            album_name = a.get("name", "")
            tracks = []
            for t in self._paged(f"/albums/{sid}/tracks", {"limit": 50}):
                tr = _to_track({
                    **t, "album": {"name": album_name, "images": a.get("images")},
                })
                if tr:
                    tracks.append(tr)
            return CollectionContents(ExternalCollection(
                provider=PROVIDER, kind="album", url=url,
                title=album_name,
                creator=", ".join(x.get("name", "") for x in a.get("artists") or []),
                cover_url=_image(a.get("images")),
                track_count=len(tracks),
            ), tracks)

        pl = self._get(f"/playlists/{sid}", {
            "fields": "name,owner(display_name,id),images",
        })
        try:
            items = list(self._paged(f"/playlists/{sid}/items", {"limit": 50}))
        except SpotifyError:
            # Older API surface — /tracks was renamed to /items in 2025.
            items = list(self._paged(f"/playlists/{sid}/tracks", {"limit": 50}))
        tracks = [
            t for t in (_to_track(i.get("item") or i.get("track")) for i in items)
            if t
        ]
        owner = pl.get("owner") or {}
        return CollectionContents(ExternalCollection(
            provider=PROVIDER, kind="playlist", url=url,
            title=pl.get("name") or "Untitled",
            creator=owner.get("display_name") or owner.get("id") or "",
            cover_url=_image(pl.get("images")),
            track_count=len(tracks),
        ), tracks)
