"""Which step of the day the repo is in - the /jobhunt phase table as a pure function.

An unclosed BRIEFS.md outranks a fresh sheet: yesterday's applications are finished
before today's leads are reviewed, exactly as /jobhunt enters the loop.
"""

from __future__ import annotations

from jobs import brief, review
from jobs.ui.paths import Paths

SCRAPE, REVIEW, APPLY, CLOSE = "scrape", "review", "apply", "close"


def detect(paths: Paths, today: str) -> str:
    _, briefs = brief.load(paths.briefs)
    if briefs:
        return APPLY if any(b.state == brief.PENDING for b in briefs) else CLOSE
    if review.sheet_date(paths.sheet) == today:
        return REVIEW
    return SCRAPE
