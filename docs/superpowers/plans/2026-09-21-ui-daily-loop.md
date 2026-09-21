# UI Shell + Daily Loop Implementation Plan

**Goal:** `python -m jobs.ui` serves a local page that runs scrape → review → build → apply →
close and Paste a job over the `jobs/` run functions.

**Spec:** [docs/superpowers/specs/2026-09-21-ui-daily-loop-design.md](../specs/2026-09-21-ui-daily-loop-design.md)
**Branch:** `ui-daily-loop`. Commits follow the commit-like-it skill.

## Global constraints

- Standard library only at runtime; `from __future__ import annotations`; ruff, line length 100.
- No printing outside `jobs/ui/__main__.py` and `server.py`'s request log.
- Tests never touch the real repo files: every UI test builds a `Paths` under `tmp_path`;
  anything that builds a CV uses `cv_sandbox`; the scrape is stubbed.
- Suite (543 passed, 2 skipped at the start) and `ruff check . && ruff format --check .` pass
  after every task. Each task is test-first: write the tests, see them fail, implement, see
  them pass, commit.

## Tasks

### 1. `Paths`, phase detection, `review.set_mark`

- `jobs/ui/__init__.py` (empty docstring), `jobs/ui/paths.py`: frozen dataclass
  `Paths(root, sheet, data, briefs, store, tailored, applications)`; `Paths.default()` from the
  repo layout (`review.SHEET_PATH`, `review.DATA_PATH`, `brief.BRIEFS_PATH`,
  `jobtrack.storage.default_path()`, `tailor.TAILORED_DIR`, `root/applications`);
  `Paths.under(root)` for tests (same relative layout).
- `review.sheet_date(sheet) -> str | None` — the `YYYY-MM-DD` from the header line.
- `review.set_mark(url, mark, *, sheet) -> None` — rewrites the marker of the heading whose
  `- **Link:**` is `url`; raises `JobsError("The sheet changed - refresh ...")` when absent,
  `ValidationError` for an unknown mark.
- `jobs/ui/phase.py`: `detect(paths, today: str) -> str` returning `scrape` / `review` /
  `apply` / `close`, per the spec table.
- Tests: `tests/test_review.py` (set_mark round-trip for take/skip/pending, unknown URL,
  bad mark, sheet_date), `tests/test_ui_phase.py` (each table row, yesterday's sheet).

### 2. `TaskRunner`

- `jobs/ui/tasks.py`: `TaskRunner.start(kind, fn)` — `fn(report) -> dict`; raises `Busy`
  when a task runs. Events are dicts `{"type": "progress"|"done"|"failed", ...}` appended to
  the current task's list under a lock with a `Condition`; `events(since) -> (list, finished)`
  blocks up to a timeout for new events. `running() -> str | None`. The worker thread is a
  daemon. A raising `fn` yields `failed` with `str(exc)` and frees the runner.
- Tests: `tests/test_ui_tasks.py` — progress then done in order; Busy while running;
  failed event; runner free after failure; `events(since)` returns only newer events.

### 3. `api.py` + `opener.py`

- `jobs/ui/opener.py`: `open_path(path, reveal)` per platform; `allowed(path, paths) -> bool`
  (resolved path under `root/cv/out` or `applications`).
- `jobs/ui/api.py`, all taking `paths` first:
  `state(paths, runner, engine)`, `leads(paths)`, `mark_lead(paths, url, mark)`,
  `cvs(paths)`, `briefs(paths)`, `mark_briefs(paths, selectors, state, reason)`,
  `close(paths, dry_run, force)`, `assess(paths, text, company, title, url, location,
  variant)` (stores the `Assessment` + text in a module dict by id, keeps the last 20),
  `start_scrape(paths, runner)`, `start_pick(paths, runner)`,
  `start_paste(paths, runner, id, force)`, `open_file(paths, path, reveal, opener)`,
  `pdf_engine()` (winreg `Word.Application` + pywin32 importable → `word`; else
  `build._find_soffice()` → `libreoffice`; else `none`; never launches Word).
  `close` with `dry_run` maps to `brief.close(..., dry_run=True)`.
  Pick/paste/scrape task results are summarised to JSON (`cvs` as `{company, variant, docx,
  pdf, engine, pages, kept, wanted, error}`, paths relative to root).
- Tests: `tests/test_ui_api.py` — each function against `Paths.under(tmp_path)` with a sheet
  written by `review.write_sheet` and `cv_sandbox`; start_pick runs to `done` and builds a
  DOCX; assess → start_paste; Skipped surfaces; open_file refuses outside paths (opener
  stubbed); state reports phase and counts.

### 4. `server.py` + `__main__.py`

- `jobs/ui/server.py`: `make_server(paths, port, *, token=None, runner=None, engine=None,
  opener=None) -> ThreadingHTTPServer` on 127.0.0.1. Handler: Host check (403), token check on
  POST (403), JSON body parse (400), routes per spec, error mapping (400/409/403/500 with
  `{error}`), static files from `jobs/ui/static` with the token injected into `index.html`,
  `GET /api/events?since=N` as SSE (streams until the task finishes, then closes).
  Quiet request log.
- `jobs/ui/__main__.py`: argparse `--port` (default 8765, next free if busy), `--no-browser`;
  prints the URL; `webbrowser.open`; `serve_forever` until Ctrl+C.
- Tests: `tests/test_ui_server.py` — real server on port 0 in a thread, urllib: index has a
  token meta; POST without token → 403; foreign Host → 403; `/api/state` JSON; bad mark → 400;
  busy → 409; SSE delivers a stubbed task's events; unknown route → 404.

### 5. The page

- Fonts: download Atkinson Hyperlegible Regular/Bold woff2 (OFL) into
  `jobs/ui/static/fonts/` with `OFL.txt`.
- `index.html`, `app.css`, `app.js` following the mockup: step bar, progress line from SSE,
  Scrape / Review (keys j/k/x/-/space) / Build / Apply / Close views, Paste a job, Applications
  placeholder, example-settings banner, no-PDF-engine note. `app.js` calls the API with the
  token header, re-fetches state after each action and on focus.
- Check: `node --check jobs/ui/static/app.js`; a server test that every `/static/*` reference in
  `index.html` is served.

### 6. Docs and manual run

- `README.md` (a "Using the web page" section), `jobs/README.md` pointer, `CLAUDE.md` layout
  lines for `jobs/ui/`.
- Manual: run `python -m jobs.ui --no-browser` against a sandbox root, walk the API through a
  full day with `curl`, and open the page in a browser.
