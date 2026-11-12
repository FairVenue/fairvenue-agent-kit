"""A one-shot browser page that asks the user to allow trading.

The fallback for clients that cannot show the MCP dialog. The page listens on
127.0.0.1 only, on a random port, under a random path token. Its URL goes to the
desktop browser and nowhere else, never to the model, a log or the journal, so
only a click in the browser can answer it. Allow and Deny are POSTs to that same
path from the page's own origin. The first answer is final and ends the listener.
"""

from __future__ import annotations

import asyncio
import html
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs

ANSWERED = {
    "allow": "Trading is allowed for this session. You can close this tab.",
    "deny": "Trading was not allowed. Nothing was signed. You can close this tab.",
}
_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FairVenue TESTNET: allow trading?</title>
<style>
:root {{ color-scheme: light dark; font-family: system-ui, sans-serif; }}
main {{ max-width: 34rem; margin: 12vh auto; padding: 0 1rem; line-height: 1.5; }}
button {{ font: inherit; padding: .5rem 1.5rem; margin-right: .75rem; cursor: pointer; }}
</style></head>
<body><main><h1>Allow trading?</h1>{body}</main></body></html>
"""
_QUESTION = """<p>{message}</p>
<p>The FairVenue MCP server on this computer asks. Your agent cannot see or click this page.</p>
<form method="post">
<button name="decision" value="allow">Allow</button>
<button name="decision" value="deny">Deny</button>
</form>"""
_POLICY = (
    "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'"
)


class ConfirmationPage:
    """Serves one question until it is answered or ``expires_at`` passes."""

    def __init__(self, message: str, *, lifetime: float) -> None:
        #: "allow" or "deny" once the user clicked.
        self.decision: str | None = None
        self.expires_at = time.monotonic() + lifetime
        self._question = _QUESTION.format(message=html.escape(message))
        self._path = "/" + secrets.token_urlsafe(32)
        self._lock = threading.Lock()
        page = self

        class Handler(BaseHTTPRequestHandler):
            timeout = 10  # A silent connection never holds a thread for long.

            def log_message(self, format: str, *args: Any) -> None:
                pass  # The request line carries the token: never log it.

            def do_GET(self) -> None:
                _reply(self, *page._answer(self, post=False))

            def do_POST(self) -> None:
                _reply(self, *page._answer(self, post=True))

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.timeout = 0.2
        self._host = f"127.0.0.1:{self._server.server_port}"
        threading.Thread(target=self._serve, daemon=True).start()

    @property
    def url(self) -> str:
        """The secret page URL. Only for the browser opener."""
        return f"http://{self._host}{self._path}"

    def close(self) -> None:
        """Stop listening within a fraction of a second."""
        self.expires_at = 0.0

    async def wait(self, seconds: float) -> str | None:
        """The decision, or None when nobody clicked within ``seconds``."""
        deadline = time.monotonic() + seconds
        while self.decision is None and time.monotonic() < min(deadline, self.expires_at):
            await asyncio.sleep(0.1)
        return self.decision

    def _serve(self) -> None:
        with self._server:
            while self.decision is None and time.monotonic() < self.expires_at:
                self._server.handle_request()

    def _answer(self, request: BaseHTTPRequestHandler, *, post: bool) -> tuple[int, str]:
        # The unguessable path keeps out everyone who was not shown the page. The Host
        # check stops DNS rebinding and the Origin check a POST from any other page.
        if not secrets.compare_digest(request.path.encode(), self._path.encode()):
            return 404, "<p>Not found.</p>"
        if request.headers.get("Host") != self._host:
            return 403, "<p>Refused.</p>"
        if not post:
            if self.decision is not None:
                return 410, f"<p>{ANSWERED[self.decision]}</p>"
            return 200, self._question
        if request.headers.get("Origin") != f"http://{self._host}":
            return 403, "<p>Refused.</p>"
        length = request.headers.get("Content-Length", "")
        if not length.isdigit() or int(length) > 64:
            return 400, "<p>Bad request.</p>"
        form = parse_qs(request.rfile.read(int(length)).decode("ascii", "replace"))
        choice = form.get("decision", [""])[0]
        if choice not in ANSWERED:
            return 400, "<p>Bad request.</p>"
        with self._lock:
            if self.decision is None and time.monotonic() < self.expires_at:
                self.decision = choice
            answered = self.decision
        if answered is None:
            return 410, "<p>This page expired. Ask your agent again.</p>"
        return (200 if answered == choice else 409), f"<p>{ANSWERED[answered]}</p>"


def _reply(request: BaseHTTPRequestHandler, status: int, body: str) -> None:
    data = _PAGE.format(body=body).encode()
    request.send_response(status)
    request.send_header("Content-Type", "text/html; charset=utf-8")
    request.send_header("Content-Length", str(len(data)))
    request.send_header("Cache-Control", "no-store")
    request.send_header("Content-Security-Policy", _POLICY)
    request.end_headers()
    request.wfile.write(data)
