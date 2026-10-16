"""Third-party music services (Spotify, Apple Music).

Neither service allows downloading audio — their streams are DRM-protected.
These integrations only *read* the user's library (playlists, saved albums,
liked songs), match every track against the Tidal catalog and hand the
matches to the regular tiddl download pipeline.

Collections are addressed by a pseudo-URL so they can travel through the
existing ``CardVM.url`` / ``DownloadTask.url`` plumbing unchanged:

    spotify:playlist:<id>      spotify:album:<id>
    spotify:track:<id>         spotify:liked
    applemusic:library-playlist:<id>
    applemusic:library-album:<id>
    applemusic:library-songs
    applemusic:catalog-playlist:<storefront>:<id>
    applemusic:catalog-album:<storefront>:<id>
    applemusic:catalog-song:<storefront>:<id>

Public share links (open.spotify.com / music.apple.com) are converted to
these pseudo-URLs by :func:`app.integrations.registry.normalize_url`.
"""
