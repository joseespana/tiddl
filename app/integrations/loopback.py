"""Tiny one-shot HTTP server on 127.0.0.1 used by the OAuth flows.

Spotify redirects the browser to ``http://127.0.0.1:<port>/callback`` with
an authorization code; Apple Music's MusicKit JS runs in a page we serve
ourselves and POSTs the Music User Token back. Both only need to receive a
single request, so this stays deliberately small (stdlib only).
"""
import html
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable, Optional
from urllib.parse import parse_qs, urlparse

log = logging.getLogger(__name__)

DONE_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>tiddl</title>
<style>body{{font-family:-apple-system,system-ui,sans-serif;background:#1a1a1a;color:#ddd;
display:flex;align-items:center;justify-content:center;height:100vh;margin:0}}
div{{text-align:center}}h1{{color:{color};font-size:22px}}</style></head>
<body><div><h1>{title}</h1><p>{message}</p></div></body></html>"""


def done_page(title: str, message: str, ok: bool = True) -> str:
    return DONE_PAGE.format(
        title=html.escape(title),
        message=html.escape(message),
        color="#0ff" if ok else "#f66",
    )


class AuthCancelled(Exception):
    pass


class LoopbackServer:
    """Serve a few routes until a handler reports a result.

    Each route handler gets ``(method, query: dict[str, str], body: bytes)``
    and returns ``(status, content_type, body_text, result_or_None)``.
    The first non-``None`` result ends :meth:`wait`.
    """

    def __init__(
        self,
        port: int,
        routes: dict[str, Callable[[str, dict, bytes], tuple]],
    ) -> None:
        self._routes = routes
        self._result: Optional[object] = None
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            def _dispatch(self, method: str) -> None:
                parsed = urlparse(self.path)
                route = outer._routes.get(parsed.path)
                if route is None:
                    self.send_error(404)
                    return
                query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                status, ctype, text, result = route(method, query, body)
                payload = text.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(payload)
                if result is not None:
                    outer._result = result

            def do_GET(self) -> None:  # noqa: N802
                self._dispatch("GET")

            def do_POST(self) -> None:  # noqa: N802
                self._dispatch("POST")

            def log_message(self, fmt: str, *args) -> None:
                log.debug("loopback: " + fmt, *args)

        # Bind to the loopback interface only — never expose on the LAN.
        self._httpd = HTTPServer(("127.0.0.1", port), _Handler)
        self._httpd.timeout = 0.5

    @property
    def port(self) -> int:
        return self._httpd.server_address[1]

    def wait(
        self,
        timeout: float = 300,
        cancel: Optional[threading.Event] = None,
    ) -> object:
        """Block until a route produced a result; raise on timeout/cancel."""
        deadline = time.monotonic() + timeout
        try:
            while self._result is None:
                if cancel is not None and cancel.is_set():
                    raise AuthCancelled("Sign-in cancelled")
                if time.monotonic() > deadline:
                    raise TimeoutError("Timed out waiting for the browser sign-in")
                self._httpd.handle_request()
            return self._result
        finally:
            self._httpd.server_close()


def json_body(body: bytes) -> dict:
    try:
        return json.loads(body.decode("utf-8") or "{}")
    except Exception:
        return {}
