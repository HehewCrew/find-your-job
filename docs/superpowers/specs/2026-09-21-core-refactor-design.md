# Core refactor: `jobs/` returns data — design

Date: 2026-09-21. Status: approved in brainstorming, awaiting spec review.

## Context

A local web UI is planned so the toolchain can be used without Claude Code. It is split
into four sub-projects, each with its own spec, plan and TDD build:

| # | Sub-project | Depends on |
|---|---|---|
| **1** | **Core refactor** (this spec) — `jobs/` returns data and reports progress; the CLIs print it | — |
| 2 | UI shell + daily loop — server, layout, visual design, scrape → pick → paste → brief → close | 1 |
| 3 | Setup — first-run check, forms for all four personal files | 2 |
| 4 | Dashboard — the full `jobtrack` feature set | 2 |

Decisions already taken for the UI, recorded here so later specs start from them:

- The UI imports the refactored modules directly (no subprocess, no stdout parsing).
- Plain HTML/CSS/JS served from the repo: no build step, no CDN, works offline.
- `python -m jobs.ui` binds to localhost only and opens the browser. Single user, no login.
- Setup uses forms for all four files. `priorities.md` / `rules.md` are one field per heading;
  the `profile.json` form must round-trip keys it does not display (`_note`s, optional keys).
- Paste shows the verdict with Build / Build anyway. No follow-up chat in v1.
- DOCX is always delivered; PDF is an extra from Word or LibreOffice. With neither, the CV is
  built and flagged "page count not checked" with a link to LibreOffice.
- The dashboard covers everything the `jobtrack` CLI does; delete asks for confirmation.
- `jobs.discover` stays out of v1.

## Goal

Every command in `jobs/` splits into a `run` function that does the work and returns a
result, and a `main` that parses arguments, calls `run`, and prints. `run` never prints,
never calls `input()`, never calls `sys.exit`. The CLI's output, exit codes and file effects
stay the same, except for the two changes called out below.

This brings `jobs/` in line with the rule `jobtrack` already follows: no printing outside
the CLI layer.

## Result shapes

| Module | Function(s) | Returns |
|---|---|---|
| `jobs.progress` (new) | — | `Progress`, `Report` (below) |
| `jobs.tailor` | `fit(t, report=None)` | `CvResult` |
| `jobs.brief` | `mark(path, selectors, state, reason)`, `close(path, root, store, *, force, keep_cvs, dry_run)` | `MarkResult`, `CloseResult` |
| `jobs.paste` | `assess(...)` (exists), `build(assessment, *, force, save, store, report=None)` | `PasteResult`; raises `Skipped` |
| `jobs.pick` | `run(selectors, *, sheet, data, briefs, store, dry_run, report=None)` | `PickResult` |
| `jobs.scrape` | `run(*, sources, min_score, limit, save, cv, json_path, store, include_tracked, report=None)` | `ScrapeResult` |

All results are dataclasses defined in the module that produces them.

```python
@dataclass
class CvResult:
    company: str
    variant: str
    docx: Path | None          # always present on success
    pdf: Path | None           # None when no engine produced one
    engine: str | None         # "word" | "libreoffice" | None
    pages: int | None          # None = not checked
    keywords_wanted: int
    keywords_kept: int
    warnings: list[str]        # what build.py used to print
    error: str = ""            # set when this CV failed; the others still complete
```

- `PickResult`: `taken`, `dropped` (leads), `cvs: list[CvResult]`, `briefs_path`.
- `PasteResult`: `assessment`, `cv: CvResult`, `forced: bool`, `app_id: int | None`.
- `MarkResult`: `changed: list[Brief]`, `pending: int`.
- `CloseResult`: `filed: list[Filed]`, `dropped: list[tuple[Brief, int]]`, `swept: list[Path]`,
  `log_path`, `dry_run`.
- `ScrapeResult`: `raw: int`, `problems: list[str]`, `leads: list[Scored]`, `sheet: Path | None`,
  `boards: int`, `pruned: int`, `example_settings: bool`, `saved: list[Application]`,
  `cvs: list[CvResult]`.

The exact field lists may grow during implementation if `main` needs something to print;
they may not shrink below what is listed.

## Progress reporting

New module `jobs/progress.py`, with no internal imports:

```python
@dataclass(frozen=True)
class Progress:
    stage: str              # "fetch" | "rank" | "tailor" | "llm" | "write"
    message: str            # readable sentence
    done: int | None = None
    total: int | None = None
    detail: bool = False    # per-item tick

Report = Callable[[Progress], None]
```

`run` functions take `report: Report | None = None`; `None` means no reporting.

| Command | Events |
|---|---|
| scrape | `fetch` "Fetching sources… (N Ashby boards, M pruned)"; `fetch` detail tick per source as it completes (`done/total`, name, ok or failed); `fetch` "N raw postings"; `rank`; `write` sheet written |
| pick | `tailor` per CV "Tailoring i/N: Company"; `tailor` detail tick per keyword-trim rebuild |
| paste | `llm` "Asking <provider> for a verdict…"; `tailor` for the build |
| brief close | none — it is fast |

**CLI parity:** `main` passes a reporter that prints the `message` of every non-detail event.
Those are exactly the lines printed today, at the same moments — "Fetching sources…" still
appears before the wait.

**Thread rule:** events are emitted only from the thread that called `run`, never from pool
workers. `scrape.collect` already consumes `as_completed` in the calling thread; it emits
there. The UI therefore receives events one at a time.

**Out of scope:** cancelling a running job. The UI disables Start while a job runs.

## DOCX-first (addition found while writing this spec)

Today `tailor.build` calls `to_pdf` without `keep_docx`, so the DOCX is deleted once a PDF
exists, and `brief` only looks for `*.pdf`. On a machine with neither Word nor LibreOffice,
`close` reports "no tailored CV found" and files nothing.

Changes:

- `tailor.build` / `fit` always keep the DOCX. A tailored folder holds `<name>.docx` and,
  when an engine exists, `<name>.pdf`.
- `brief._cv_for` returns every CV file for a slug (`.pdf` and `.docx`, plus the old flat
  `*__<slug>.*` fallback). `file_brief` copies all of them; `Filed.cv` becomes
  `Filed.cvs: list[Path]`. The "no tailored CV" problem is raised only when neither exists,
  and a hand-placed `.docx` in `applications/<slug>/` counts like a hand-placed `.pdf` does.
- `sweep_tailored` removes both files, under the same "only after the filed copy is confirmed
  on disk" rule.
- `questions.md` lists the file names that were filed.

Other `to_pdf` callers (`cv/build.py`'s own `main`) keep their current `--keep-docx` behaviour.

## `cv/build.py`

Only the code under `to_pdf` changes:

- `_word_to_pdf`, `_libreoffice_to_pdf`, `_report_oversized` and the `build_variant` "no
  bullets tagged" warning stop printing and append to a `warnings` list the caller passes in.
  `cv/build.py`'s `main` prints that list, so its output is unchanged.
- `to_pdf` reports which engine produced each PDF.
- `_word_to_pdf` calls `pythoncom.CoInitialize()` / `CoUninitialize()` when it is not on the
  main thread. From the CLI this does nothing; from a UI worker thread it is what makes Word
  COM work at all.

## Errors

| Raised by `run` | Meaning | `main` exits |
|---|---|---|
| `SettingsError`, `ValidationError` | bad settings or input | 2 |
| `JobsError` (new, `jobs/errors.py`) | nothing to do: no sheet, nothing ticked, briefs still pending, nothing marked | 1 |
| `Skipped(JobsError)` | paste verdict is skip and `force` is false; carries the `Assessment` | 1 |

Partial failures are data, not exceptions: a failed source goes in `ScrapeResult.problems`,
a failed CV in `CvResult.error`.

`brief.close` keeps its existing guarantee: every mutation happens before anything is
reported, so a crash while printing never leaves the store unsaved on top of written folders.

Paste's terminal-only behaviour — the follow-up question loop and the "Tailor a CV anyway?"
prompt — stays in `paste.main`, which calls `assess`, asks, then calls `build(force=...)`.

## Behaviour changes

1. **pick survives one failing CV.** Today, if CV #3 raises, CVs #1–2 are built but no brief
   is appended, because `append_briefs` runs after the loop. After: each CV's exception is
   caught into `CvResult.error`, briefs are appended for the CVs that built, and `main`
   reports the failure and exits 1.
2. **DOCX-first**, as described above: tailored folders keep the DOCX, and `close` files it.

Nothing else changes in the CLI's output, exit codes or file effects.

## Testing

For each module, in this order:

1. **Characterization tests first.** Run the current `main` against fixtures with network
   access stubbed and Word forced unavailable. Snapshot stdout, stderr, exit code, and the
   files written. These pass before the refactor and, unchanged, after it — except where a
   test pins one of the two behaviour changes, which is written as a new failing test first.
2. **TDD for each `run`:** result fields, the progress-event sequence (a fake reporter that
   records into a list), and the exceptions raised.
3. `fit` is tested with a fake `to_pdf` that reports page counts, so the trimming loop is
   covered without Word.
4. The existing suite (315 tests, including `tests/test_score.py` on the Tunisia fixture)
   stays green at every commit. `ruff check . && ruff format --check .` passes.

## Order

One commit per step, suite green after each:

1. `jobs/progress.py`, `jobs/errors.py`, `CvResult`, `tailor.fit`/`build`, `cv/build.py` warnings, engine report
   and COM initialisation, DOCX kept.
2. `brief`: `mark`, `close`, DOCX-aware filing and sweeping.
3. `paste`: `build` split from `main`; `Skipped`.
4. `pick`: `run`; per-CV failure isolation.
5. `scrape`: `run`; per-source progress ticks.

## Out of scope

`jobs.discover`, `cv/init.py`, `cv/build.py`'s own `main` beyond printing the returned
warnings, and `jobtrack` (already follows the rule). Cancellation. Anything UI.

## Docs to update

`CLAUDE.md` ("No printing outside `cli.py`" gains "or a module's `main`"; the BRIEFS/CV
contract note gains the DOCX), `jobs/README.md` (DOCX kept next to the PDF; close files both).
