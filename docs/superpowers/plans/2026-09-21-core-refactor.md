# Core Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split every `jobs/` command into a `run`-style function that returns data and reports progress, and a `main` that prints — without changing the CLI's output — and make tailored CVs DOCX-first.

**Architecture:** Golden-output tests pin today's CLI first. Then each module gains result dataclasses and pure functions; `main` becomes a printer over them. A tiny `jobs/progress.py` carries progress events; `jobs/errors.py` carries "nothing to do" errors. `cv/build.py` returns its warnings and which PDF engine ran instead of printing, and keeps the DOCX.

**Tech Stack:** Python 3 standard library only (runtime). pytest + ruff (dev). Word COM via pywin32 (optional, already present).

**Spec:** [docs/superpowers/specs/2026-09-21-core-refactor-design.md](../specs/2026-09-21-core-refactor-design.md)

## Global Constraints

- Standard library only for runtime code. No new dependencies.
- `from __future__ import annotations` at the top of every module; line length 100; ruff-formatted.
- A module's `run`-style functions never `print`, never call `input()`, never call `sys.exit`.
- CLI stdout, stderr and exit codes stay byte-identical to the golden files, except the two
  approved behaviour changes (pick survives one failing CV; DOCX-first filing).
- Exit codes: `0` success, `1` nothing to do / not found, `2` invalid input.
- The full suite (500 passed, 2 skipped at the start) and `ruff check . && ruff format --check .`
  pass after every task.
- Tests must never write into the real `cv/out/`, `TODAY_SCRAPING.*`, `jobs/ashby_boards.json`
  or `applications/`. Every test that builds or files a CV uses the `cv_sandbox` fixture.
- Commit messages end with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.

## File map

| File | Change |
|---|---|
| `jobs/progress.py` | **new** — `Progress`, `Report`, `emit()`, `printer()` |
| `jobs/errors.py` | **new** — `JobsError`, `Skipped` |
| `cv/build.py` | warnings list instead of prints; engine report; COM init off the main thread |
| `jobs/tailor.py` | `display_path()`; `write_jd` default resolved at call time; `CvResult`; `fit()` returns it and keeps the DOCX |
| `jobs/brief.py` | DOCX-aware `_cvs_for`/`file_brief`/`sweep_tailored`; `mark_briefs()`, `close()`, `StillPending` |
| `jobs/paste.py` | `assess(report=)`, `build()`, `PasteResult`; `_show` replaced by `tl.display_path` |
| `jobs/pick.py` | `choose()`, `run()`, `PickResult`, `cv_line()`; per-CV failure isolation |
| `jobs/scrape.py` | `run()`, `ScrapeResult`; `collect(report=)` per-source ticks |
| `tests/conftest.py` | `cv_sandbox` and `golden` fixtures |
| `tests/test_cli_parity.py` | **new** — golden-output tests for pick, paste, brief, scrape |
| `tests/golden/*.txt` | **new** — captured CLI output |
| `tests/test_progress.py`, `tests/test_tailor_fit.py`, `tests/test_pick.py`, `tests/test_scrape_run.py` | **new** |
| `tests/test_brief.py`, `tests/test_paste.py` | extended |
| `CLAUDE.md`, `jobs/README.md` | docs |

---

### Task 1: Pin today's CLI output

Captures golden output for `pick`, `paste`, `brief` and `scrape` on the *unchanged* code. Two
prerequisite fixes make that possible without touching behaviour for in-repo paths:
`pick`/`scrape` crash when printing a path outside the repo, and `write_jd` binds its default
folder at import time so tests cannot redirect it.

**Files:**
- Modify: `jobs/tailor.py` (add `display_path`, fix `write_jd` default)
- Modify: `jobs/paste.py` (use `tl.display_path`, delete `_show`)
- Modify: `jobs/pick.py:175`, `jobs/scrape.py:185,269` (use `tl.display_path`)
- Modify: `tests/conftest.py`
- Create: `tests/test_cli_parity.py`, `tests/golden/*.txt`

**Interfaces:**
- Produces: `tl.display_path(path: Path) -> str`; fixtures `cv_sandbox -> Path` (the sandbox
  `cv/out`) and `golden -> Callable[[str, int, str, str], None]` (name, exit code, stdout, stderr).

- [ ] **Step 1: Write the failing test for `display_path` and `write_jd`**

Append to `tests/test_paste.py`:

```python
def test_display_path_is_repo_relative_inside_the_repo():
    assert tl.display_path(tl.ROOT / "cv" / "x.pdf").replace("\\", "/") == "cv/x.pdf"


def test_display_path_falls_back_to_absolute_outside_the_repo(tmp_path):
    assert tl.display_path(tmp_path / "x.pdf") == str(tmp_path / "x.pdf")


def test_write_jd_follows_a_patched_tailored_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(tl, "TAILORED_DIR", tmp_path)
    path = tl.write_jd("Acme", "QA", "the text", "acme-qa")
    assert path == tmp_path / "acme-qa" / "jd.md"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_paste.py -k "display_path or patched_tailored" -v`
Expected: FAIL — `AttributeError: module 'jobs.tailor' has no attribute 'display_path'`, and the
`write_jd` test writes under the real `cv/out/tailored` instead of `tmp_path`.

If the `write_jd` test created `cv/out/tailored/acme-qa/`, delete that folder.

- [ ] **Step 3: Implement**

In `jobs/tailor.py`, after `TAILORED_DIR = ...`:

```python
def display_path(path: Path) -> str:
    """Repo-relative when it is inside the repo, absolute otherwise.

    --briefs, --file and test sandboxes can point anywhere, and Path.relative_to raises
    rather than falling back - which turned a successful build into a traceback after
    the work was already done.
    """
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)
```

Change `write_jd`'s signature and first line of its body:

```python
def write_jd(
    company: str, title: str, text: str, slug: str, tailored: Path | None = None
) -> Path | None:
    ...
    tailored = tailored or TAILORED_DIR
```

(Keep the docstring. Insert `tailored = tailored or TAILORED_DIR` as the first statement.)

In `jobs/paste.py`: delete `_show` and replace its three call sites with `tl.display_path(...)`.
In `jobs/pick.py` replace `args.briefs.relative_to(ROOT)` with `tl.display_path(args.briefs)`.
In `jobs/scrape.py` replace both `sheet.relative_to(HERE.parent)` with `tl.display_path(sheet)`.

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_paste.py -v`
Expected: PASS.

- [ ] **Step 5: Add the sandbox and golden fixtures**

Append to `tests/conftest.py`:

```python
ROOT = FIXTURES.parent.parent
GOLDEN = FIXTURES.parent / "golden"


@pytest.fixture
def cv_sandbox(tmp_path, monkeypatch):
    """Build tailored CVs into tmp_path from the example profile, with no PDF engine.

    Word and LibreOffice are both reported missing, so the result is the same on every
    machine: a .docx and no PDF. Returns the sandbox's `cv/out`.
    """
    from jobs import brief
    from jobs import tailor as tl

    out = tmp_path / "cvout"
    monkeypatch.setattr(tl.cvbuild, "OUT_DIR", out)
    monkeypatch.setattr(tl, "TAILORED_DIR", out / "tailored")
    monkeypatch.setattr(brief, "TAILORED_DIR", out / "tailored")
    monkeypatch.setattr(tl, "PROFILE_PATH", ROOT / "cv" / "profile.example.json")
    monkeypatch.setattr(tl.cvbuild, "_word_to_pdf", lambda paths, **kw: [])
    monkeypatch.setattr(tl.cvbuild, "_find_soffice", lambda: None)
    return out


@pytest.fixture
def golden(tmp_path):
    """Compare a CLI run to tests/golden/<name>.txt; UPDATE_GOLDEN=1 rewrites it.

    tmp_path is replaced by <tmp> and backslashes by slashes, so the file is the same on
    every machine and OS.
    """

    def check(name: str, code: int, out: str, err: str) -> None:
        text = f"exit={code}\n--- stdout\n{out}--- stderr\n{err}"
        text = text.replace(str(tmp_path), "<tmp>").replace("\\", "/")
        path = GOLDEN / f"{name}.txt"
        if os.environ.get("UPDATE_GOLDEN"):
            path.parent.mkdir(exist_ok=True)
            path.write_text(text, encoding="utf-8")
            return
        if not path.exists():
            pytest.fail(f"no golden file {path.name}; run once with UPDATE_GOLDEN=1")
        assert text == path.read_text(encoding="utf-8")

    return check
```

- [ ] **Step 6: Write the parity tests**

Create `tests/test_cli_parity.py`:

```python
"""Golden-output tests: the CLI prints exactly what it printed before the core refactor.

Captured on the pre-refactor code with UPDATE_GOLDEN=1. A diff here means terminal output
changed - intended only for the behaviour changes the spec names.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from jobs import brief, paste, pick, review, scrape
from jobs.score import Scored
from jobs.sources import Posting

JD = """Senior QA Automation Engineer

Remote (worldwide)

We are a game studio. You will own the test strategy: designing test plans, building
automated test suites in Python, and driving regression testing across releases. You
will run API testing against our REST services and own defect management in Jira.
"""


def lead(company: str, title: str, url: str) -> Scored:
    p = Posting(
        source="remotive",
        company=company,
        title=title,
        url=url,
        location="Remote",
        description=JD,
        posted_at="2026-09-20",
    )
    s = Scored(p, 120, "qa_gaming", ["remote-anywhere", "gaming"])
    s.tailor = {
        "company": company,
        "job_title": title,
        "variant": "qa_gaming",
        "matched": ["Python", "Test automation", "Regression testing", "API testing"],
        "gaps": [],
        "slug": f"{company.lower()}-qa",
        "url": url,
    }
    return s


@pytest.fixture
def sheet(tmp_path):
    md, js = tmp_path / "TODAY_SCRAPING.md", tmp_path / "TODAY_SCRAPING.json"
    leads = [lead("Roblox", "QA Analyst", "https://x/1"), lead("Riot", "QA Lead", "https://x/2")]
    review.write_sheet(leads, "2026-09-21", sheet=md, data=js)
    text = md.read_text(encoding="utf-8")
    text = text.replace("## [ ] 1.", "## [x] 1.").replace("## [ ] 2.", "## [-] 2.")
    md.write_text(text, encoding="utf-8")
    return md, js


def run_pick(argv, sheet, tmp_path):
    md, js = sheet
    return pick.main(
        [
            *argv,
            "--sheet",
            str(md),
            "--data",
            str(js),
            "--briefs",
            str(tmp_path / "BRIEFS.md"),
            "--file",
            str(tmp_path / "applications.json"),
        ]
    )


def test_pick_builds_the_ticked_lead(sheet, tmp_path, cv_sandbox, capsys, golden):
    code = run_pick([], sheet, tmp_path)
    golden("pick_ticked", code, *capsys.readouterr())


def test_pick_dry_run(sheet, tmp_path, cv_sandbox, capsys, golden):
    code = run_pick(["--dry-run"], sheet, tmp_path)
    golden("pick_dry_run", code, *capsys.readouterr())


def test_pick_with_no_sheet(tmp_path, cv_sandbox, capsys, golden):
    missing = (tmp_path / "none.md", tmp_path / "none.json")
    code = run_pick([], missing, tmp_path)
    golden("pick_no_sheet", code, *capsys.readouterr())


def run_paste(argv, tmp_path):
    jd = tmp_path / "jd.txt"
    jd.write_text(JD, encoding="utf-8")
    return paste.main(
        [
            "--file",
            str(jd),
            "--company",
            "Acme",
            "--variant",
            "qa_gaming",
            "--briefs",
            str(tmp_path / "BRIEFS.md"),
            *argv,
        ]
    )


def test_paste_builds(tmp_path, cv_sandbox, capsys, golden):
    code = run_paste(["--force"], tmp_path)
    golden("paste_force", code, *capsys.readouterr())


def test_paste_dry_run(tmp_path, cv_sandbox, capsys, golden):
    code = run_paste(["--dry-run"], tmp_path)
    golden("paste_dry_run", code, *capsys.readouterr())


def test_paste_too_short(tmp_path, cv_sandbox, capsys, golden):
    short = tmp_path / "short.txt"
    short.write_text("QA Engineer\n", encoding="utf-8")
    code = paste.main(["--file", str(short), "--company", "Acme"])
    golden("paste_too_short", code, *capsys.readouterr())


BRIEFS = """# Tailored applications

## Roblox — QA Analyst
- **Status:** pending
- **CV:** `qa_gaming` → `cv/out/tailored/roblox-qa/*.pdf`
- **Link:** https://x/1
- **Matched skills:** Python
- **Gaps to expect:** none flagged

## Riot — QA Lead
- **Status:** pending
- **CV:** `qa_gaming` → `cv/out/tailored/riot-qa/*.pdf`
- **Link:** https://x/2
- **Matched skills:** Python
- **Gaps to expect:** none flagged
"""


def run_brief(argv, tmp_path):
    return brief.main(["--briefs", str(tmp_path / "BRIEFS.md"), "--root", str(tmp_path), *argv])


def test_brief_mark_list_close(tmp_path, cv_sandbox, capsys, golden):
    (tmp_path / "BRIEFS.md").write_text(BRIEFS, encoding="utf-8")
    cv = cv_sandbox / "tailored" / "roblox-qa" / "Your_Name_CV.pdf"
    cv.parent.mkdir(parents=True)
    cv.write_bytes(b"%PDF-1.4")
    store = str(tmp_path / "applications.json")

    codes = [
        run_brief(["applied", "1"], tmp_path),
        run_brief(["close", "--file", store], tmp_path),  # refused: #2 still pending
        run_brief(["aborted", "rest", "--reason", "US-only"], tmp_path),
        run_brief(["list"], tmp_path),
        run_brief(["close", "--file", store], tmp_path),
    ]
    out, err = capsys.readouterr()
    golden("brief_day", sum(c << i for i, c in enumerate(codes)), out, err)


def test_scrape_writes_the_sheet(tmp_path, cv_sandbox, monkeypatch, capsys, golden):
    posting = Posting(
        source="remotive",
        company="Roblox",
        title="QA Automation Engineer",
        url="https://x/9",
        location="Remote (worldwide)",
        description="Fully remote worldwide. " + JD,
        posted_at=date.today().isoformat(),
    )
    monkeypatch.setattr(scrape, "collect", lambda *a, **k: ([posting], ["lever:gone: HTTPError"]))
    monkeypatch.setattr(scrape.discover, "load_state", lambda *a, **k: {"boards": {}})
    monkeypatch.setattr(scrape, "_refresh_boards", lambda *a, **k: None)
    real = review.write_sheet
    monkeypatch.setattr(
        scrape.review,
        "write_sheet",
        lambda leads, when: real(leads, when, sheet=tmp_path / "S.md", data=tmp_path / "S.json"),
    )
    code = scrape.main(["--file", str(tmp_path / "applications.json")])
    out, err = capsys.readouterr()
    assert "lead(s) written" in out, "the posting must qualify; widen its description if not"
    golden("scrape_sheet", code, out, err)
```

Note on `brief_day`: the five exit codes are packed into one number so the golden records all
of them.

- [ ] **Step 7: Capture the goldens on the unchanged code**

Run (PowerShell): `$env:UPDATE_GOLDEN=1; pytest tests/test_cli_parity.py; Remove-Item Env:UPDATE_GOLDEN`
Run (bash): `UPDATE_GOLDEN=1 pytest tests/test_cli_parity.py`
Expected: PASS, and `tests/golden/` holds 8 `.txt` files.

Open each file and check it reads like real CLI output: `pick_ticked` has a
`(no PDF - Word unavailable, .docx only)` line and `Next: apply`; `paste_force` has the
Company/Role/CV block; `brief_day` shows the refusal (`1 brief(s) still pending`) and the
`Logged 1 application(s)` line; `scrape_sheet` has the table and `1 source(s) unavailable`.

- [ ] **Step 8: Run the parity tests again without the flag**

Run: `pytest tests/test_cli_parity.py -v`
Expected: 8 PASS.

- [ ] **Step 9: Full suite + lint, then commit**

Run: `pytest; ruff check .; ruff format --check .`
Expected: all pass (511 passed, 2 skipped).

```bash
git add jobs/tailor.py jobs/paste.py jobs/pick.py jobs/scrape.py tests/conftest.py tests/test_paste.py tests/test_cli_parity.py tests/golden
git commit -m "Pin CLI output with golden tests before the core refactor

Also: display_path() for paths outside the repo (pick/scrape crashed on them),
and write_jd resolves its folder at call time so tests can sandbox it.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Progress events and error types

**Files:**
- Create: `jobs/progress.py`, `jobs/errors.py`, `tests/test_progress.py`

**Interfaces:**
- Produces:
  - `Progress(stage: str, message: str, done: int | None = None, total: int | None = None, detail: bool = False)` (frozen dataclass)
  - `Report = Callable[[Progress], None]`
  - `emit(report: Report | None, stage: str, message: str, *, done=None, total=None, detail=False) -> None`
  - `printer() -> Report` — prints the message of each non-detail event to stdout
  - `JobsError(Exception)`; `Skipped(JobsError)` with `.assessment`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_progress.py`:

```python
from __future__ import annotations

from jobs import errors, progress


def test_emit_with_no_reporter_does_nothing():
    progress.emit(None, "fetch", "ignored")  # must not raise


def test_emit_builds_the_event():
    got: list[progress.Progress] = []
    progress.emit(got.append, "fetch", "3/9 remotive", done=3, total=9, detail=True)
    assert got == [progress.Progress("fetch", "3/9 remotive", 3, 9, True)]


def test_printer_prints_only_non_detail_events(capsys):
    report = progress.printer()
    report(progress.Progress("fetch", "Fetching sources…"))
    report(progress.Progress("fetch", "1/9 remotive", 1, 9, detail=True))
    assert capsys.readouterr().out == "Fetching sources…\n"


def test_skipped_is_a_jobs_error_carrying_the_assessment():
    exc = errors.Skipped(assessment="A")
    assert isinstance(exc, errors.JobsError)
    assert exc.assessment == "A"
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_progress.py -v`
Expected: FAIL — `ImportError: cannot import name 'errors'`.

- [ ] **Step 3: Implement**

Create `jobs/progress.py`:

```python
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
```

Create `jobs/errors.py`:

```python
"""Errors a `run`-style function raises instead of printing and exiting.

`main` turns each into the exit code the CLI always used: `JobsError` means there was
nothing to do (exit 1). Bad input stays `ValidationError` / `SettingsError` (exit 2).
"""

from __future__ import annotations


class JobsError(Exception):
    """Nothing to do: no sheet, nothing ticked, briefs still pending."""


class Skipped(JobsError):
    """paste's verdict was skip and the build was not forced."""

    def __init__(self, assessment: object) -> None:
        super().__init__("Skipped - nothing built. Rerun with --force to tailor it anyway.")
        self.assessment = assessment
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_progress.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add jobs/progress.py jobs/errors.py tests/test_progress.py
git commit -m "Add progress events and JobsError for the run/main split

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: `cv/build.py` returns warnings and engine; `tailor.fit` returns `CvResult` and keeps the DOCX

**Files:**
- Modify: `cv/build.py` (`build_variant`, `_report_oversized`, `_word_to_pdf`, `_libreoffice_to_pdf`, `to_pdf`)
- Modify: `jobs/tailor.py` (`CvResult`, `build`, `fit`)
- Modify: `jobs/pick.py`, `jobs/paste.py` (adapt the two `tl.fit` call sites)
- Create: `tests/test_tailor_fit.py`

**Interfaces:**
- Consumes: `progress.emit`, `progress.Report` (Task 2)
- Produces:
  - `cvbuild.build_variant(profile, tag, tailor=None, warnings: list[str] | None = None)`
  - `cvbuild.to_pdf(paths, *, keep_docx=False, pages_out=None, warn=True, warnings: list[str] | None = None, engines_out: dict[Path, str] | None = None) -> list[Path]`
    — when `warnings` is a list, messages are appended to it instead of printed to stderr.
  - `tl.CvResult` (fields below); `tl.fit(t: dict, report: Report | None = None) -> CvResult`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tailor_fit.py`:

```python
"""fit(): the keyword-trimming loop, driven by a fake PDF step so it runs without Word."""

from __future__ import annotations

from pathlib import Path

from jobs import tailor as tl


def tailor(matched: int = 12) -> dict:
    return {
        "company": "Acme",
        "job_title": "QA Engineer",
        "variant": "qa_gaming",
        "matched": [f"k{i}" for i in range(matched)],
        "gaps": [],
        "slug": "acme-qa-engineer",
        "url": "https://x/1",
    }


def fake_pdf(monkeypatch, pages_by_keywords, engine="word"):
    """to_pdf stand-in: page count depends on how many keywords the CV was built with."""
    calls: list[int] = []

    def to_pdf(
        paths, *, keep_docx=False, pages_out=None, warn=True, warnings=None, engines_out=None
    ):
        assert keep_docx, "tailored CVs must keep their .docx"
        made = []
        for src in paths:
            pdf = src.with_suffix(".pdf")
            pdf.write_bytes(b"%PDF")
            n = calls[-1]
            if pages_out is not None and pages_by_keywords(n) is not None:
                pages_out[pdf] = pages_by_keywords(n)
            if engines_out is not None:
                engines_out[pdf] = engine
            made.append(pdf)
        return made

    real_build = tl.cvbuild.build_variant

    def build_variant(profile, tag, tailor=None, warnings=None):
        calls.append(len(tailor["matched"]))
        return real_build(profile, tag, tailor=tailor, warnings=warnings)

    monkeypatch.setattr(tl.cvbuild, "to_pdf", to_pdf)
    monkeypatch.setattr(tl.cvbuild, "build_variant", build_variant)
    return calls


def test_fit_keeps_every_keyword_when_it_fits(cv_sandbox, monkeypatch):
    fake_pdf(monkeypatch, lambda n: 2)
    r = tl.fit(tailor(12))
    assert (r.keywords_wanted, r.keywords_kept, r.pages, r.engine) == (12, 12, 2, "word")
    assert r.docx.suffix == ".docx" and r.docx.exists()
    assert r.pdf.suffix == ".pdf" and r.pdf.exists()
    assert r.error == ""


def test_fit_trims_until_two_pages_and_reports_each_rebuild(cv_sandbox, monkeypatch):
    calls = fake_pdf(monkeypatch, lambda n: 3 if n > 6 else 2)
    events = []
    r = tl.fit(tailor(12), report=events.append)
    assert calls == [12, 9, 6]
    assert (r.keywords_kept, r.pages) == (6, 2)
    assert [e.detail for e in events] == [True, True]
    assert events[0].message == "trimmed to 9 keywords, rebuilding"


def test_fit_drops_to_zero_keywords_on_a_variant_with_no_slack(cv_sandbox, monkeypatch):
    fake_pdf(monkeypatch, lambda n: 3 if n else 2)
    r = tl.fit(tailor(5))
    assert (r.keywords_kept, r.pages) == (0, 2)


def test_fit_without_a_pdf_engine_keeps_the_docx_and_leaves_pages_unchecked(cv_sandbox):
    r = tl.fit(tailor(4))  # cv_sandbox reports Word and LibreOffice missing
    assert r.docx.exists() and r.pdf is None
    assert (r.engine, r.pages, r.keywords_kept) == (None, None, 4)


def test_fit_stops_when_the_page_count_is_undetectable(cv_sandbox, monkeypatch):
    calls = fake_pdf(monkeypatch, lambda n: None, engine="libreoffice")
    r = tl.fit(tailor(12))
    assert calls == [12]
    assert (r.engine, r.pages, r.pdf is not None) == ("libreoffice", None, True)


def test_build_variant_collects_its_warning_instead_of_printing(cv_sandbox, capsys):
    profile = tl._profile()
    role = next(r for r in profile["roles"] if r["id"] == "current_role")  # qa_gaming lists it
    role["bullets"] = [b for b in role["bullets"] if "qa_gaming" not in b["tags"]]
    warnings: list[str] = []
    tl.cvbuild.build_variant(profile, "qa_gaming", warnings=warnings)
    assert capsys.readouterr().err == ""
    assert any("current_role" in w and "MISSING from the timeline" in w for w in warnings)


def test_to_pdf_reports_the_missing_engines_into_the_list(cv_sandbox, tmp_path, capsys):
    docx = tmp_path / "a.docx"
    docx.write_bytes(b"PK")
    warnings: list[str] = []
    assert tl.cvbuild.to_pdf([docx], warnings=warnings) == []
    assert capsys.readouterr().err == ""
    assert warnings == ["! neither Word nor LibreOffice is available - skipping PDF step"]
```

(`cv/profile.example.json`: bullets are `{"text", "tags"}`; `qa_gaming` lists roles
`current_role` and `previous_role`.)

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_tailor_fit.py -v`
Expected: FAIL — `fit()` returns a tuple, `build_variant()` has no `warnings` parameter.

- [ ] **Step 3: Implement `cv/build.py`**

Add after `_WD_STATISTIC_PAGES = 2`:

```python
def _warn(message: str, warnings: list[str] | None) -> None:
    """Print to stderr as before, or collect for a caller that reports it itself."""
    if warnings is None:
        print(message, file=sys.stderr)
    else:
        warnings.append(message.strip())
```

`build_variant`: add `warnings: list[str] | None = None` as its last parameter and replace the
`print(... MISSING from the timeline ...)` call with:

```python
            _warn(
                f"  ! {tag}: role '{entry['id']}' has no bullets tagged '{tag}' - "
                f"it will be MISSING from the timeline",
                warnings,
            )
```

`_report_oversized(oversized, warn, warnings=None)`: replace each of its three `print(..., file=sys.stderr)`
calls with `_warn(<same f-string>, warnings)`.

`_word_to_pdf`: add `warnings: list[str] | None = None, engines_out: dict[Path, str] | None = None`
to its keyword parameters. Replace the body from `made: list[Path] = []` to the end with:

```python
    import threading

    made: list[Path] = []
    oversized: list[tuple[Path, int]] = []
    # COM must be initialised per thread. The CLI runs on the main thread, where pywin32
    # already did it; a UI worker thread is not, and Word fails there without this.
    off_main = threading.current_thread() is not threading.main_thread()
    if off_main:
        pythoncom.CoInitialize()
    word = None
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        for src in paths:
            dst = src.with_suffix(".pdf")
            doc = word.Documents.Open(str(src), ReadOnly=True, AddToRecentFiles=False)
            try:
                doc.SaveAs2(str(dst), FileFormat=17)  # 17 = wdFormatPDF
                made.append(dst)
                if engines_out is not None:
                    engines_out[dst] = "word"
                pages = int(doc.ComputeStatistics(_WD_STATISTIC_PAGES))
                if pages_out is not None:
                    pages_out[dst] = pages
                if pages > MAX_PAGES:
                    oversized.append((dst, pages))
            finally:
                doc.Close(SaveChanges=0)
            if not keep_docx:
                with contextlib.suppress(OSError):
                    src.unlink()
    except Exception as exc:  # noqa: BLE001
        if warn:
            _warn(f"  ! Word PDF conversion failed: {exc}", warnings)
    finally:
        if word is not None:
            with contextlib.suppress(Exception):
                word.Quit()
        if off_main:
            pythoncom.CoUninitialize()
    _report_oversized(oversized, warn, warnings)
    return made
```

Change the import at the top of `_word_to_pdf` from `import pythoncom  # noqa: F401` to
`import pythoncom`.

`_libreoffice_to_pdf`: add the same two keyword parameters. Replace each `print(..., file=sys.stderr)`
with `_warn(<same f-string>, warnings)`, add `if engines_out is not None: engines_out[dst] = "libreoffice"`
right after `made.append(dst)`, and pass `warnings` to `_report_oversized`.

`to_pdf`: add `warnings: list[str] | None = None, engines_out: dict[Path, str] | None = None`,
pass both through to `_word_to_pdf` and `_libreoffice_to_pdf`, and replace its own `print` with:

```python
            _warn("  ! neither Word nor LibreOffice is available - skipping PDF step", warnings)
```

Add to the `to_pdf` docstring: "`warnings`, if given, collects every message instead of
printing it. `engines_out` is filled with {pdf path: "word" | "libreoffice"}."

- [ ] **Step 4: Implement `jobs/tailor.py`**

Add imports: `from dataclasses import dataclass, field` and
`from jobs.progress import Report, emit` (after `from jobs import settings`).

Replace `build()` and `fit()` with:

```python
@dataclass
class CvResult:
    """One tailored CV, as built. The DOCX is always kept; the PDF is an extra."""

    company: str
    variant: str
    docx: Path | None = None
    pdf: Path | None = None
    engine: str | None = None  # "word" | "libreoffice" | None when no PDF was made
    pages: int | None = None  # None = not checked (no engine, or undetectable)
    keywords_wanted: int = 0
    keywords_kept: int = 0
    warnings: list[str] = field(default_factory=list)
    error: str = ""  # set by callers that catch a failed build


def build(
    tailors: list[dict],
    *,
    pdf: bool = True,
    pages_out: dict[Path, int] | None = None,
    warn: bool = True,
) -> list[Path]:
    """Generate one tailored CV per posting, keeping each .docx beside its PDF.

    Returns the PDFs, or the .docx files when pdf=False. Used by the one-shot
    `jobs.scrape --cv`; pick and paste go through fit().
    """
    profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    made: list[Path] = []
    for t in tailors:
        if not t["variant"]:
            continue
        path, _ = cvbuild.build_variant(profile, t["variant"], tailor=t)
        made.append(path)
    if pdf and made:
        return cvbuild.to_pdf(made, keep_docx=True, pages_out=pages_out, warn=warn)
    return made


def fit(t: dict, report: Report | None = None) -> CvResult:
    """Build one tailored CV, dropping keywords until it obeys the two-page rule.

    The "Most Relevant to <company>" block is the only part of a tailored CV whose
    length varies with the posting, so it is the only part worth trimming. Keywords are
    dropped from the end - `analyse()` yields them in whitelist order, roughly
    strongest-first.

    The last attempt is always an empty list, which makes build_variant drop the block
    header as well: on a variant with no slack, two keywords still cost the two lines
    that spill the page, so trimming that stops short of empty never converges.

    Without a page count - no PDF engine, or LibreOffice's count undetectable - there is
    nothing to trim against, so the first build is the result and `pages` stays None.
    `keywords_kept` describes the file on disk, because the brief is written from `t`.
    """
    result = CvResult(t["company"], t["variant"], keywords_wanted=len(t["matched"]))
    if not t["variant"]:
        return result
    profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    while True:
        pages: dict[Path, int] = {}
        engines: dict[Path, str] = {}
        warnings: list[str] = []
        docx, _ = cvbuild.build_variant(profile, t["variant"], tailor=t, warnings=warnings)
        made = cvbuild.to_pdf(
            [docx], keep_docx=True, pages_out=pages, engines_out=engines, warnings=warnings
        )
        result.docx = docx
        result.pdf = made[0] if made else None
        result.engine = engines.get(result.pdf) if result.pdf else None
        result.pages = pages.get(result.pdf) if result.pdf else None
        result.keywords_kept = len(t["matched"])
        result.warnings = warnings
        if result.pages is None or result.pages <= MAX_PAGES or not t["matched"]:
            return result
        t["matched"] = shrink(t["matched"])
        emit(
            report,
            "tailor",
            f"trimmed to {len(t['matched'])} keywords, rebuilding",
            detail=True,
        )
```

Update the `shrink` docstring's "fit() cannot run without Word" to "fit() needs a PDF engine
to measure pages".

- [ ] **Step 5: Adapt the two `fit` call sites (behaviour unchanged)**

In `jobs/pick.py`, replace

```python
            made, pages, kept = tl.fit(t)
```

with

```python
            r = tl.fit(t)
            made, pages, kept = ([r.pdf] if r.pdf else []), r.pages or 0, r.keywords_kept
```

In `jobs/paste.py`, make the same replacement for `made, pages, kept = tl.fit(t)`.

- [ ] **Step 6: Run to verify it passes**

Run: `pytest tests/test_tailor_fit.py tests/test_cli_parity.py tests/test_cv_init.py -v`
Expected: all PASS. The parity goldens are unchanged: the terminal never showed the DOCX.

- [ ] **Step 7: Full suite + lint, then commit**

Run: `pytest; ruff check .; ruff format --check .`

```bash
git add cv/build.py jobs/tailor.py jobs/pick.py jobs/paste.py tests/test_tailor_fit.py
git commit -m "fit() returns a CvResult and keeps the DOCX; build.py collects warnings

to_pdf reports which engine made each PDF and initialises COM off the main
thread, so Word works from a UI worker.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: `brief` files the DOCX as well as the PDF

**Files:**
- Modify: `jobs/brief.py` (`_cv_for` → `_cvs_for`, `Filed.cv` → `Filed.cvs`, `file_brief`, `sweep_tailored`, `QUESTIONS_TEMPLATE`)
- Modify: `tests/test_brief.py`

**Interfaces:**
- Produces: `brief.CV_SUFFIXES = (".pdf", ".docx")`; `brief._cvs_for(slug: str, tailored: Path) -> list[Path]`;
  `Filed.cvs: list[Path]` (replaces `Filed.cv`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_brief.py`:

```python
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
```

(`make_cv` writes `%PDF-1.4` bytes whatever the name; that is fine for these tests.)

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_brief.py -k "docx" -v`
Expected: FAIL — the DOCX is not filed and the log reports "no tailored CV found".

- [ ] **Step 3: Implement**

In `jobs/brief.py`, replace `_cv_for` with:

```python
CV_SUFFIXES = (".pdf", ".docx")


def _cvs_for(slug: str, tailored: Path) -> list[Path]:
    """Every generated CV file for one posting: `cv/out/tailored/<slug>/<name>.{pdf,docx}`.

    The DOCX is always built and the PDF only when Word or LibreOffice is installed, so
    either may be alone. Falls back to the old flat `*__<slug>.pdf`, so a CV built before
    the layout changed is still found and filed rather than reported missing.
    """
    folder = tailored / slug
    found = sorted(p for p in folder.glob("*") if p.suffix in CV_SUFFIXES)
    if found:
        return found
    return sorted(p for s in CV_SUFFIXES for p in tailored.glob(f"*__{slug}{s}"))


def _cvs_in(folder: Path) -> list[Path]:
    return sorted(p for p in folder.glob("*") if p.suffix in CV_SUFFIXES)
```

In `Filed`, replace `cv: Path | None = None` with `cvs: list[Path] = field(default_factory=list)`.

In `file_brief`, replace from `source_cv = _cv_for(...)` down to (not including)
`source_jd = ...` with:

```python
    sources = _cvs_for(b.folder, TAILORED_DIR)
    if not sources and not _cvs_in(dest):
        filed.problems.append(f"no tailored CV found in cv/out/tailored/{b.folder}/")
    if dry_run:
        return filed

    dest.mkdir(parents=True, exist_ok=True)
    if sources:
        for source in sources:
            target = dest / source.name
            if not target.exists():
                shutil.copy2(source, target)
            if target.exists():
                filed.cvs.append(target)
    else:
        # A CV put here by hand counts; not every variant comes from the tailored build.
        filed.cvs = _cvs_in(dest)
```

In the `QUESTIONS_TEMPLATE`, change the CV line to:

```
- **CV attached:** {cv} (variant `{variant}`)
```

and in `file_brief`'s `.format(...)` call replace `cv=filed.cv.name if filed.cv else "not found",` with:

```python
cv = (", ".join(f"`{p.name}`" for p in filed.cvs) or "not found",)
```

Replace `sweep_tailored`'s loop bodies with:

```python
    removed: list[Path] = []
    for f in filed:
        source_jd = TAILORED_DIR / f.brief.folder / "jd.md"
        if f.jd is not None and f.jd.exists():
            source_jd.unlink(missing_ok=True)
        sent = {p.name for p in f.cvs if p.exists()}
        for source in _cvs_for(f.brief.folder, TAILORED_DIR):
            if source.name not in sent or f.folder / source.name == source:
                continue
            source.unlink(missing_ok=True)
            removed.append(source)
            _prune(source.parent)
    for b in aborted:
        (TAILORED_DIR / b.folder / "jd.md").unlink(missing_ok=True)
        for source in _cvs_for(b.folder, TAILORED_DIR):
            source.unlink(missing_ok=True)
            removed.append(source)
            _prune(source.parent)
    return removed
```

Update the `sweep_tailored` docstring's "`*__<slug>.pdf` name" to "`*__<slug>.pdf` name, and
both the PDF and the DOCX".

Search the tests for the old attribute and update any hit: `rg "\.cv\b|_cv_for" tests jobs`.

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_brief.py tests/test_cli_parity.py -v`
Expected: all PASS, goldens unchanged (`close` output does not name CV files).

- [ ] **Step 5: Full suite + lint, then commit**

Run: `pytest; ruff check .; ruff format --check .`

```bash
git add jobs/brief.py tests/test_brief.py
git commit -m "brief close files the DOCX as well as the PDF

Without Word or LibreOffice the DOCX is the only CV; close reported it
missing and filed nothing.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: `brief.mark_briefs()` and `brief.close()`

`mark` is already the name of the pure line-rewriting helper, so the new function is
`mark_briefs` (the spec's `mark(...)`).

**Files:**
- Modify: `jobs/brief.py` (`MarkResult`, `CloseResult`, `StillPending`, `mark_briefs`, `close`, `cmd_mark`, `cmd_close`)
- Modify: `tests/test_brief.py`

**Interfaces:**
- Consumes: `errors.JobsError` (Task 2); `Filed.cvs` (Task 4)
- Produces:
  - `MarkResult(changed: list[Brief], pending: int)`
  - `mark_briefs(path: Path, selectors: list[str], state: str, reason: str = "") -> MarkResult`
  - `CloseResult(filed: list[Filed], dropped: list[tuple[Brief, int]], swept: list[Path], log_path: Path, dry_run: bool)`
  - `StillPending(JobsError)` with `.briefs: list[Brief]`
  - `close(path: Path, root: Path, store: Store, *, force=False, keep_cvs=False, dry_run=False) -> CloseResult`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_brief.py`:

```python
from jobs.errors import JobsError  # noqa: E402


def test_mark_briefs_returns_what_changed(briefs_file):
    result = brief.mark_briefs(briefs_file, ["1"], brief.APPLIED)
    assert [b.company for b in result.changed] == ["Anthropic"]
    assert result.pending == 1
    assert brief.load(briefs_file)[1][0].state == brief.APPLIED


def test_mark_briefs_on_an_empty_file_is_nothing_to_do(tmp_path):
    with pytest.raises(JobsError):
        brief.mark_briefs(tmp_path / "missing.md", ["1"], brief.APPLIED)


def test_close_raises_still_pending_with_the_unmarked(briefs_file, tmp_path, tracked):
    brief.mark_briefs(briefs_file, ["1"], brief.APPLIED)
    with pytest.raises(brief.StillPending) as caught:
        brief.close(briefs_file, tmp_path, tracked)
    assert [b.company for b in caught.value.briefs] == ["prolific"]


def test_close_with_nothing_marked_is_nothing_to_do(briefs_file, tmp_path, tracked):
    with pytest.raises(JobsError):
        brief.close(briefs_file, tmp_path, tracked)


def test_close_returns_what_it_filed(briefs_file, tmp_path, tracked, isolated_tailored):
    make_cv(isolated_tailored, "anthropic-red-team-engineer-safeguards")
    brief.mark_briefs(briefs_file, ["1"], brief.APPLIED)
    brief.mark_briefs(briefs_file, ["2"], brief.ABORTED)

    result = brief.close(briefs_file, tmp_path, tracked)

    assert [f.app_id for f in result.filed] == [1]
    assert [(b.company, app_id) for b, app_id in result.dropped] == [("prolific", 2)]
    assert len(result.swept) == 1
    assert result.log_path == tmp_path / brief.FOLDER / "LOG.md"
    assert result.dry_run is False
    assert brief.load(briefs_file)[1] == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_brief.py -k "mark_briefs or still_pending or nothing_to_do or returns_what" -v`
Expected: FAIL — `AttributeError: module 'jobs.brief' has no attribute 'mark_briefs'`.

- [ ] **Step 3: Implement**

In `jobs/brief.py` add `from jobs.errors import JobsError  # noqa: E402` to the imports.

Add under the `# -- marking` section, after `select`:

```python
@dataclass
class MarkResult:
    changed: list[Brief]
    pending: int  # still pending after this mark


def mark_briefs(path: Path, selectors: list[str], state: str, reason: str = "") -> MarkResult:
    """Mark the selected briefs `state` in BRIEFS.md. Bad selectors raise ValidationError."""
    lines, briefs = load(path)
    if not briefs:
        raise JobsError(f"No briefs in {path}.")
    chosen = select(briefs, selectors)
    path.write_text("\n".join(mark(lines, chosen, state, reason)) + "\n", encoding="utf-8")
    left = sum(1 for b in briefs if b.state == PENDING and b not in chosen)
    return MarkResult(chosen, left)
```

Add before `# -- commands`:

```python
class StillPending(JobsError):
    """close refused: some briefs are neither applied nor aborted."""

    def __init__(self, briefs: list[Brief]) -> None:
        super().__init__(
            f"{len(briefs)} brief(s) still pending. Mark them, or pass --force to "
            "close anyway (pending briefs are discarded)."
        )
        self.briefs = briefs


@dataclass
class CloseResult:
    filed: list[Filed]
    dropped: list[tuple[Brief, int]]
    swept: list[Path]
    log_path: Path
    dry_run: bool


def close(
    path: Path,
    root: Path,
    store: Store,
    *,
    force: bool = False,
    keep_cvs: bool = False,
    dry_run: bool = False,
) -> CloseResult:
    """File every applied brief, withdraw every aborted one, log the day, empty BRIEFS.md.

    Every mutation happens here, before the caller reports anything: console encoding
    varies (a legacy Windows codepage raises on the em dash in a posting title), and a
    crash while reporting must never leave the store unsaved on top of folders written.
    """
    _, briefs = load(path)
    applied = [b for b in briefs if b.state == APPLIED]
    aborted = [b for b in briefs if b.state == ABORTED]
    still_pending = [b for b in briefs if b.state not in (APPLIED, ABORTED)]
    if not applied and not aborted:
        raise JobsError("Nothing marked applied or aborted yet.")
    if still_pending and not force:
        raise StillPending(still_pending)

    when = today()
    log = root / FOLDER / "LOG.md"
    filed = [file_brief(b, store, root, dry_run=dry_run) for b in applied]
    dropped = [(b, abandon(b, store, dry_run=dry_run)) for b in aborted]
    swept: list[Path] = []
    if not dry_run:
        store.save()
        write_log(log_entry(filed, aborted, when), log, when)
        # Sweep before emptying: the briefs are what say which CVs these were.
        if not keep_cvs:
            swept = sweep_tailored(filed, aborted)
        empty_briefs(path)
    return CloseResult(filed, dropped, swept, log, dry_run)
```

Replace `cmd_mark` with:

```python
def cmd_mark(args: argparse.Namespace, briefs: list[Brief]) -> int:
    try:
        result = mark_briefs(args.briefs, args.selectors, args.state, args.reason)
    except JobsError as exc:
        print(exc, file=sys.stderr)
        return 1
    for b in result.changed:
        print(f"{args.state}: {b.label}" + (f" ({args.reason})" if args.reason else ""))
    print(
        f"{result.pending} still pending."
        if result.pending
        else "All marked. Next: python -m jobs.brief close"
    )
    return 0
```

Replace `cmd_close` with:

```python
def cmd_close(args: argparse.Namespace, briefs: list[Brief]) -> int:
    store = Store(args.file or default_path())
    try:
        result = close(
            args.briefs,
            args.root,
            store,
            force=args.force,
            keep_cvs=args.keep_cvs,
            dry_run=args.dry_run,
        )
    except StillPending as exc:
        for b in exc.briefs:
            print(f"  unmarked: {b.label}", file=sys.stderr)
        print(exc, file=sys.stderr)
        return 1
    except JobsError as exc:
        print(exc, file=sys.stderr)
        return 1

    for f in result.filed:
        print(f"{FOLDER}/{f.brief.folder}/  <- jobtrack #{f.app_id}, CV {f.brief.variant}")
        if f.kept_answers:
            print("    answers already on file, kept as they are")
        for problem in f.problems:
            print(f"    !  {problem}")
    for b, app_id in result.dropped:
        print(f"not pursued: {b.label} (jobtrack #{app_id} -> withdrawn)")

    if result.dry_run:
        print("\n--dry-run: nothing written.")
        return 0
    print(
        f"\nLogged {len(result.filed)} application(s) to {result.log_path.relative_to(args.root)}"
    )
    if result.swept:
        print(f"Cleared {len(result.swept)} tailored CV(s); the ones you sent are in {FOLDER}/.")
    print(f"{args.briefs.name} is empty and ready for tomorrow's scrape.")
    return 0
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_brief.py tests/test_cli_parity.py -v`
Expected: all PASS; `brief_day` golden unchanged.

- [ ] **Step 5: Full suite + lint, then commit**

Run: `pytest; ruff check .; ruff format --check .`

```bash
git add jobs/brief.py tests/test_brief.py
git commit -m "brief: mark_briefs() and close() return results; main only prints

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: `paste.build()` split from `main`

**Files:**
- Modify: `jobs/paste.py` (`assess(report=)`, `PasteResult`, `build`, `main`)
- Modify: `tests/test_paste.py`

**Interfaces:**
- Consumes: `tl.fit -> CvResult` (Task 3); `errors.Skipped` (Task 2); `progress.emit`
- Produces:
  - `assess(text, *, company, title, url="", location="", variant="", use_llm=True, cfg=None, report=None) -> Assessment`
  - `PasteResult(assessment: Assessment, cv: tl.CvResult, forced: bool, app_id: int | None, briefs_path: Path)`
  - `build(a: Assessment, text: str, *, force: bool = False, save: bool = False, store: Store | None = None, briefs: Path = BRIEFS_PATH, report: Report | None = None) -> PasteResult` — raises `Skipped` when the verdict is skip and `force` is false.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_paste.py`:

```python
from jobs import paste  # noqa: E402
from jobs.errors import Skipped  # noqa: E402
from jobtrack.storage import Store  # noqa: E402

LONG_JD = JD + "\nFully remote, worldwide. " * 3


def test_build_writes_the_cv_brief_and_jd(tmp_path, cv_sandbox):
    a = paste.assess(
        LONG_JD,
        company="Acme",
        title="Senior QA Automation Engineer",
        variant="qa_gaming",
        use_llm=False,
    )
    events = []
    result = paste.build(a, LONG_JD, force=True, briefs=tmp_path / "B.md", report=events.append)
    assert result.cv.docx.exists()
    assert result.cv.pdf is None and result.cv.pages is None
    assert "## Acme" in (tmp_path / "B.md").read_text(encoding="utf-8")
    assert (cv_sandbox / "tailored" / a.tailor["slug"] / "jd.md").exists()
    assert result.app_id is None
    assert [e.stage for e in events] == ["tailor"]


def test_build_refuses_a_skip_unless_forced(tmp_path, cv_sandbox):
    a = paste.assess(
        LONG_JD,
        company="Acme",
        title="Senior QA Automation Engineer",
        variant="qa_gaming",
        use_llm=False,
    )
    a.rules = paste.llm.Verdict("skip", blockers=["US only"])
    with pytest.raises(Skipped) as caught:
        paste.build(a, LONG_JD, briefs=tmp_path / "B.md")
    assert caught.value.assessment is a
    assert not (tmp_path / "B.md").exists()
    assert paste.build(a, LONG_JD, force=True, briefs=tmp_path / "B.md").forced is True


def test_build_can_save_to_jobtrack(tmp_path, cv_sandbox):
    a = paste.assess(
        LONG_JD, company="Acme", title="QA Engineer", variant="qa_gaming", use_llm=False
    )
    store = Store(tmp_path / "applications.json").load()
    result = paste.build(a, LONG_JD, force=True, save=True, store=store, briefs=tmp_path / "B.md")
    saved = Store(tmp_path / "applications.json").load().get(result.app_id)
    assert saved.status == "wishlist" and saved.company == "Acme"
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_paste.py -k "build_" -v`
Expected: FAIL — `AttributeError: module 'jobs.paste' has no attribute 'build'`.

- [ ] **Step 3: Implement**

In `jobs/paste.py` add imports: `from jobs.errors import Skipped  # noqa: E402` and
`from jobs.progress import Report, emit  # noqa: E402`.

`assess`: add `report: Report | None = None` as the last keyword parameter, and just before
`a.conversation = llm.Conversation(client, opening)` add:

```python
    emit(report, "llm", f"Asking {client.provider} for a verdict…", detail=True)
```

(If `llm.Client` names the attribute differently, use the field that holds the provider name.)

Add after `assess`:

```python
@dataclass
class PasteResult:
    assessment: Assessment
    cv: tl.CvResult
    forced: bool
    app_id: int | None
    briefs_path: Path


def build(
    a: Assessment,
    text: str,
    *,
    force: bool = False,
    save: bool = False,
    store: Store | None = None,
    briefs: Path = BRIEFS_PATH,
    report: Report | None = None,
) -> PasteResult:
    """Tailor the CV, append the brief, save the JD, and optionally track it.

    A skip verdict raises Skipped unless `force`: you found the posting, the verdict
    advises, and forcing is the explicit override.
    """
    forced = a.verdict.decision == "skip"
    if forced and not force:
        raise Skipped(a)
    t = a.tailor
    emit(report, "tailor", f"Tailoring the {t['variant']} CV for {t['company']}…")
    cv = tl.fit(t, report=report)
    tl.append_briefs([t], briefs)
    tl.write_jd(t["company"], t["job_title"], text, t["slug"])

    app_id = None
    if save:
        store = store or Store(default_path())
        verdict = a.verdict
        app = Application(
            id=store.next_id(),
            company=t["company"],
            role=t["job_title"],
            status="wishlist",
            url=a.posting.url,
            location=a.posting.location or "Remote",
            notes=[
                f"pasted by hand | cv: {t['variant']}",
                f"verdict: {verdict.decision} ({verdict.source})"
                + (" - tailored anyway" if forced else ""),
                "source: paste",
            ],
        )
        store.add(app)
        store.save()
        app_id = app.id
    return PasteResult(a, cv, forced, app_id, briefs)
```

In `main`, replace everything from `forced = a.verdict.decision == "skip"` to the final
`return 0` with:

```python
    confirmed = (
        a.verdict.decision == "skip"
        and not args.force
        and interactive
        and _confirm("\n  Verdict is skip. Tailor a CV anyway? [y/N] ")
    )
    wanted = len(t["matched"])
    try:
        result = build(
            a,
            text,
            force=args.force or confirmed,
            save=args.save,
            store=Store(args.store_file or default_path()) if args.save else None,
            briefs=args.briefs,
        )
    except Skipped as exc:
        print(f"\n  {exc}")
        return 1

    cv = result.cv
    kept = cv.keywords_kept
    if kept < wanted:
        detail = (
            f"kept the strongest {kept}"
            if kept
            else f"the {t['variant']} variant has no room for the block at all, so it "
            "was left off - the CV is untailored apart from the folder it lands in"
        )
        print(f"\n  note: {wanted} keywords ran past {tl.MAX_PAGES} pages; {detail}.")
    pages = cv.pages or 0
    if cv.pdf:
        print(f"\n  CV      -> {tl.display_path(cv.pdf)}" + (f"  ({pages} pages)" if pages else ""))
    if pages > tl.MAX_PAGES:
        print(
            f"  !! still {pages} pages with no keyword block left to trim - "
            "check cv/profile.json before sending.",
            file=sys.stderr,
        )
    if not cv.pdf:
        print("  !  no PDF produced (Word unavailable); the .docx is in cv/out/tailored/")
    print(f"  Brief   -> appended to {tl.display_path(args.briefs)}")
    if result.app_id is not None:
        print(f"  Tracked -> jobtrack #{result.app_id} (wishlist)")

    print(f'\nNext: apply, then  python -m jobs.brief applied "{company}"')
    return 0
```

Note: `build()` uses `a.posting.url` and `a.posting.location`, which `assess` filled from
`args.url` / `args.location` — the same values `main` used before.

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_paste.py tests/test_cli_parity.py -v`
Expected: all PASS; `paste_force`, `paste_dry_run`, `paste_too_short` goldens unchanged.

- [ ] **Step 5: Full suite + lint, then commit**

Run: `pytest; ruff check .; ruff format --check .`

```bash
git add jobs/paste.py tests/test_paste.py
git commit -m "paste: build() split from main; a skip raises Skipped

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: `pick.choose()` and `pick.run()`, surviving one failing CV

**Files:**
- Modify: `jobs/pick.py`
- Create: `tests/test_pick.py`

**Interfaces:**
- Consumes: `tl.fit -> CvResult`; `errors.JobsError`; `progress.emit`, `progress.printer`
- Produces:
  - `choose(leads: list[review.Lead], selectors: list[str], sheet_name: str) -> tuple[list[Lead], list[Lead]]` — raises `JobsError` when nothing is ticked or dropped.
  - `PickResult(taken: list[Lead], dropped: list[Lead], cvs: list[tl.CvResult], briefs_path: Path)`
  - `cv_line(t: dict, cv: tl.CvResult) -> str` — the per-CV terminal line
  - `run(taken, dropped, *, briefs: Path, store: Store, report: Report | None = None) -> PickResult`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_pick.py`:

```python
"""pick.run(): jobtrack entries, one CV per taken lead, briefs - and one bad CV sinks nothing."""

from __future__ import annotations

import pytest
from jobs import pick, review
from jobs import tailor as tl
from jobs.errors import JobsError

from jobtrack.storage import Store


def make_lead(i: int, company: str, mark: str) -> review.Lead:
    t = {
        "company": company,
        "job_title": "QA Analyst",
        "variant": "qa_gaming",
        "matched": [],
        "gaps": [],
        "slug": f"{company.lower()}-qa-analyst",
        "url": f"https://x/{i}",
    }
    data = {"tailor": t, "location": "Remote", "score": 100, "reasons": [], "description": "jd"}
    return review.Lead(index=i, label=f"{company} — QA Analyst", url=t["url"], mark=mark, data=data)


def test_choose_takes_ticked_and_drops_skipped():
    leads = [
        make_lead(1, "A", review.TAKE),
        make_lead(2, "B", review.SKIP),
        make_lead(3, "C", review.PENDING),
    ]
    taken, dropped = pick.choose(leads, [], "S.md")
    assert [x.company for x in taken] == ["A"] and [x.company for x in dropped] == ["B"]


def test_choose_with_nothing_marked_is_nothing_to_do():
    with pytest.raises(JobsError):
        pick.choose([make_lead(1, "A", review.PENDING)], [], "S.md")


def test_run_builds_records_and_reports(tmp_path, cv_sandbox):
    store = Store(tmp_path / "applications.json").load()
    events = []
    result = pick.run(
        [make_lead(1, "A", review.TAKE)],
        [make_lead(2, "B", review.SKIP)],
        briefs=tmp_path / "B.md",
        store=store,
        report=events.append,
    )
    assert [cv.error for cv in result.cvs] == [""]
    assert result.cvs[0].docx.exists()
    statuses = sorted(a.status for a in Store(tmp_path / "applications.json").load())
    assert statuses == ["wishlist", "withdrawn"]
    non_detail = [e.message for e in events if not e.detail]
    assert non_detail[0] == "\nTailoring 1 CV(s)…"
    assert non_detail[1].startswith("  · A")


def test_one_failing_cv_does_not_lose_the_others_briefs(tmp_path, cv_sandbox, monkeypatch):
    real_fit = tl.fit

    def flaky(t, report=None):
        if t["company"] == "B":
            raise RuntimeError("Word crashed")
        return real_fit(t, report=report)

    monkeypatch.setattr(pick.tl, "fit", flaky)
    store = Store(tmp_path / "applications.json").load()
    result = pick.run(
        [
            make_lead(1, "A", review.TAKE),
            make_lead(2, "B", review.TAKE),
            make_lead(3, "C", review.TAKE),
        ],
        [],
        briefs=tmp_path / "B.md",
        store=store,
    )
    assert [cv.error for cv in result.cvs] == ["", "Word crashed", ""]
    text = (tmp_path / "B.md").read_text(encoding="utf-8")
    assert "## A —" in text and "## C —" in text and "## B —" not in text
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_pick.py -v`
Expected: FAIL — `AttributeError: module 'jobs.pick' has no attribute 'choose'`.

- [ ] **Step 3: Implement**

In `jobs/pick.py` add imports:

```python
from dataclasses import dataclass  # with the stdlib imports

from jobs.errors import JobsError  # noqa: E402
from jobs.progress import Report, emit, printer  # noqa: E402
```

Add after `cmd_list`:

```python
def choose(
    leads: list[review.Lead], selectors: list[str], sheet_name: str
) -> tuple[list[review.Lead], list[review.Lead]]:
    """(taken, dropped): the selectors if given, else the `[x]` ticks; `[-]` is dropped."""
    taken = (
        review.select(leads, selectors)
        if selectors
        else [lead for lead in leads if lead.mark == review.TAKE]
    )
    dropped = [lead for lead in leads if lead.mark == review.SKIP]
    if not taken and not dropped:
        raise JobsError(
            f"Nothing ticked on {sheet_name}. Mark a heading `[x]`, "
            "or pass lead numbers: python -m jobs.pick 1 4 9"
        )
    return taken, dropped


@dataclass
class PickResult:
    taken: list[review.Lead]
    dropped: list[review.Lead]
    cvs: list[tl.CvResult]
    briefs_path: Path


def cv_line(t: dict, cv: tl.CvResult) -> str:
    """The terminal line for one built CV - also the UI's progress message."""
    note = ""
    if cv.error:
        note = f"  !! failed: {cv.error}"
    elif not cv.pdf:
        note = "  (no PDF - Word unavailable, .docx only)"
    elif (cv.pages or 0) > tl.MAX_PAGES:
        note = f"  !! {cv.pages} pages - check before sending"
    elif cv.keywords_kept < cv.keywords_wanted:
        note = (
            f"  (trimmed {cv.keywords_wanted}->{cv.keywords_kept} keywords "
            f"to hold {tl.MAX_PAGES} pages)"
        )
    return f"  · {t['company'][:26]:<26} {t['variant']:<18}{note}"


def run(
    taken: list[review.Lead],
    dropped: list[review.Lead],
    *,
    briefs: Path,
    store: Store,
    report: Report | None = None,
) -> PickResult:
    """Record the leads in jobtrack, build one CV per taken lead, append their briefs.

    A CV that fails is recorded in its CvResult.error; the others still build and still
    get their briefs - one Word crash must not lose a batch.
    """
    tailors: list[dict] = []
    for lead in taken:
        t = _tailor_dict(lead)
        store.add(
            Application(
                id=store.next_id(),
                company=lead.company,
                role=lead.title or lead.label,
                status="wishlist",
                url=lead.url,
                location=lead.data.get("location") or "Remote",
                notes=[
                    f"score {lead.data.get('score', '?')} | cv: {t['variant']}"
                    + (f" | {', '.join(lead.data.get('reasons', []))}" if lead.data else ""),
                    f"source: {lead.data.get('source', 'review sheet')}",
                ],
            )
        )
        tl.write_jd(t["company"], t["job_title"], lead.data.get("description", ""), t["slug"])
        tailors.append(t)
    for lead in dropped:
        store.add(
            Application(
                id=store.next_id(),
                company=lead.company,
                role=lead.title or lead.label,
                status="withdrawn",
                url=lead.url,
                location=lead.data.get("location") or "",
                notes=[f"dropped at review {today()} - not pursued"],
            )
        )
    store.save()

    cvs: list[tl.CvResult] = []
    built: list[dict] = []
    if tailors:
        emit(report, "tailor", f"\nTailoring {len(tailors)} CV(s)…")
    for i, t in enumerate(tailors, 1):
        emit(
            report,
            "tailor",
            f"Tailoring {i}/{len(tailors)}: {t['company']}",
            done=i - 1,
            total=len(tailors),
            detail=True,
        )
        try:
            cv = tl.fit(t, report=report)
            built.append(t)
        # build_variant raises SystemExit for a variant missing from profile.json.
        except (Exception, SystemExit) as exc:  # noqa: BLE001
            cv = tl.CvResult(t["company"], t["variant"], error=str(exc) or type(exc).__name__)
        cvs.append(cv)
        emit(report, "tailor", cv_line(t, cv), done=i, total=len(tailors))
    if built:
        tl.append_briefs(built, briefs)
    return PickResult(taken, dropped, cvs, briefs)
```

Replace `main` from `chosen = (` to the final `return 0` with:

```python
try:
    taken, dropped = choose(leads, args.selectors, args.sheet.name)
except JobsError as exc:
    print(exc, file=sys.stderr)
    return 1

for lead in taken:
    print(f"  take  #{lead.index}  {lead.company} — {lead.title or lead.label}")
for lead in dropped:
    print(f"  drop  #{lead.index}  {lead.company}")
if args.dry_run:
    print(f"\n--dry-run: would build {len(taken)} CV(s), nothing written.")
    return 0

result = run(
    taken,
    dropped,
    briefs=args.briefs,
    store=Store(args.file or default_path()),
    report=printer(),
)
built = [cv for cv in result.cvs if not cv.error]
if built:
    print(f"\n  {len(built)} brief(s) appended to {tl.display_path(args.briefs)}")
if dropped:
    print(f"  {len(dropped)} lead(s) recorded as withdrawn - they won't be scraped again.")
print("\nNext: apply, then  python -m jobs.brief applied <n>")
return 1 if len(built) < len(result.cvs) else 0
```

Delete `pick.py`'s now-unused `ROOT` only if ruff reports it unused.

Why the per-CV line is an event and not printed by `main`: the CLI prints it as each CV
finishes, which is the only feedback during a multi-minute Word run. `cv_line` keeps the
exact old text for the three existing cases; `!! failed:` is new (behaviour change 1).

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_pick.py tests/test_cli_parity.py -v`
Expected: all PASS; `pick_ticked`, `pick_dry_run`, `pick_no_sheet` goldens unchanged.

If `pick_ticked` differs only in the order of the per-CV note checks, compare with the old
`note` logic in git (`git show HEAD~1:jobs/pick.py`): the old code let a later check
overwrite an earlier one, so "no PDF" won over "trimmed". `cv_line` keeps that precedence.

- [ ] **Step 5: Full suite + lint, then commit**

Run: `pytest; ruff check .; ruff format --check .`

```bash
git add jobs/pick.py tests/test_pick.py
git commit -m "pick: choose() and run() return results; one failed CV no longer drops the batch's briefs

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: `scrape.run()` with per-source progress

`--save`, `--cv` and `--json` are the legacy one-shot flow and act on the result after the
table is printed, so they stay in `main` (the UI does not use them). `ScrapeResult` therefore
has no `saved`/`cvs` fields — the spec is amended to match.

**Files:**
- Modify: `jobs/scrape.py` (`collect(report=)`, `ScrapeResult`, `run`, `main`)
- Create: `tests/test_scrape_run.py`

**Interfaces:**
- Consumes: `progress.emit`, `progress.printer`; `settings.SettingsError`
- Produces:
  - `collect(wanted, ashby=None, queries=None, report: Report | None = None)`
  - `ScrapeResult(raw: int, problems: list[str], leads: list[Scored], sheet: Path | None, boards: int, pruned: int, example_settings: bool)`
  - `run(*, sources: list[str] | None, min_score: int | None, store: Store, include_tracked: bool = False, report: Report | None = None) -> ScrapeResult`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_scrape_run.py`:

```python
"""scrape.run(): fetch, rank, write the sheet - and say what it is doing as it goes."""

from __future__ import annotations

from datetime import date

from jobs import review, scrape
from jobs.sources import Posting

from jobtrack.storage import Store


def posting(n: int) -> Posting:
    return Posting(
        source="remotive",
        company=f"Studio{n}",
        title="QA Automation Engineer",
        url=f"https://x/{n}",
        location="Remote (worldwide)",
        description="Fully remote worldwide. Python test automation, regression testing, "
        "API testing, Jira. We are a game studio.",
        posted_at=date.today().isoformat(),
    )


def sandbox(monkeypatch, tmp_path):
    monkeypatch.setattr(scrape.discover, "load_state", lambda *a, **k: {"boards": {}})
    monkeypatch.setattr(scrape, "_refresh_boards", lambda *a, **k: None)
    real = review.write_sheet
    monkeypatch.setattr(
        scrape.review,
        "write_sheet",
        lambda leads, when: real(leads, when, sheet=tmp_path / "S.md", data=tmp_path / "S.json"),
    )


def test_collect_ticks_once_per_source_from_the_calling_thread(monkeypatch):
    import threading

    monkeypatch.setattr(scrape.sources, "remotive", lambda: [posting(1)])

    def broken():
        raise OSError("down")

    monkeypatch.setattr(scrape.sources, "arbeitnow", broken)
    events, threads = [], set()

    def report(e):
        events.append(e)
        threads.add(threading.get_ident())

    postings, problems = scrape.collect(["remotive", "arbeitnow"], [], report=report)
    assert len(postings) == 1 and problems == ["arbeitnow: OSError"]
    ticks = [e for e in events if e.detail]
    assert sorted(e.done for e in ticks) == [1, 2] and {e.total for e in ticks} == {2}
    assert threads == {threading.get_ident()}


def test_run_ranks_writes_the_sheet_and_reports(tmp_path, monkeypatch, cv_sandbox):
    sandbox(monkeypatch, tmp_path)
    monkeypatch.setattr(scrape, "collect", lambda *a, **k: ([posting(1), posting(2)], []))
    events = []
    result = scrape.run(
        sources=None,
        min_score=None,
        store=Store(tmp_path / "a.json").load(),
        report=events.append,
    )
    assert result.raw == 2 and len(result.leads) == 2
    assert result.sheet == tmp_path / "S.md" and result.sheet.exists()
    assert all(s.tailor for s in result.leads)
    stages = [e.stage for e in events if not e.detail]
    assert stages[0] == "fetch" and "rank" in stages


def test_run_with_nothing_qualifying_writes_no_sheet(tmp_path, monkeypatch, cv_sandbox):
    sandbox(monkeypatch, tmp_path)
    monkeypatch.setattr(scrape, "collect", lambda *a, **k: ([], ["remotive: HTTPError"]))
    result = scrape.run(sources=None, min_score=None, store=Store(tmp_path / "a.json").load())
    assert (result.leads, result.sheet, result.problems) == ([], None, ["remotive: HTTPError"])
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_scrape_run.py -v`
Expected: FAIL — `collect()` has no `report` parameter; `scrape.run` does not exist.

- [ ] **Step 3: Implement**

In `jobs/scrape.py` add imports: `from dataclasses import dataclass` (stdlib block) and
`from jobs.progress import Report, emit, printer  # noqa: E402`.

`collect`: add `report: Report | None = None` as the last parameter and replace the
`as_completed` loop with:

```python
for done, fut in enumerate(cf.as_completed(futures), 1):
    name = futures[fut]
    try:
        got = fut.result()
        out.extend(got)
        mark = f"✓ {len(got)}"
    except Exception as exc:  # noqa: BLE001
        problems.append(f"{name}: {type(exc).__name__}")
        mark = "✗"
    # Emitted here, in the calling thread, never from a worker - see jobs.progress.
    emit(
        report,
        "fetch",
        f"{done}/{len(futures)} {name} {mark}",
        done=done,
        total=len(futures),
        detail=True,
    )
```

Add before `main`:

```python
@dataclass
class ScrapeResult:
    raw: int
    problems: list[str]
    leads: list[Scored]
    sheet: Path | None
    boards: int
    pruned: int
    example_settings: bool


def run(
    *,
    sources: list[str] | None,
    min_score: int | None,
    store: Store,
    include_tracked: bool = False,
    report: Report | None = None,
) -> ScrapeResult:
    """Fetch every source, rank against settings.json, write TODAY_SCRAPING.md.

    Raises settings.SettingsError for unreadable settings. Writes nothing to jobtrack.
    """
    cfg = settings.current()
    if cfg.is_example:
        emit(
            report,
            "fetch",
            "note: scoring against jobs/settings.example.json - copy it to "
            "jobs/settings.json and make it yours, or these rankings are someone else's.",
        )
    # Ashby: the curated boards plus everything jobs.discover found, minus the pruned.
    board_state = discover.load_state()
    run_day = date.today()
    ashby, pruned = discover.ashby_plan(
        TARGETS.get("ashby", []), board_state, discover.rejections(store), run_day
    )
    emit(report, "fetch", f"Fetching sources… ({len(ashby)} Ashby boards, {len(pruned)} pruned)")
    if not board_state["boards"]:
        emit(report, "fetch", "  tip: `python -m jobs.discover` widens Ashby beyond targets.json")
    postings, problems = collect(sources, ashby, cfg.search_queries, report=report)
    raw = len(postings)
    emit(report, "fetch", f"  {raw} raw postings")
    if problems:
        emit(report, "fetch", f"  {len(problems)} source(s) unavailable: {', '.join(problems[:6])}")
    _refresh_boards(board_state, ashby, postings, problems, run_day, sources)
    postings = drop_stale_discovered(postings, TARGETS.get("ashby", []), run_day)
    seen = set() if include_tracked else already_tracked(store)

    # `exclude=` rather than filtering the result: the per-company cap inside rank()
    # must not spend a company's daily slots on roles already in jobtrack. See rank().
    leads = rank(postings, min_score=min_score, exclude=seen, settings=cfg)
    rejected = len(postings) - len(leads)
    emit(
        report,
        "rank",
        f"  {len(leads)} qualify after filtering ({rejected} filtered out or already tracked)",
    )
    for s in leads:
        s.tailor = tl.tailor_for(s.posting, s.variant)
    sheet = review.write_sheet(leads, today()) if leads else None
    if sheet:
        emit(report, "write", f"{len(leads)} lead(s) written", detail=True)
    return ScrapeResult(raw, problems, leads, sheet, len(ashby), len(pruned), cfg.is_example)
```

Replace `main`'s body from `try: cfg = settings.current()` down to (not including)
`chosen = leads[: args.limit]` with:

```python
    store = Store(args.file or default_path())
    try:
        result = run(
            sources=args.sources,
            min_score=args.min_score,
            store=store,
            include_tracked=args.include_tracked,
            report=printer(),
        )
    except settings.SettingsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    leads = result.leads

    show(leads, args.limit)
    # --limit is a display setting. Everything that qualified goes to the sheet, so a
    # lead is never dropped just because it fell past the end of the printed table.
    if result.sheet:
        print(f"\n{len(leads)} lead(s) written to {tl.display_path(result.sheet)}")
        print("Tick the ones you want, then:  python -m jobs.pick")
```

The rest of `main` (`chosen = ...`, `--json`, `--save`, `--cv`, return) stays as it is.
`show()` and `make_cvs()` keep printing: they are CLI-only helpers called from `main`. Add
this line to each docstring: "CLI helper - called only from main()."

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_scrape_run.py tests/test_cli_parity.py tests/test_discover.py -v`
Expected: all PASS; `scrape_sheet` golden unchanged.

- [ ] **Step 5: Full suite + lint, then commit**

Run: `pytest; ruff check .; ruff format --check .`

```bash
git add jobs/scrape.py tests/test_scrape_run.py
git commit -m "scrape: run() returns the ranked leads and reports per-source progress

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: Docs and a real-Word check

**Files:**
- Modify: `CLAUDE.md`, `jobs/README.md`, `docs/superpowers/specs/2026-09-21-core-refactor-design.md`

- [ ] **Step 1: Update `CLAUDE.md`**

- Conventions: change "**No printing outside `cli.py`.** Other modules return strings or raise."
  to "**No printing outside `cli.py` or a module's `main()`.** Other functions return data or
  raise; long jobs report through `jobs.progress`. `jobs/errors.py` holds the 'nothing to do'
  errors `main` turns into exit 1."
- Layout: add `progress.py   Progress events for long jobs (scrape, pick, paste)` and
  `errors.py     JobsError / Skipped - raised by run functions, mapped to exit 1 by main`
  under `jobs/`.
- Watch out for: in the "Every variant builds to the same filename" bullet, replace
  "`brief.parse_cv` and `brief._cv_for` still read that older shape" with
  "`brief.parse_cv` and `brief._cvs_for` still read that older shape", and add: "A tailored
  folder holds the `.docx` always and the `.pdf` when Word or LibreOffice exists; `close`
  files and sweeps both."

- [ ] **Step 2: Update `jobs/README.md`**

In the `jobs.brief` section, change "creates `applications/<posting-slug>/` holding the tailored
CV" to "creates `applications/<posting-slug>/` holding the tailored CV (the `.docx`, and the
`.pdf` when Word or LibreOffice made one)".

- [ ] **Step 3: Amend the spec to what was built**

In the spec: `brief.mark(...)` → `brief.mark_briefs(...)`; `ScrapeResult` loses `saved` and
`cvs` (the one-shot flags stay in `main`); pick is `choose()` + `run()`; "315 tests" →
"500 tests"; `cv/build.py` warnings are collected when a list is passed and printed as before
when not.

- [ ] **Step 4: Check against real Word (Windows with Word only)**

In a scratch copy of the example profile, not your own:

Run: `python -m jobs.paste --company Acme --title "QA Engineer" --file <a real JD> --force --no-llm`
Expected: `CV -> cv/out/tailored/acme-qa-engineer/<name>.pdf  (2 pages)` and a `.docx` beside it.

Then from a worker thread:

```bash
python -c "import threading; from jobs import tailor as tl; t={'company':'Acme','job_title':'QA','variant':'qa_gaming','matched':[],'gaps':[],'slug':'acme-thread','url':''}; r=[]; th=threading.Thread(target=lambda: r.append(tl.fit(t))); th.start(); th.join(); print(r[0].engine, r[0].pages)"
```

Expected: `word 2` (or `word 1`). Before Task 3 this printed `None None` with a COM error in the
warnings. Delete `cv/out/tailored/acme-*` afterwards.

- [ ] **Step 5: Full suite + lint, then commit**

Run: `pytest; ruff check .; ruff format --check .`

```bash
git add CLAUDE.md jobs/README.md docs/superpowers/specs/2026-09-21-core-refactor-design.md
git commit -m "Docs: run/main split, progress events, DOCX-first filing

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```
