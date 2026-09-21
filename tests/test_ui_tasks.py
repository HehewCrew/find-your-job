"""TaskRunner: one long job at a time, its progress as a list of events a page can follow."""

from __future__ import annotations

import threading

import pytest
from jobs.progress import emit
from jobs.ui.tasks import Busy, TaskRunner


def wait_done(runner: TaskRunner) -> list[dict]:
    events, finished = [], False
    while not finished:
        new, finished = runner.events(len(events), timeout=5)
        events += new
    return events


def test_progress_then_done_in_order():
    runner = TaskRunner()

    def job(report):
        emit(report, "fetch", "one", done=1, total=2)
        emit(report, "fetch", "two", done=2, total=2, detail=True)
        return {"leads": 3}

    runner.start("scrape", job)
    events = wait_done(runner)
    assert [e["type"] for e in events] == ["progress", "progress", "done"]
    assert events[0]["message"] == "one" and events[1]["detail"] is True
    assert events[-1] == {"type": "done", "task": "scrape", "result": {"leads": 3}}
    assert runner.running() is None


def test_a_second_task_is_refused_while_one_runs():
    runner = TaskRunner()
    gate = threading.Event()
    runner.start("scrape", lambda report: gate.wait(5) and {})
    try:
        assert runner.running() == "scrape"
        with pytest.raises(Busy, match="A scrape is already running"):
            runner.start("build", lambda report: {})
    finally:
        gate.set()
    wait_done(runner)


def test_a_failing_task_reports_and_frees_the_runner():
    runner = TaskRunner()

    def job(report):
        raise RuntimeError("Word crashed")

    runner.start("build", job)
    assert wait_done(runner)[-1] == {"type": "failed", "task": "build", "message": "Word crashed"}
    runner.start("scrape", lambda report: {})
    assert wait_done(runner)[-1]["type"] == "done"


def test_events_since_returns_only_newer_events():
    runner = TaskRunner()
    runner.start("scrape", lambda report: emit(report, "fetch", "a") or {})
    wait_done(runner)
    assert [e["type"] for e in runner.events(1)[0]] == ["done"]
    assert runner.events(2) == ([], True)


def test_a_new_task_starts_a_fresh_event_list():
    runner = TaskRunner()
    runner.start("scrape", lambda report: emit(report, "fetch", "a") or {})
    wait_done(runner)
    runner.start("build", lambda report: {})
    assert [e["type"] for e in wait_done(runner)] == ["done"]
