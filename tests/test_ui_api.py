"""The UI's API functions, against a tmp_path copy of the repo layout - no HTTP here."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest
from jobs import review, scrape
from jobs.errors import JobsError, Skipped
from jobs.score import Scored
from jobs.sources import Posting
from jobs.ui import api
from jobs.ui.paths import Paths
from jobs.ui.tasks import TaskRunner

from jobtrack.models import ValidationError
from jobtrack.storage import Store

JD = """Senior QA Automation Engineer

Remote (worldwide)

We are a game studio. You will own the test strategy: designing test plans, building
automated test suites in Python, and driving regression testing across releases. You
will run API testing against our REST services and own defect management in Jira.
"""


def lead(company: str, url: str) -> Scored:
    p = Posting(
        source="remotive",
        company=company,
        title="QA Analyst",
        url=url,
        location="Remote",
        description=JD,
        posted_at="2026-09-20",
    )
    s = Scored(p, 120, "qa_gaming", ["remote-anywhere", "gaming"])
    s.tailor = {
        "company": company,
        "job_title": "QA Analyst",
        "variant": "qa_gaming",
        "matched": ["Python", "Jira"],
        "gaps": ["SQL"],
        "slug": f"{company.lower()}-qa-analyst",
        "url": url,
    }
    return s


@pytest.fixture
def paths(tmp_path, cv_sandbox):
    """The repo layout under tmp_path, with CVs where cv_sandbox builds them."""
    tailored = cv_sandbox / "tailored"
    return replace(Paths.under(tmp_path), tailored=tailored, briefs=tailored / "BRIEFS.md")


@pytest.fixture
def sheet(paths):
    today = date.today().isoformat()
    review.write_sheet(
        [lead("Roblox", "https://x/1"), lead("Riot", "https://x/2")],
        today,
        sheet=paths.sheet,
        data=paths.data,
    )
    return paths.sheet


def finish(runner: TaskRunner) -> dict:
    events, finished = [], False
    while not finished:
        new, finished = runner.events(len(events), timeout=10)
        events += new
    return events[-1]


# --- state and review --------------------------------------------------------------


def test_state_of_a_fresh_repo(paths):
    s = api.state(paths, TaskRunner(), "none")
    assert s["phase"] == "scrape"
    assert s["leads"] == {"take": 0, "skip": 0, "pending": 0}
    assert s["briefs"] == {"pending": 0, "applied": 0, "aborted": 0}
    assert (s["pdf_engine"], s["task"], s["example_settings"]) == ("none", None, False)


def test_leads_carry_what_the_review_step_shows(paths, sheet):
    got = api.leads(paths)
    assert [x["company"] for x in got] == ["Roblox", "Riot"]
    first = got[0]
    assert first["mark"] == "pending" and first["score"] == 120
    assert first["matched"] == ["Python", "Jira"] and first["gaps"] == ["SQL"]
    assert "Python" in first["description"]


def test_mark_lead_writes_the_sheet(paths, sheet):
    assert api.mark_lead(paths, "https://x/2", "take")["mark"] == "take"
    assert [x.mark for x in review.read_sheet(sheet=paths.sheet, data=paths.data)] == [
        "pending",
        "take",
    ]
    assert api.state(paths, TaskRunner(), "none")["leads"]["take"] == 1


def test_mark_lead_on_a_changed_sheet_refuses(paths, sheet):
    with pytest.raises(JobsError):
        api.mark_lead(paths, "https://x/9", "take")


# --- build, apply, close -----------------------------------------------------------


def test_pick_with_nothing_ticked_refuses_before_starting(paths, sheet):
    runner = TaskRunner()
    with pytest.raises(JobsError):
        api.start_pick(paths, runner)
    assert runner.running() is None


def test_a_full_day_through_the_api(paths, sheet):
    api.mark_lead(paths, "https://x/1", "take")
    api.mark_lead(paths, "https://x/2", "skip")
    runner = TaskRunner()

    api.start_pick(paths, runner)
    done = finish(runner)
    assert done["type"] == "done"
    cv = done["result"]["cvs"][0]
    assert cv["company"] == "Roblox" and cv["error"] == ""
    assert cv["docx"].endswith(".docx") and cv["pdf"] is None and cv["pages"] is None

    assert [c["slug"] for c in api.cvs(paths)] == ["roblox-qa-analyst"]
    got = api.briefs(paths)
    assert [(b["company"], b["state"]) for b in got] == [("Roblox", "pending")]
    assert got[0]["cv"][0].endswith(".docx")
    assert api.state(paths, runner, "none")["phase"] == "apply"

    marked = api.mark_briefs(paths, ["1"], "applied", "")
    assert marked == {"changed": ["Roblox — QA Analyst"], "pending": 0}

    preview = api.close(paths, dry_run=True, force=False)
    assert [f["company"] for f in preview["filed"]] == ["Roblox"]
    assert not paths.applications.exists()

    closed = api.close(paths, dry_run=False, force=False)
    assert closed["cleared"] == 1
    folder = paths.applications / "roblox-qa-analyst"
    assert any(p.suffix == ".docx" for p in folder.iterdir())
    assert Store(paths.store).load().get(1).status == "applied"


# --- paste -------------------------------------------------------------------------


def test_assess_refuses_a_description_too_short(paths):
    with pytest.raises(ValidationError, match="characters"):
        api.assess(
            paths, text="QA Engineer", company="Acme", title="", url="", location="", variant=""
        )


def test_assess_then_build(paths):
    verdict = api.assess(
        paths, text=JD, company="Acme", title="", url="", location="", variant="qa_gaming"
    )
    assert verdict["decision"] in ("apply", "consider", "skip")
    assert verdict["title"] == "Senior QA Automation Engineer"
    assert "Python" in verdict["matched"]

    runner = TaskRunner()
    api.start_paste(paths, runner, verdict["id"], force=True)
    done = finish(runner)
    assert done["type"] == "done" and done["result"]["cv"]["docx"].endswith(".docx")
    assert "## Acme" in paths.briefs.read_text(encoding="utf-8")


def test_a_skip_verdict_is_refused_unless_forced(paths):
    verdict = api.assess(
        paths, text=JD, company="Acme", title="", url="", location="", variant="qa_gaming"
    )
    a, _ = api._ASSESSMENTS[verdict["id"]]
    a.rules = api.paste.llm.Verdict("skip", blockers=["US only"])
    runner = TaskRunner()
    with pytest.raises(Skipped):
        api.start_paste(paths, runner, verdict["id"], force=False)
    assert runner.running() is None


def test_an_unknown_verdict_id_asks_for_a_new_one(paths):
    with pytest.raises(JobsError, match="verdict"):
        api.start_paste(paths, TaskRunner(), "nope", force=False)


# --- scrape ------------------------------------------------------------------------


def test_scrape_writes_the_sheet_where_paths_says(paths, monkeypatch):
    posting = Posting(
        source="remotive",
        company="Roblox",
        title="QA Automation Engineer",
        url="https://x/9",
        location="Remote (worldwide)",
        description="Fully remote worldwide. " + JD,
        posted_at=date.today().isoformat(),
    )
    monkeypatch.setattr(scrape, "collect", lambda *a, **k: ([posting], []))
    monkeypatch.setattr(scrape.discover, "load_state", lambda *a, **k: {"boards": {}})
    monkeypatch.setattr(scrape, "_refresh_boards", lambda *a, **k: None)
    runner = TaskRunner()
    api.start_scrape(paths, runner)
    done = finish(runner)
    assert done["type"] == "done" and done["result"]["leads"] == 1
    assert paths.sheet.exists() and api.state(paths, runner, "none")["phase"] == "review"


# --- opening files -----------------------------------------------------------------


def test_open_refuses_anything_outside_the_cv_and_application_folders(paths):
    calls = []
    outside = paths.root / "secrets.txt"
    outside.write_text("x", encoding="utf-8")
    with pytest.raises(ValidationError):
        api.open_file(paths, str(outside), False, opener=lambda p, r: calls.append(p))
    with pytest.raises(ValidationError):
        api.open_file(paths, "cv/out/../../secrets.txt", False, opener=lambda p, r: calls.append(p))
    assert calls == []


def test_open_passes_an_allowed_file_to_the_opener(paths):
    cv = paths.root / "cv" / "out" / "tailored" / "acme" / "CV.docx"
    cv.parent.mkdir(parents=True)
    cv.write_bytes(b"PK")
    calls = []
    api.open_file(
        paths, "cv/out/tailored/acme/CV.docx", True, opener=lambda p, r: calls.append((p, r))
    )
    assert calls == [(cv.resolve(), True)]


def test_pdf_engine_is_one_of_three():
    assert api.pdf_engine() in ("word", "libreoffice", "none")
