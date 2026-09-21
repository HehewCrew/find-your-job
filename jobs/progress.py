"""Progress events for long-running jobs (scrape, pick, paste).

A `run`-style function takes `report: Report | None` and calls `emit()` as it goes. The CLI
passes `printer()`, which prints the lines the command always printed; the UI passes a
function that queues every event, detail ticks included, for a progress bar.

Events are emitted only from the thread that called `run`, never from pool workers, so a
reporter never receives two at once.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Progress:
    stage: str  # "fetch" | "rank" | "tailor" | "llm" | "write"
    message: str
    done: int | None = None
    total: int | None = None
    detail: bool = False  # a per-item tick; the CLI stays quiet on these


Report = Callable[[Progress], None]


def emit(
    report: Report | None,
    stage: str,
    message: str,
    *,
    done: int | None = None,
    total: int | None = None,
    detail: bool = False,
) -> None:
    if report is not None:
        report(Progress(stage, message, done, total, detail))


def printer() -> Report:
    """The CLI's reporter. The one place outside a `main` allowed to print, on its behalf."""

    def _print(p: Progress) -> None:
        if not p.detail:
            print(p.message)

    return _print
