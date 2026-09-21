"""Phase detection: the /jobhunt table, as the UI's step bar reads it."""

from __future__ import annotations

import pytest
from jobs import review
from jobs.score import Scored
from jobs.sources import Posting
from jobs.ui import phase
from jobs.ui.paths import Paths

TODAY = "2026-09-21"


def lead(url: str) -> Scored:
    p = Posting(source="t", company="Acme", title="QA", url=url, location="Remote")
    s = Scored(p, 100, "qa_gaming", [])
    s.tailor = {
        "company": "Acme",
        "job_title": "QA",
        "variant": "qa_gaming",
        "matched": [],
        "gaps": [],
        "slug": "acme-qa",
        "url": url,
    }
    return s


def sheet(paths: Paths, when: str, mark: str | None = None) -> None:
    review.write_sheet(
        [lead("https://x/1"), lead("https://x/2")], when, sheet=paths.sheet, data=paths.data
    )
    if mark:
        review.set_mark("https://x/1", mark, sheet=paths.sheet)


def briefs(paths: Paths, *states: str) -> None:
    blocks = [
        f"## Acme{i} — QA\n- **Status:** {s}\n- **CV:** `qa_gaming` → "
        f"`cv/out/tailored/acme{i}-qa/*.pdf`\n- **Link:** https://x/{i}\n"
        for i, s in enumerate(states)
    ]
    paths.briefs.parent.mkdir(parents=True, exist_ok=True)
    paths.briefs.write_text("# Tailored applications\n\n" + "\n".join(blocks), encoding="utf-8")


@pytest.fixture
def paths(tmp_path):
    return Paths.under(tmp_path)


def test_nothing_yet_means_scrape(paths):
    assert phase.detect(paths, TODAY) == "scrape"


def test_yesterdays_sheet_means_scrape(paths):
    sheet(paths, "2026-09-20", review.TAKE)
    assert phase.detect(paths, TODAY) == "scrape"


def test_todays_sheet_is_review_marked_or_not(paths):
    sheet(paths, TODAY)
    assert phase.detect(paths, TODAY) == "review"
    review.set_mark("https://x/1", review.TAKE, sheet=paths.sheet)
    assert phase.detect(paths, TODAY) == "review"


def test_pending_briefs_mean_apply_even_over_a_fresh_sheet(paths):
    sheet(paths, TODAY)
    briefs(paths, "applied", "pending")
    assert phase.detect(paths, TODAY) == "apply"


def test_all_briefs_marked_means_close(paths):
    briefs(paths, "applied", "aborted")
    assert phase.detect(paths, TODAY) == "close"


def test_an_emptied_briefs_file_falls_back_to_the_sheet(paths):
    paths.briefs.parent.mkdir(parents=True, exist_ok=True)
    paths.briefs.write_text("# Tailored applications\n", encoding="utf-8")
    sheet(paths, TODAY)
    assert phase.detect(paths, TODAY) == "review"


def test_paths_under_mirrors_the_repo_layout(tmp_path):
    p = Paths.under(tmp_path)
    assert p.sheet == tmp_path / "TODAY_SCRAPING.md"
    assert p.briefs == tmp_path / "cv" / "out" / "tailored" / "BRIEFS.md"
    assert p.tailored == tmp_path / "cv" / "out" / "tailored"
    assert p.store == tmp_path / "applications.json"
    assert p.applications == tmp_path / "applications"
