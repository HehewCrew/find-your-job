"""The local web server: routes, JSON, static files, and the two checks that keep other
web pages out.

A server on 127.0.0.1 is reachable from any page the browser has open, so:

  * every POST must carry the token that was written into index.html when the server
    started - another site cannot read that page, so it cannot know the token;
  * every request must name this server in its Host header - that stops DNS rebinding,
    where a hostile domain is re-pointed at 127.0.0.1 to get around the browser's
    same-origin rule.

What each endpoint does lives in api.py; this module only speaks HTTP.
"""

from __future__ import annotations

import json
import secrets
import sys
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from jobs import brief, settings
from jobs.errors import JobsError, Skipped
from jobs.ui import api
from jobs.ui.opener import open_path
from jobs.ui.paths import Paths
from jobs.ui.tasks import Busy, TaskRunner
from jobtrack.models import ValidationError

STATIC = Path(__file__).resolve().parent / "static"
TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
    ".svg": "image/svg+xml",
}


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR lets a second server bind a port already in use, so a second
    # copy of the UI would silently share it instead of moving to the next free one.
    allow_reuse_address = sys.platform != "win32"


@dataclass
class App:
    paths: Paths
    token: str
    runner: TaskRunner
    engine: str
    opener: Callable[[Path, bool], None]


class NotFound(Exception):
    pass


class _Handler(BaseHTTPRequestHandler):
    app: App  # set per server by make_server
    server_version = "FindYourJob"
    protocol_version = "HTTP/1.0"

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib signature
        pass  # the terminal is for the URL and real errors, not a line per request

    # -- plumbing ---------------------------------------------------------------------

    def _host_ok(self) -> bool:
        port = self.server.server_address[1]
        return self.headers.get("Host", "") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload) -> None:
        self._send(status, json.dumps(payload).encode(), "application/json; charset=utf-8")

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw or b"{}")
        except json.JSONDecodeError as exc:
            raise ValidationError(f"The request was not valid JSON: {exc.msg}.") from None
        if not isinstance(data, dict):
            raise ValidationError("The request body must be a JSON object.")
        return data

    def _dispatch(self, fn: Callable[[], object]) -> None:
        try:
            self._json(200, fn())
        except NotFound:
            self._json(404, {"error": "Not found."})
        except (ValidationError, settings.SettingsError) as exc:
            self._json(400, {"error": str(exc)})
        except brief.StillPending as exc:
            self._json(409, {"error": str(exc), "pending": [b.label for b in exc.briefs]})
        except Skipped as exc:
            self._json(409, {"error": str(exc), "verdict": api._verdict(exc.assessment.verdict)})
        except (JobsError, Busy) as exc:
            self._json(409, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001 - reported to the page, logged here
            traceback.print_exc()
            self._json(500, {"error": f"Something went wrong: {exc}"})

    # -- GET --------------------------------------------------------------------------

    def do_GET(self) -> None:
        if not self._host_ok():
            self._json(403, {"error": "This server only answers to 127.0.0.1."})
            return
        url = urlsplit(self.path)
        a = self.app
        if url.path == "/":
            html = (STATIC / "index.html").read_text(encoding="utf-8")
            self._send(200, html.replace("{{TOKEN}}", a.token).encode(), TYPES[".html"])
        elif url.path.startswith("/static/"):
            self._static(url.path)
        elif url.path.startswith("/api/setup/"):
            name = url.path.removeprefix("/api/setup/")
            self._dispatch(lambda: api.setup_load(a.paths, name))
        elif url.path == "/api/events":
            since = int(parse_qs(url.query).get("since", ["0"])[0] or 0)
            self._events(since)
        else:
            routes = {
                "/api/state": lambda: api.state(a.paths, a.runner, a.engine),
                "/api/leads": lambda: api.leads(a.paths),
                "/api/cvs": lambda: api.cvs(a.paths),
                "/api/briefs": lambda: api.briefs(a.paths),
                "/api/setup": lambda: api.setup_status(a.paths),
            }
            self._dispatch(routes.get(url.path, _not_found))

    def _static(self, path: str) -> None:
        target = (STATIC / unquote(path.removeprefix("/static/"))).resolve()
        if not target.is_relative_to(STATIC) or not target.is_file():
            self._json(404, {"error": "Not found."})
            return
        ctype = TYPES.get(target.suffix, "application/octet-stream")
        self._send(200, target.read_bytes(), ctype)

    def _events(self, since: int) -> None:
        """Server-Sent Events for the current task, closed once it has finished."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            while True:
                events, finished = self.app.runner.events(since, timeout=15)
                for event in events:
                    self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
                since += len(events)
                if not events and not finished:
                    self.wfile.write(b": still working\n\n")
                self.wfile.flush()
                if finished and not events:
                    return
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return  # the page went away; the task carries on

    # -- POST -------------------------------------------------------------------------

    def do_POST(self) -> None:
        if not self._host_ok():
            self._json(403, {"error": "This server only answers to 127.0.0.1."})
            return
        if not secrets.compare_digest(self.headers.get("X-FYJ-Token", ""), self.app.token):
            self._json(403, {"error": "Missing or wrong page token - reload the page."})
            return
        a = self.app
        path = urlsplit(self.path).path

        def route():
            b = self._body()
            if path == "/api/leads/mark":
                return api.mark_lead(a.paths, str(b.get("url", "")), str(b.get("mark", "")))
            if path == "/api/scrape":
                api.start_scrape(a.paths, a.runner)
                return {"task": "scrape"}
            if path == "/api/pick":
                api.start_pick(a.paths, a.runner)
                return {"task": "build"}
            if path == "/api/paste/assess":
                return api.assess(
                    a.paths,
                    **{
                        k: str(b.get(k, ""))
                        for k in ("text", "company", "title", "url", "location", "variant")
                    },
                )
            if path == "/api/paste/build":
                api.start_paste(a.paths, a.runner, str(b.get("id", "")), bool(b.get("force")))
                return {"task": "paste"}
            if path == "/api/briefs/mark":
                selectors = b.get("selectors") or []
                if not isinstance(selectors, list):
                    raise ValidationError("selectors must be a list")
                return api.mark_briefs(
                    a.paths, selectors, str(b.get("state", "")), str(b.get("reason", ""))
                )
            if path == "/api/close":
                return api.close(a.paths, bool(b.get("dry_run")), bool(b.get("force")))
            if path == "/api/setup/profile/start":
                return api.setup_start(a.paths, b.get("answers"))
            if path == "/api/setup/preview":
                api.start_preview(a.paths, a.runner, str(b.get("variant", "")))
                return {"task": "preview"}
            if path.startswith("/api/setup/"):
                return api.setup_save(a.paths, path.removeprefix("/api/setup/"), b)
            if path == "/api/open":
                api.open_file(
                    a.paths, str(b.get("path", "")), bool(b.get("reveal")), opener=a.opener
                )
                return {}
            raise NotFound

        self._dispatch(route)


def _not_found():
    raise NotFound


def make_server(
    paths: Paths,
    port: int,
    *,
    token: str | None = None,
    runner: TaskRunner | None = None,
    engine: str | None = None,
    opener: Callable[[Path, bool], None] | None = None,
) -> ThreadingHTTPServer:
    """A server bound to 127.0.0.1:`port` (0 = any free port). Raises OSError if taken."""
    app = App(
        paths=paths,
        token=token or secrets.token_urlsafe(32),
        runner=runner or TaskRunner(),
        engine=engine or api.pdf_engine(),
        opener=opener or open_path,
    )
    handler = type("Handler", (_Handler,), {"app": app})
    srv = _Server(("127.0.0.1", port), handler)
    srv.app = app
    return srv
