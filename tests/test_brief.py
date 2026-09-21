from __future__ import annotations

from pathlib import Path

import pytest
from jobs import brief

from jobtrack.models import Application, ValidationError
from jobtrack.storage import Store

SAMPLE = """# Tailored applications

## Anthropic — Red Team Engineer, Safeguards
- **CV:** `ai_qa` → `cv/out/tailored/anthropic-red-team-engineer-safeguards/*.pdf`
- **Link:** https://job-boards.greenhouse.io/anthropic/jobs/5320469008
- **Matched skills:** REST APIs, Test automation
- **Gaps to expect:** Machine learning

## prolific — Data Analyst - Supply
- **Status:** pending
- **CV:** `data_analyst` → `cv/out/tailored/prolific-data-analyst-supply/*.pdf`
- **Link:** https://www.arbeitnow.co.uk/jobs/companies/prolific/data-analyst-supply-139841
- **Matched skills:** Python
- **Gaps to expect:** SQL
"""


@pytest.fixture(autouse=True)
def isolated_tailored(tmp_path, monkeypatch):
    """Point TAILORED_DIR at a temp folder for *every* test.

    `close` deletes from this directory. A test that forgets to isolate it would
    delete real CVs out of cv/out/tailored/ — which is exactly what happened once.
    Tests needing specific files there can still monkeypatch over this.
    """
    path = tmp_path / "tailored-default"
    path.mkdir()
    monkeypatch.setattr(brief, "TAILORED_DIR", path)
    return path


def make_cv(tailored: Path, slug: str, name: str = "Your_Name_CV.pdf") -> Path:
    """A generated tailored CV, in the layout build.py writes: `<slug>/<name>.pdf`.

    Every variant produces the same filename, so the posting is identified by the folder.
    """
    path = tailored / slug / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.4")
    return path


@pytest.fixture
def briefs_file(tmp_path):
    path = tmp_path / "BRIEFS.md"
    path.write_text(SAMPLE, encoding="utf-8")
    return path


@pytest.fixture
def tracked(tmp_path):
    """A jobtrack store holding both postings as wishlist entries."""
    store = Store(tmp_path / "applications.json").load()
    store.add(
        Application(
            id=1,
            company="Anthropic",
            role="Red Team Engineer, Safeguards",
            status="wishlist",
            url="https://job-boards.greenhouse.io/anthropic/jobs/5320469008",
        )
    )
    store.add(
        Application(
            id=2,
            company="prolific",
            role="Data Analyst - Supply",
            status="wishlist",
            url="https://www.arbeitnow.co.uk/jobs/companies/prolific/data-analyst-supply-139841",
        )
    )
    store.save()
    return store


def run(argv, briefs_file, tmp_path, store_path=None):
    args = ["--briefs", str(briefs_file), "--root", str(tmp_path), *argv]
    if store_path is not None:
        args += ["--file", str(store_path)]
    return brief.main(args)


# -- parsing ------------------------------------------------------------


def test_parse_reads_every_field():
    b = brief.parse(SAMPLE)[0]
    assert (b.company, b.title) == ("Anthropic", "Red Team Engineer, Safeguards")
    assert b.variant == "ai_qa"
    assert b.slug == "anthropic-red-team-engineer-safeguards"
    assert b.url.endswith("5320469008")
    assert b.gaps == "Machine learning"


def test_parse_still_reads_the_old_cv_shape():
    """The slug used to live in the filename. A BRIEFS.md written before the layout
    changed must still file rather than losing track of which posting it is."""
    old = SAMPLE.replace(
        "`cv/out/tailored/anthropic-red-team-engineer-safeguards/*.pdf`",
        "`cv/out/tailored/*__anthropic-red-team-engineer-safeguards.pdf`",
    )
    b = brief.parse(old)[0]
    assert b.variant == "ai_qa"
    assert b.slug == "anthropic-red-team-engineer-safeguards"


def test_close_finds_a_cv_left_in_the_old_flat_layout(briefs_file, tmp_path, tracked, monkeypatch):
    tailored = tmp_path / "tailored"
    tailored.mkdir()
    cv = tailored / "Your_Name_AIQAEngineer__anthropic-red-team-engineer-safeguards.pdf"
    cv.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(brief, "TAILORED_DIR", tailored)

    run(["applied", "1"], briefs_file, tmp_path)
    run(["aborted", "2"], briefs_file, tmp_path)
    run(["close"], briefs_file, tmp_path, tracked.path)

    folder = tmp_path / brief.FOLDER / "anthropic-red-team-engineer-safeguards"
    assert (folder / cv.name).exists()
    assert not cv.exists()
    assert tailored.exists()  # pruning must not take the root the fallback lives in


def test_brief_without_status_line_is_pending():
    first, second = brief.parse(SAMPLE)
    assert first.state == brief.PENDING and first.status_line is None
    assert second.state == brief.PENDING and second.status_line is not None


@pytest.mark.parametrize(
    "written",
    [
        "aborted · 2026-08-04 · US-only",  # what the tool writes
        "aborted - 2026-08-04 . US-only",  # what a hand-edit looks like
        "aborted, 2026-08-04, US-only",
        "aborted 2026-08-04 US-only",
    ],
)
def test_status_survives_whatever_separator_was_typed(written):
    """The written separator is "·", which no keyboard offers - so a hand-edited
    brief must still parse, or it is silently treated as unmarked."""
    assert brief.parse_status(written) == ("aborted", "2026-08-04", "US-only")


def test_status_keeps_hyphens_inside_the_reason():
    assert brief.parse_status("aborted - 2026-08-04 . under-qualified")[2] == "under-qualified"


def test_unreadable_status_is_reported_not_rounded_to_pending(briefs_file, tmp_path, capsys):
    text = briefs_file.read_text(encoding="utf-8").replace(
        "- **Status:** pending", "- **Status:** nonsense here"
    )
    briefs_file.write_text(text, encoding="utf-8")
    assert brief.parse_status("nonsense here")[0] == "nonsense here"
    assert run([], briefs_file, tmp_path) == 2
    assert "could not be read" in capsys.readouterr().err


def test_parse_reads_state_date_and_reason():
    text = SAMPLE.replace("- **Status:** pending", "- **Status:** aborted · 2026-08-04 · US-only")
    b = brief.parse(text)[1]
    assert (b.state, b.marked_on, b.reason) == ("aborted", "2026-08-04", "US-only")


# -- selecting ----------------------------------------------------------


def test_select_by_index_name_and_rest():
    briefs = brief.parse(SAMPLE)
    assert brief.select(briefs, ["1"])[0].company == "Anthropic"
    assert brief.select(briefs, ["prolific"])[0].company == "prolific"
    assert len(brief.select(briefs, ["rest"])) == 2
    assert len(brief.select(briefs, ["1", "anthropic"])) == 1  # no duplicates


@pytest.mark.parametrize("selector", ["9", "nonesuch"])
def test_select_rejects_bad_selectors(selector):
    with pytest.raises(ValidationError):
        brief.select(brief.parse(SAMPLE), [selector])


def test_unknown_selector_exits_2(briefs_file, tmp_path, capsys):
    assert run(["applied", "nonesuch"], briefs_file, tmp_path) == 2
    assert "no brief matches" in capsys.readouterr().err


# -- marking ------------------------------------------------------------


def test_mark_inserts_a_status_line_when_absent(briefs_file, tmp_path):
    assert run(["applied", "1"], briefs_file, tmp_path) == 0
    b = brief.parse(briefs_file.read_text(encoding="utf-8"))[0]
    assert b.state == "applied"
    assert b.marked_on


def test_mark_replaces_an_existing_status_line(briefs_file, tmp_path):
    run(["applied", "2"], briefs_file, tmp_path)
    run(["aborted", "2", "--reason", "SQL-heavy"], briefs_file, tmp_path)
    text = briefs_file.read_text(encoding="utf-8")
    assert text.count("**Status:**") == 1  # rewritten in place, not stacked up
    assert brief.parse(text)[1].state == "aborted"
    assert brief.parse(text)[1].reason == "SQL-heavy"


def test_mark_keeps_the_other_brief_untouched(briefs_file, tmp_path):
    run(["applied", "1"], briefs_file, tmp_path)
    assert brief.parse(briefs_file.read_text(encoding="utf-8"))[1].state == brief.PENDING


def test_list_reports_counts(briefs_file, tmp_path, capsys):
    run(["applied", "1"], briefs_file, tmp_path)
    assert run([], briefs_file, tmp_path) == 0
    assert "1 applied" in capsys.readouterr().out


def test_list_on_a_missing_file_exits_1(tmp_path):
    assert run([], tmp_path / "gone.md", tmp_path) == 1


# -- closing ------------------------------------------------------------


def test_close_files_the_cv_questions_log_and_jobtrack(briefs_file, tmp_path, tracked, monkeypatch):
    tailored = tmp_path / "tailored"
    tailored.mkdir()
    cv = make_cv(tailored, "anthropic-red-team-engineer-safeguards")
    cv.write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setattr(brief, "TAILORED_DIR", tailored)

    run(["applied", "1"], briefs_file, tmp_path)
    run(["aborted", "2", "--reason", "SQL-heavy"], briefs_file, tmp_path)
    assert run(["close"], briefs_file, tmp_path, tracked.path) == 0

    folder = tmp_path / brief.FOLDER / "anthropic-red-team-engineer-safeguards"
    assert (folder / cv.name).read_bytes() == b"%PDF-1.4 fake"
    questions = (folder / "questions.md").read_text(encoding="utf-8")
    assert "Red Team Engineer" in questions
    assert "jobtrack id:** 1" in questions

    log = (tmp_path / brief.FOLDER / "LOG.md").read_text(encoding="utf-8")
    assert "### Applied (1)" in log
    assert "SQL-heavy" in log

    after = Store(tracked.path).load()
    assert after.get(1).status == "applied"
    assert after.get(2).status == "withdrawn"


def test_close_clears_the_tailored_cvs_it_filed(briefs_file, tmp_path, tracked, monkeypatch):
    tailored = tmp_path / "tailored"
    tailored.mkdir()
    applied_cv = make_cv(tailored, "anthropic-red-team-engineer-safeguards")
    aborted_cv = make_cv(tailored, "prolific-data-analyst-supply")
    byhand = tailored / "Your_Name_CV_Written_By_Hand.pdf"
    byhand.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(brief, "TAILORED_DIR", tailored)

    run(["applied", "1"], briefs_file, tmp_path)
    run(["aborted", "2"], briefs_file, tmp_path)
    run(["close"], briefs_file, tmp_path, tracked.path)

    folder = tmp_path / brief.FOLDER / "anthropic-red-team-engineer-safeguards"
    assert (folder / applied_cv.name).exists()  # the copy that was sent survives
    assert not applied_cv.exists()
    assert not aborted_cv.exists()
    assert not applied_cv.parent.exists()  # the emptied folder goes too
    assert not aborted_cv.parent.exists()
    assert tailored.exists()  # but never the tailored root, which holds BRIEFS.md
    assert byhand.exists()  # not a generated location - never touched


def test_close_copies_the_jd_and_prunes_its_source(briefs_file, tmp_path, tracked, monkeypatch):
    """jd.md rides along with the CV: copied into applications/<slug>/, then its source
    is removed so the tailored <slug>/ folder ends up empty and gets pruned - same
    contract as the CV, just one more file to carry across."""
    tailored = tmp_path / "tailored"
    tailored.mkdir()
    cv = make_cv(tailored, "anthropic-red-team-engineer-safeguards")
    jd = cv.parent / "jd.md"
    jd.write_text("# Anthropic — Red Team Engineer\n\nfull posting text", encoding="utf-8")
    monkeypatch.setattr(brief, "TAILORED_DIR", tailored)

    run(["applied", "1"], briefs_file, tmp_path)
    run(["aborted", "2"], briefs_file, tmp_path)
    run(["close"], briefs_file, tmp_path, tracked.path)

    folder = tmp_path / brief.FOLDER / "anthropic-red-team-engineer-safeguards"
    assert "full posting text" in (folder / "jd.md").read_text(encoding="utf-8")
    assert not jd.exists()
    assert not jd.parent.exists()  # emptied of both CV and jd.md, so pruned like before


def test_close_deletes_the_jd_for_an_aborted_posting(briefs_file, tmp_path, tracked, monkeypatch):
    """Not considered means not applied - the JD goes the same way as the CV, not kept."""
    tailored = tmp_path / "tailored"
    tailored.mkdir()
    cv = make_cv(tailored, "prolific-data-analyst-supply")
    jd = cv.parent / "jd.md"
    jd.write_text("# prolific — Data Analyst\n\nfull posting text", encoding="utf-8")
    monkeypatch.setattr(brief, "TAILORED_DIR", tailored)

    run(["aborted", "2", "--reason", "SQL-heavy"], briefs_file, tmp_path)
    run(["applied", "1"], briefs_file, tmp_path)
    run(["close"], briefs_file, tmp_path, tracked.path)

    assert not jd.exists()
    assert not jd.parent.exists()
    assert not (tmp_path / brief.FOLDER / "prolific-data-analyst-supply").exists()


def test_close_keeps_a_cv_it_could_not_copy(briefs_file, tmp_path, tracked, monkeypatch):
    """After closing, BRIEFS.md is empty and the CV can no longer be rebuilt from it -
    so a CV that never reached applications/ must not be deleted."""
    tailored = tmp_path / "tailored"
    tailored.mkdir()
    cv = make_cv(tailored, "anthropic-red-team-engineer-safeguards")
    monkeypatch.setattr(brief, "TAILORED_DIR", tailored)
    monkeypatch.setattr(brief.shutil, "copy2", lambda *a, **k: None)  # copy silently fails

    run(["applied", "all"], briefs_file, tmp_path)
    run(["close"], briefs_file, tmp_path, tracked.path)
    assert cv.exists()


def test_keep_cvs_leaves_them_alone(briefs_file, tmp_path, tracked, monkeypatch):
    tailored = tmp_path / "tailored"
    tailored.mkdir()
    cv = make_cv(tailored, "prolific-data-analyst-supply")
    monkeypatch.setattr(brief, "TAILORED_DIR", tailored)
    run(["aborted", "all"], briefs_file, tmp_path)
    run(["close", "--keep-cvs"], briefs_file, tmp_path, tracked.path)
    assert cv.exists()


def test_close_empties_the_briefing_sheet(briefs_file, tmp_path, tracked):
    run(["applied", "all"], briefs_file, tmp_path)
    run(["close"], briefs_file, tmp_path, tracked.path)
    assert briefs_file.read_text(encoding="utf-8") == brief.HEADER
    assert brief.parse(briefs_file.read_text(encoding="utf-8")) == []


def test_close_refuses_while_briefs_are_pending(briefs_file, tmp_path, tracked, capsys):
    run(["applied", "1"], briefs_file, tmp_path)
    assert run(["close"], briefs_file, tmp_path, tracked.path) == 1
    assert "still pending" in capsys.readouterr().err
    assert "## " in briefs_file.read_text(encoding="utf-8")  # nothing was emptied


def test_close_with_nothing_marked_exits_1(briefs_file, tmp_path, tracked):
    assert run(["close"], briefs_file, tmp_path, tracked.path) == 1


def test_dry_run_writes_nothing(briefs_file, tmp_path, tracked):
    run(["applied", "all"], briefs_file, tmp_path)
    assert run(["close", "--dry-run"], briefs_file, tmp_path, tracked.path) == 0
    assert not (tmp_path / brief.FOLDER).exists()
    assert "## " in briefs_file.read_text(encoding="utf-8")
    assert Store(tracked.path).load().get(1).status == "wishlist"


def test_close_adds_a_jobtrack_entry_when_the_posting_is_untracked(briefs_file, tmp_path):
    store = Store(tmp_path / "empty.json")
    store.save()
    run(["applied", "all"], briefs_file, tmp_path)
    run(["close"], briefs_file, tmp_path, store.path)
    apps = Store(store.path).load().applications
    assert [a.status for a in apps] == ["applied", "applied"]


def test_aborting_an_untracked_posting_still_records_it_as_withdrawn(briefs_file, tmp_path):
    """Without an entry the posting returns in tomorrow's scrape - the decision must stick."""
    store = Store(tmp_path / "empty.json")
    store.save()
    run(["aborted", "all", "--reason", "US-only"], briefs_file, tmp_path)
    run(["close"], briefs_file, tmp_path, store.path)
    apps = Store(store.path).load().applications
    assert [a.status for a in apps] == ["withdrawn", "withdrawn"]
    assert [a.id for a in apps] == [1, 2]
    assert "US-only" in apps[0].notes[0]


def test_close_matches_on_url_when_the_company_name_drifted(briefs_file, tmp_path):
    """Company names change between a scrape and a later run; the URL is the stable key."""
    store = Store(tmp_path / "drift.json").load()
    store.add(
        Application(
            id=1,
            company="Anthropicpbc",  # not what BRIEFS.md says
            role="Red Team Engineer",
            status="wishlist",
            url="https://job-boards.greenhouse.io/anthropic/jobs/5320469008",
        )
    )
    store.save()
    run(["applied", "1"], briefs_file, tmp_path)
    run(["aborted", "2"], briefs_file, tmp_path)
    run(["close"], briefs_file, tmp_path, store.path)
    after = Store(store.path).load()
    assert after.get(1).status == "applied"
    assert [a.company for a in after] == ["Anthropicpbc", "prolific"]  # matched, not duplicated


def test_second_close_the_same_day_extends_that_day_section(briefs_file, tmp_path, tracked):
    run(["applied", "1"], briefs_file, tmp_path)
    run(["aborted", "2"], briefs_file, tmp_path)
    run(["close"], briefs_file, tmp_path, tracked.path)
    briefs_file.write_text(SAMPLE, encoding="utf-8")
    run(["applied", "all"], briefs_file, tmp_path)
    run(["close"], briefs_file, tmp_path, tracked.path)
    log = (tmp_path / brief.FOLDER / "LOG.md").read_text(encoding="utf-8")
    assert log.count("# Application log") == 1
    assert log.count(f"## {brief.today()}") == 1
    assert log.count("Red Team Engineer") == 2


def test_answers_drafted_before_closing_survive_and_are_logged(
    briefs_file, tmp_path, tracked, isolated_tailored
):
    """The normal case for a form with questions - close must not clobber the answers."""
    for slug in ("anthropic-red-team-engineer-safeguards", "prolific-data-analyst-supply"):
        make_cv(isolated_tailored, slug)
    folder = tmp_path / brief.FOLDER / "anthropic-red-team-engineer-safeguards"
    folder.mkdir(parents=True)
    (folder / "questions.md").write_text("my answers", encoding="utf-8")
    run(["applied", "all"], briefs_file, tmp_path)
    run(["close"], briefs_file, tmp_path, tracked.path)
    assert (folder / "questions.md").read_text(encoding="utf-8") == "my answers"
    log = (tmp_path / brief.FOLDER / "LOG.md").read_text(encoding="utf-8")
    assert "Answers on file" in log
    assert "⚠️" not in log  # having answers already is not a problem


# -- scrape interop -----------------------------------------------------


def test_pending_counts_unresolved_briefs(briefs_file, tmp_path):
    assert brief.pending(briefs_file) == 2
    run(["applied", "1"], briefs_file, tmp_path)
    assert brief.pending(briefs_file) == 1
    assert brief.pending(tmp_path / "gone.md") == 0


def test_write_briefs_stashes_a_sheet_that_was_never_closed(briefs_file):
    from jobs import tailor

    tailor.write_briefs([], briefs_file)
    assert briefs_file.with_name("BRIEFS.prev.md").read_text(encoding="utf-8") == SAMPLE
    assert brief.parse(briefs_file.read_text(encoding="utf-8")) == []


def test_close_files_a_docx_when_no_pdf_was_made(briefs_file, tmp_path, tracked, monkeypatch):
    tailored = tmp_path / "tailored"
    docx = make_cv(tailored, "anthropic-red-team-engineer-safeguards", "Your_Name_CV.docx")
    monkeypatch.setattr(brief, "TAILORED_DIR", tailored)

    run(["applied", "1"], briefs_file, tmp_path)
    run(["aborted", "2"], briefs_file, tmp_path)
    assert run(["close"], briefs_file, tmp_path, tracked.path) == 0

    folder = tmp_path / brief.FOLDER / "anthropic-red-team-engineer-safeguards"
    assert (folder / docx.name).exists()
    assert not docx.exists(), "the tailored copy is swept once filed"
    log = (tmp_path / brief.FOLDER / "LOG.md").read_text(encoding="utf-8")
    assert "no tailored CV found" not in log
    assert "`Your_Name_CV.docx`" in (folder / "questions.md").read_text(encoding="utf-8")


def test_close_files_both_the_docx_and_the_pdf(briefs_file, tmp_path, tracked, monkeypatch):
    tailored = tmp_path / "tailored"
    slug = "anthropic-red-team-engineer-safeguards"
    make_cv(tailored, slug, "Your_Name_CV.pdf")
    make_cv(tailored, slug, "Your_Name_CV.docx")
    monkeypatch.setattr(brief, "TAILORED_DIR", tailored)

    run(["applied", "1"], briefs_file, tmp_path)
    run(["aborted", "2"], briefs_file, tmp_path)
    run(["close"], briefs_file, tmp_path, tracked.path)

    folder = tmp_path / brief.FOLDER / slug
    assert sorted(p.name for p in folder.glob("Your_Name_CV.*")) == [
        "Your_Name_CV.docx",
        "Your_Name_CV.pdf",
    ]
    assert not (tailored / slug).exists(), "the emptied tailored folder is pruned"


def test_a_docx_placed_by_hand_counts_as_the_cv(briefs_file, tmp_path, tracked):
    folder = tmp_path / brief.FOLDER / "anthropic-red-team-engineer-safeguards"
    folder.mkdir(parents=True)
    (folder / "My_CV.docx").write_bytes(b"PK")

    run(["applied", "1"], briefs_file, tmp_path)
    run(["aborted", "2"], briefs_file, tmp_path)
    run(["close"], briefs_file, tmp_path, tracked.path)

    log = (tmp_path / brief.FOLDER / "LOG.md").read_text(encoding="utf-8")
    assert "no tailored CV found" not in log


def test_a_word_lock_file_is_never_filed_as_a_cv(briefs_file, tmp_path, tracked, monkeypatch):
    """Word leaves `~$<name>.docx` beside a document it has open."""
    tailored = tmp_path / "tailored"
    slug = "anthropic-red-team-engineer-safeguards"
    make_cv(tailored, slug, "Your_Name_CV.docx")
    make_cv(tailored, slug, "~$ur_Name_CV.docx")
    monkeypatch.setattr(brief, "TAILORED_DIR", tailored)

    assert [p.name for p in brief._cvs_for(slug, tailored)] == ["Your_Name_CV.docx"]
