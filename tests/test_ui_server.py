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
    return resp.status, (json.loads(raw) if "json" in ctype else raw.decode(errors="replace"))


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


def test_every_asset_the_page_references_is_served(app):
    import re

    srv, *_ = app
    _, page = call(srv, "GET", "/")
    _, css = call(srv, "GET", "/static/app.css")
    refs = set(re.findall(r'(?:href|src)="(/static/[^"]+)"', page))
    refs |= set(re.findall(r'url\("(/static/[^"]+)"\)', css))
    assert {"/static/app.css", "/static/app.js"} <= refs
    assert any(r.endswith(".woff2") for r in refs)
    for ref in refs:
        assert call(srv, "GET", ref)[0] == 200, ref


# --- setup routes ------------------------------------------------------------------


def copy_examples(root):
    import shutil
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    for rel in (
        "jobs/settings.example.json",
        "cv/profile.example.json",
        "jobs/priorities.example.md",
        "cv/rules.example.md",
    ):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(repo / rel, root / rel)


def test_state_lists_the_setup_files_still_missing(app):
    srv, *_ = app
    _, body = call(srv, "GET", "/api/state")
    assert body["setup_missing"] == ["profile", "settings", "priorities", "rules"]


def test_setup_status_and_load(app):
    srv, _, _, root = app
    copy_examples(root)
    status, body = call(srv, "GET", "/api/setup")
    assert status == 200 and body["missing"] == ["profile", "settings", "priorities", "rules"]
    status, body = call(srv, "GET", "/api/setup/rules")
    assert status == 200 and body["sections"] and body["from_example"] is True
    status, body = call(srv, "GET", "/api/setup/profile_start")
    assert status == 200 and "contact" in body["data"] and body["schema"]
    assert call(srv, "GET", "/api/setup/passwords")[0] == 400


def test_an_invalid_settings_save_is_400_with_the_loaders_message(app):
    srv, _, _, root = app
    copy_examples(root)
    _, loaded = call(srv, "GET", "/api/setup/settings")
    data = loaded["data"]
    data["roles"][0]["max_level"] = "overlord"
    status, body = call(srv, "POST", "/api/setup/settings", {"data": data})
    assert status == 400 and "max_level" in body["error"]
    assert not (root / "jobs" / "settings.json").exists()
    data["roles"][0]["max_level"] = "senior"
    assert call(srv, "POST", "/api/setup/settings", {"data": data})[0] == 200
    assert (root / "jobs" / "settings.json").exists()


def test_the_guided_start_once_then_409(app):
    srv, _, _, root = app
    copy_examples(root)
    _, tpl = call(srv, "GET", "/api/setup/profile_start")
    a = tpl["data"]
    a["contact"].update({"name": "Sam Rivera", "email": "sam@example.com"})
    a.update({"headline": "QA", "summary": "QA engineer.", "variant_key": "qa"})
    a["skill_groups"]["core"] = {"title": "QA", "items": ["Test design"]}
    a["roles"][0].update(
        {
            "id": "acme",
            "title": "QA",
            "org": "Acme",
            "start": "01/2022",
            "bullets": ["Owned the regression suite"],
        }
    )
    a["education"] = []
    status, body = call(srv, "POST", "/api/setup/profile/start", {"answers": a})
    assert status == 200, body
    assert call(srv, "POST", "/api/setup/profile/start", {"answers": a})[0] == 409


def test_preview_without_a_profile_is_409(app):
    srv, *_ = app
    status, body = call(srv, "POST", "/api/setup/preview", {"variant": "qa"})
    assert status == 409 and "profile" in body["error"]


# --- applications routes -----------------------------------------------------------


def seed(root):
    from jobtrack.models import Application
    from jobtrack.storage import Store

    store = Store(root / "applications.json").load()
    store.add(Application(id=1, company="Acme", role="QA", updated_at="2026-08-01T09:00:00"))
    store.add(Application(id=2, company="Globex", role="SDET", status="rejected"))
    store.save()


def test_applications_list_filters_and_404(app):
    srv, _, _, root = app
    seed(root)
    _, body = call(srv, "GET", "/api/applications?open=1")
    assert [r["id"] for r in body["apps"]] == [1] and body["stats"]["total"] == 2
    _, body = call(srv, "GET", "/api/applications?status=rejected&q=glo")
    assert [r["id"] for r in body["apps"]] == [2]
    assert call(srv, "GET", "/api/applications/1")[1]["company"] == "Acme"
    assert call(srv, "GET", "/api/applications/99")[0] == 404
    assert call(srv, "GET", "/api/applications?status=w")[0] == 400


def test_applications_writes(app):
    srv, _, _, root = app
    seed(root)
    status, body = call(
        srv, "POST", "/api/applications", {"fields": {"company": "Hooli", "role": "QA"}}
    )
    assert status == 200 and body["id"] == 3
    assert call(srv, "POST", "/api/applications/3", {"fields": {"company": ""}})[0] == 400
    assert call(srv, "POST", "/api/applications/3", {"fields": {"status": "offer"}})[1][
        "changed"
    ] == ["status=offer"]
    assert call(srv, "POST", "/api/applications/3/note", {"text": "call Friday"})[1]["notes"] == [
        "call Friday"
    ]
    assert (
        call(srv, "POST", "/api/applications/1/quick", {"action": "no_reply"})[1]["status"]
        == "rejected"
    )
    assert call(srv, "POST", "/api/applications/3/delete", {})[0] == 200
    assert call(srv, "POST", "/api/applications/3/delete", {})[0] == 404


def test_applications_csv_is_a_download(app):
    srv, _, _, root = app
    seed(root)
    port = srv.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", "/api/applications.csv?open=1", headers={"Host": f"127.0.0.1:{port}"})
    resp = conn.getresponse()
    body = resp.read().decode("utf-8-sig")
    conn.close()
    assert resp.status == 200
    assert resp.getheader("Content-Type").startswith("text/csv")
    assert resp.getheader("Content-Disposition").startswith('attachment; filename="applications-')
    assert "Acme" in body and "Globex" not in body


def test_a_refused_post_still_reads_its_body_so_the_client_gets_the_403(app):
    """Answering before the body was read closes a socket with unread data; on Windows that
    is a reset, and the client saw ConnectionAbortedError instead of the 403 (a flaky test
    once in ~50 runs, and a page that would have shown a network error)."""
    srv, *_ = app
    big = {"text": "x" * 300_000}
    for _ in range(10):
        assert call(srv, "POST", "/api/paste/assess", big, token=None)[0] == 403


def test_an_oversized_body_is_refused(app):
    srv, *_ = app
    port = srv.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.putrequest("POST", "/api/scrape", skip_host=True)
    conn.putheader("Host", f"127.0.0.1:{port}")
    conn.putheader("X-FYJ-Token", TOKEN)
    conn.putheader("Content-Length", str(server.MAX_BODY + 1))
    conn.endheaders()
    resp = conn.getresponse()
    assert resp.status == 413
    conn.close()
