"""Apple Music API client — MusicKit JS sign-in, library reads only.

Apple Music requires two tokens:

* a **developer token** — a JWT signed with a MusicKit private key
  (``.p8``) from an Apple Developer account. tiddl can sign it locally
  from Team ID + Key ID + .p8 file (needs ``pyjwt[crypto]``) or accept a
  pre-generated token pasted in the settings dialog;
* a **Music User Token** — obtained by serving a small page on
  ``127.0.0.1`` that loads MusicKit JS and calls ``authorize()``; the page
  POSTs the token back to tiddl.
"""
import json
import logging
import os
import re
import threading
import time
import webbrowser
from pathlib import Path
from typing import Iterator, Optional

import requests

from app.integrations import store
from app.integrations.loopback import LoopbackServer, json_body
from app.integrations.models import (
    CollectionContents,
    ExternalCollection,
    ExternalTrack,
)

log = logging.getLogger(__name__)

PROVIDER = "apple_music"
API_URL = "https://api.music.apple.com"
LOGIN_PORT = 8889
LIBRARY_SONGS_URL = "applemusic:library-songs"
# Apple caps developer tokens at 6 months.
_DEV_TOKEN_TTL = 180 * 24 * 3600

_SHARE_RE = re.compile(
    r"music\.apple\.com/([a-z]{2})/(playlist|album|song)/(?:[^/?#]+/)?([^/?#]+)"
    r"(?:\?[^#]*?\bi=(\d+))?"
)
_PSEUDO_RE = re.compile(r"^applemusic:([a-z-]+)(?::(.+))?$")

_LOGIN_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>tiddl — Apple Music</title>
<script src="https://js-cdn.music.apple.com/musickit/v3/musickit.js" data-web-components async></script>
<style>body{font-family:-apple-system,system-ui,sans-serif;background:#1a1a1a;color:#ddd;
display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
div{text-align:center;max-width:420px}h1{color:#0ff;font-size:22px}
button{background:#fa2d48;color:#fff;border:0;border-radius:8px;padding:12px 22px;
font-size:15px;cursor:pointer}#msg{margin-top:16px;color:#999}</style></head>
<body><div><h1>Connect Apple Music</h1>
<p>Sign in with your Apple ID so tiddl can read your library.</p>
<button id="go" disabled>Sign in with Apple Music</button><p id="msg">Loading MusicKit…</p></div>
<script>
const msg = document.getElementById('msg'), go = document.getElementById('go');
async function send(payload) {
  await fetch('/token', {method: 'POST', headers: {'Content-Type': 'application/json'},
                         body: JSON.stringify(payload)});
}
document.addEventListener('musickitloaded', async () => {
  try {
    await MusicKit.configure({developerToken: __DEV_TOKEN__, app: {name: 'tiddl', build: '1.0'}});
    go.disabled = false; msg.textContent = '';
  } catch (e) { msg.textContent = 'MusicKit error: ' + e; send({error: String(e)}); }
});
go.onclick = async () => {
  try {
    const token = await MusicKit.getInstance().authorize();
    await send({token});
    document.body.innerHTML = '<div><h1>Apple Music connected</h1><p>You can close this tab and return to tiddl.</p></div>';
  } catch (e) { msg.textContent = 'Sign-in failed: ' + e; send({error: String(e)}); }
};
</script></body></html>"""


class AppleMusicError(RuntimeError):
    pass


def normalize_url(url: str) -> Optional[str]:
    """Map a music.apple.com link to our pseudo-URL, or ``None``."""
    url = url.strip()
    if _PSEUDO_RE.match(url):
        return url
    m = _SHARE_RE.search(url)
    if not m:
        return None
    storefront, kind, ident, song_param = m.groups()
    if kind == "album" and song_param:
        return f"applemusic:catalog-song:{storefront}:{song_param}"
    return f"applemusic:catalog-{kind}:{storefront}:{ident}"


def _artwork(attrs: dict, size: int = 300) -> Optional[str]:
    art = attrs.get("artwork") or {}
    url = art.get("url")
    if not url:
        return None
    return url.replace("{w}", str(size)).replace("{h}", str(size)).replace("{f}", "jpg")


def _to_track(res: dict) -> Optional[ExternalTrack]:
    """Build a track from a catalog song or a library song (+catalog include)."""
    if not res or res.get("type") not in ("songs", "library-songs"):
        return None
    attrs = res.get("attributes") or {}
    isrc = attrs.get("isrc", "")
    if not isrc:
        catalog = ((res.get("relationships") or {}).get("catalog") or {}).get("data") or []
        if catalog:
            isrc = (catalog[0].get("attributes") or {}).get("isrc", "")
    return ExternalTrack(
        title=attrs.get("name") or "",
        artists=[attrs.get("artistName") or ""],
        album=attrs.get("albumName") or "",
        isrc=isrc or "",
        duration_ms=int(attrs.get("durationInMillis") or 0),
        cover_url=_artwork(attrs, 600),
        track_number=int(attrs.get("trackNumber") or 0),
    )


def sign_developer_token(team_id: str, key_id: str, p8_path: str) -> str:
    """Create an ES256 developer token from a MusicKit private key."""
    try:
        import jwt  # pyjwt[crypto]
    except ImportError as exc:
        raise AppleMusicError(
            "Signing needs PyJWT: run `pip install \"pyjwt[crypto]\"`, or paste "
            "a pre-generated developer token instead."
        ) from exc
    key = Path(p8_path).expanduser().read_text(encoding="utf-8")
    now = int(time.time())
    return jwt.encode(
        {"iss": team_id, "iat": now, "exp": now + _DEV_TOKEN_TTL},
        key,
        algorithm="ES256",
        headers={"kid": key_id},
    )


class AppleMusicClient:
    def __init__(self) -> None:
        self._cfg = store.load(PROVIDER)
        self._session = requests.Session()
        self._lock = threading.Lock()

    # ── Settings / state ─────────────────────────────────────────────────────

    def _bundled_token(self) -> str:
        # TIDDL_APPLE_DEVELOPER_TOKEN lets a packaged build ship a developer
        # token so end users only sign in with their Apple ID.
        return os.environ.get("TIDDL_APPLE_DEVELOPER_TOKEN", "")

    def is_configured(self) -> bool:
        c = self._cfg
        return bool(
            self._bundled_token()
            or c.get("developer_token")
            or (c.get("team_id") and c.get("key_id") and c.get("p8_path"))
        )

    def is_connected(self) -> bool:
        return bool(self._cfg.get("music_user_token")) and self.is_configured()

    @property
    def account_name(self) -> str:
        sf = self._cfg.get("storefront", "")
        return f"Apple Music ({sf.upper()})" if sf else "Apple Music"

    @staticmethod
    def save_settings(
        developer_token: str = "",
        team_id: str = "",
        key_id: str = "",
        p8_path: str = "",
    ) -> None:
        store.save(PROVIDER, {
            "developer_token": developer_token.strip(),
            "team_id": team_id.strip(),
            "key_id": key_id.strip(),
            "p8_path": p8_path.strip(),
            # Settings changed — any cached signed token is stale.
            "signed_token": "",
            "signed_token_exp": 0,
        })

    @staticmethod
    def disconnect() -> None:
        store.clear_keys(PROVIDER, ["music_user_token", "storefront"])

    def developer_token(self) -> str:
        with self._lock:
            if self._cfg.get("developer_token"):
                return self._cfg["developer_token"]
            if not self._cfg.get("team_id") and self._bundled_token():
                return self._bundled_token()
            if self._cfg.get("signed_token") and \
                    time.time() < self._cfg.get("signed_token_exp", 0) - 86400:
                return self._cfg["signed_token"]
            if not self.is_configured():
                raise AppleMusicError("Configure Apple Music developer credentials first.")
            token = sign_developer_token(
                self._cfg["team_id"], self._cfg["key_id"], self._cfg["p8_path"]
            )
            update = {
                "signed_token": token,
                "signed_token_exp": int(time.time()) + _DEV_TOKEN_TTL,
            }
            store.save(PROVIDER, update)
            self._cfg.update(update)
            return token

    # ── Sign-in ──────────────────────────────────────────────────────────────

    def login(self, cancel: Optional[threading.Event] = None) -> str:
        dev_token = self.developer_token()
        page = _LOGIN_PAGE.replace("__DEV_TOKEN__", json.dumps(dev_token))

        def index(_m: str, _q: dict, _b: bytes) -> tuple:
            return 200, "text/html; charset=utf-8", page, None

        def token(method: str, _q: dict, body: bytes) -> tuple:
            if method != "POST":
                return 405, "text/plain", "POST only", None
            return 200, "application/json", "{}", json_body(body)

        server = LoopbackServer(LOGIN_PORT, {"/": index, "/token": token})
        webbrowser.open(f"http://127.0.0.1:{server.port}/")
        result = server.wait(cancel=cancel)
        assert isinstance(result, dict)
        if result.get("error") or not result.get("token"):
            raise AppleMusicError(f"Apple Music sign-in failed: {result.get('error', 'no token')}")

        store.save(PROVIDER, {"music_user_token": result["token"]})
        self._cfg["music_user_token"] = result["token"]

        sf = self._get("/v1/me/storefront")
        storefront = ((sf.get("data") or [{}])[0]).get("id", "us")
        store.save(PROVIDER, {"storefront": storefront})
        self._cfg["storefront"] = storefront
        return self.account_name

    # ── HTTP ─────────────────────────────────────────────────────────────────

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        headers = {"Authorization": f"Bearer {self.developer_token()}"}
        if path.startswith("/v1/me"):
            mut = self._cfg.get("music_user_token")
            if not mut:
                raise AppleMusicError("Not connected to Apple Music.")
            headers["Music-User-Token"] = mut
        for _attempt in range(5):
            resp = self._session.get(
                f"{API_URL}{path}", params=params, headers=headers, timeout=30
            )
            if resp.status_code == 429:
                time.sleep(min(int(resp.headers.get("Retry-After", "2")), 30))
                continue
            if resp.status_code in (401, 403):
                raise AppleMusicError(
                    "Apple Music rejected the credentials — check the developer "
                    "token or reconnect from the Apple Music tab."
                )
            if resp.status_code == 404:
                return {"data": []}
            if resp.status_code != 200:
                raise AppleMusicError(
                    f"Apple Music API {resp.status_code} on {path}: {resp.text[:200]}"
                )
            return resp.json()
        raise AppleMusicError(f"Apple Music API kept failing on {path}")

    def _paged(self, path: str, params: Optional[dict] = None) -> Iterator[dict]:
        data = self._get(path, params)
        while True:
            yield from data.get("data") or []
            nxt = data.get("next")
            if not nxt:
                return
            # "next" is a relative path that already carries offset; keep
            # include/limit params which Apple does not echo back.
            extra = {k: v for k, v in (params or {}).items() if k != "offset"}
            data = self._get(nxt, extra)

    def _tracks(self, path: str) -> list[ExternalTrack]:
        return [
            t for t in (
                _to_track(r) for r in self._paged(path, {"limit": 100, "include": "catalog"})
            ) if t
        ]

    # ── Library ──────────────────────────────────────────────────────────────

    def iter_library(self) -> Iterator[ExternalCollection]:
        """Library Songs, then library playlists, then library albums."""
        yield ExternalCollection(
            provider=PROVIDER, kind="playlist", url=LIBRARY_SONGS_URL,
            title="Library Songs", creator="Apple Music", source="liked",
        )
        for pl in self._paged("/v1/me/library/playlists", {"limit": 100}):
            attrs = pl.get("attributes") or {}
            yield ExternalCollection(
                provider=PROVIDER, kind="playlist",
                url=f"applemusic:library-playlist:{pl['id']}",
                title=attrs.get("name") or "Untitled",
                creator="Apple Music",
                cover_url=_artwork(attrs),
                source="owned" if attrs.get("canEdit") else "liked",
            )
        for al in self._paged("/v1/me/library/albums", {"limit": 100}):
            attrs = al.get("attributes") or {}
            yield ExternalCollection(
                provider=PROVIDER, kind="album",
                url=f"applemusic:library-album:{al['id']}",
                title=attrs.get("name") or "",
                creator=attrs.get("artistName") or "",
                cover_url=_artwork(attrs),
                track_count=int(attrs.get("trackCount") or 0),
            )

    def get_contents(self, url: str) -> CollectionContents:
        m = _PSEUDO_RE.match(url)
        if not m:
            raise AppleMusicError(f"Not an Apple Music URL: {url}")
        kind, rest = m.group(1), m.group(2) or ""

        if kind == "library-songs":
            tracks = self._tracks("/v1/me/library/songs")
            return CollectionContents(ExternalCollection(
                provider=PROVIDER, kind="playlist", url=url,
                title="Library Songs", creator="Apple Music",
                track_count=len(tracks),
            ), tracks)

        if kind in ("library-playlist", "library-album"):
            res_type = "playlists" if kind == "library-playlist" else "albums"
            meta = (self._get(f"/v1/me/library/{res_type}/{rest}").get("data") or [{}])[0]
            attrs = meta.get("attributes") or {}
            tracks = self._tracks(f"/v1/me/library/{res_type}/{rest}/tracks")
            return CollectionContents(ExternalCollection(
                provider=PROVIDER,
                kind="playlist" if res_type == "playlists" else "album",
                url=url,
                title=attrs.get("name") or "Untitled",
                creator=attrs.get("artistName") or "Apple Music",
                cover_url=_artwork(attrs),
                track_count=len(tracks),
            ), tracks)

        storefront, _, cid = rest.partition(":")
        if kind == "catalog-song":
            song = (self._get(f"/v1/catalog/{storefront}/songs/{cid}").get("data") or [None])[0]
            track = _to_track(song) if song else None
            if not track:
                raise AppleMusicError("Song not found on Apple Music.")
            return CollectionContents(ExternalCollection(
                provider=PROVIDER, kind="track", url=url, title=track.title,
                creator=", ".join(track.artists),
                cover_url=_artwork(song.get("attributes") or {}),
                track_count=1,
            ), [track])

        if kind in ("catalog-playlist", "catalog-album"):
            res_type = "playlists" if kind == "catalog-playlist" else "albums"
            meta = (self._get(f"/v1/catalog/{storefront}/{res_type}/{cid}").get("data") or [{}])[0]
            attrs = meta.get("attributes") or {}
            tracks = [
                t for t in (
                    _to_track(r) for r in self._paged(
                        f"/v1/catalog/{storefront}/{res_type}/{cid}/tracks", {"limit": 100}
                    )
                ) if t
            ]
            return CollectionContents(ExternalCollection(
                provider=PROVIDER,
                kind="playlist" if res_type == "playlists" else "album",
                url=url,
                title=attrs.get("name") or "Untitled",
                creator=attrs.get("curatorName") or attrs.get("artistName") or "",
                cover_url=_artwork(attrs),
                track_count=len(tracks),
            ), tracks)

        raise AppleMusicError(f"Unsupported Apple Music URL: {url}")
