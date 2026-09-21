"""The HTTP layer, against a real server on a free port: routing, the token and Host checks,
error mapping and the event stream. What each endpoint does is tested in test_ui_api.py."""

from __future__ import annotations

import http.client
import json
import threading

import pytest
from jobs.progress import emit
from jobs.ui import server
from jobs.ui.paths import Paths
from jobs.ui.tasks import TaskRunner

TOKEN = "test-token"


@pytest.fixture
def app(tmp_path):
    opened = []
    runner = TaskRunner()
    srv = server.make_server(
        Paths.under(tmp_path),
        0,
        token=TOKEN,
        runner=runner,
        engine="none",
        opener=lambda p, reveal: opened.append((p, reveal)),
    )
    thread = threading.Thread(target=srv.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    yield srv, runner, opened, tmp_path
    srv.shutdown()
    srv.server_close()


def call(srv, method, path, body=None, *, token=TOKEN, host=None):
    port = srv.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.putrequest(method, path, skip_host=True)
    conn.putheader("Host", host or f"127.0.0.1:{port}")
    data = b""
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        conn.putheader("Content-Type", "application/json")
    if token:
        conn.putheader("X-FYJ-Token", token)
    conn.putheader("Content-Length", str(len(data)))
    conn.endheaders(data)
    resp = conn.getresponse()
    raw = resp.read()
    conn.close()
    ctype = resp.getheader("Content-Type", "")
    return resp.status, (json.loads(raw) if "json" in ctype else raw.decode())


def test_the_page_carries_the_token(app):
    srv, *_ = app
    status, body = call(srv, "GET", "/")
    assert status == 200
    assert f'<meta name="fyj-token" content="{TOKEN}">' in body


def test_state_is_json(app):
    srv, *_ = app
    status, body = call(srv, "GET", "/api/state")
    assert status == 200 and body["phase"] == "scrape" and body["pdf_engine"] == "none"


def test_a_post_without_the_token_is_refused(app):
    srv, *_ = app
    status, body = call(srv, "POST", "/api/scrape", {}, token=None)
    assert status == 403 and "token" in body["error"].lower()
    assert call(srv, "POST", "/api/scrape", {}, token="wrong")[0] == 403


def test_a_foreign_host_is_refused(app):
    srv, *_ = app
    assert call(srv, "GET", "/api/state", host="evil.example:80")[0] == 403
    assert call(srv, "GET", "/api/state", host=f"localhost:{srv.server_address[1]}")[0] == 200


def test_bad_input_is_400(app):
    srv, *_ = app
    status, body = call(srv, "POST", "/api/leads/mark", {"url": "https://x/1", "mark": "maybe"})
    assert status == 400 and "maybe" in body["error"]
    assert call(srv, "POST", "/api/leads/mark", b"{not json")[0] == 400


def test_nothing_to_do_is_409(app):
    srv, *_ = app
    status, body = call(srv, "POST", "/api/pick", {})
    assert status == 409 and body["error"]


def test_a_second_task_is_409(app):
    srv, runner, *_ = app
    gate = threading.Event()
    runner.start("scrape", lambda report: gate.wait(5) and {})
    try:
        status, body = call(srv, "POST", "/api/scrape", {})
        assert status == 409 and body["error"] == "A scrape is already running."
    finally:
        gate.set()


def test_events_stream_a_tasks_progress_then_its_end(app):
    srv, runner, *_ = app

    def job(report):
        emit(report, "fetch", "Fetching sources…")
        emit(report, "fetch", "1/2 remotive ✓ 3", done=1, total=2, detail=True)
        return {"leads": 3}

    runner.start("scrape", job)
    status, body = call(srv, "GET", "/api/events?since=0")
    events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
    assert status == 200
    assert [e["type"] for e in events] == ["progress", "progress", "done"]
    assert events[-1]["result"] == {"leads": 3}


def test_open_only_opens_allowed_files(app):
    srv, _, opened, root = app
    cv = root / "cv" / "out" / "tailored" / "acme" / "CV.docx"
    cv.parent.mkdir(parents=True)
    cv.write_bytes(b"PK")
    assert (
        call(srv, "POST", "/api/open", {"path": "cv/out/tailored/acme/CV.docx", "reveal": True})[0]
        == 200
    )
    assert opened == [(cv.resolve(), True)]
    assert call(srv, "POST", "/api/open", {"path": "../outside.txt", "reveal": False})[0] == 400


def test_static_files_cannot_escape_the_static_folder(app):
    srv, *_ = app
    assert call(srv, "GET", "/static/../server.py")[0] == 404
    assert call(srv, "GET", "/static/%2e%2e/server.py")[0] == 404


def test_unknown_routes_are_404(app):
    srv, *_ = app
    assert call(srv, "GET", "/api/nope")[0] == 404
    assert call(srv, "POST", "/api/nope", {})[0] == 404


def test_a_busy_port_is_not_shared(app):
    srv, *_ = app
    with pytest.raises(OSError):
        server.make_server(Paths.under(app[3]), srv.server_address[1], engine="none")
