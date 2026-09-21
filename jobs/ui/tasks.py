"""One long job at a time - a scrape, a CV build, a paste - on a worker thread.

The page starts a task, then follows its events: every `Progress` the job reports, then one
`done` (with the job's result) or `failed` (with the reason). Events are kept in a list for
the current task, so a page that connects late, or reconnects, reads from where it left off.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import asdict

from jobs.progress import Progress, Report

Job = Callable[[Report], dict]


class Busy(Exception):
    """A task is already running; the page disables its start buttons meanwhile."""


class TaskRunner:
    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._kind: str | None = None
        self._events: list[dict] = []
        self._finished = True

    def running(self) -> str | None:
        with self._cond:
            return self._kind

    def start(self, kind: str, job: Job) -> None:
        with self._cond:
            if self._kind is not None:
                raise Busy(f"A {self._kind} is already running.")
            self._kind = kind
            self._events = []
            self._finished = False
        threading.Thread(target=self._run, args=(kind, job), daemon=True).start()

    def events(self, since: int = 0, timeout: float = 15.0) -> tuple[list[dict], bool]:
        """Events after the first `since`, waiting up to `timeout` for one; and whether the
        task has finished (after which no more will come)."""
        with self._cond:
            self._cond.wait_for(lambda: len(self._events) > since or self._finished, timeout)
            return self._events[since:], self._finished

    def _push(self, event: dict) -> None:
        with self._cond:
            self._events.append(event)
            self._cond.notify_all()

    def _report(self, p: Progress) -> None:
        self._push({"type": "progress", **asdict(p)})

    def _run(self, kind: str, job: Job) -> None:
        try:
            result = job(self._report)
            final = {"type": "done", "task": kind, "result": result}
        # build_variant raises SystemExit for a variant missing from profile.json.
        except (Exception, SystemExit) as exc:  # noqa: BLE001
            final = {"type": "failed", "task": kind, "message": str(exc) or type(exc).__name__}
        with self._cond:
            self._events.append(final)
            self._kind = None
            self._finished = True
            self._cond.notify_all()
