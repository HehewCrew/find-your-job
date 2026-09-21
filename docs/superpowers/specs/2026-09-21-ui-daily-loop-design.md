# UI shell + daily loop — design

Date: 2026-09-21. Status: approved in brainstorming, awaiting spec review.
Sub-project 2 of 4. Builds on [the core refactor](2026-09-21-core-refactor-design.md).
Mockup: https://claude.ai/artifact/6JWXrFyNzJZQBRXwKsvwzc (private to the owner).

## Goal

`python -m jobs.ui` opens a local web page that runs the whole daily loop — scrape, review,
build, apply, close — plus Paste a job, without Claude Code and without the command line.
The page and the CLI work on the same files and can be mixed in the middle of a day.

## Decisions

| # | Decision |
|---|---|
| 1 | `TODAY_SCRAPING.md` and `BRIEFS.md` stay the source of truth. The UI rewrites the same `[x]`/`[-]` and `**Status:**` lines the CLI reads. |
| 2 | One **Today** page with a step bar (Scrape › Review › Build › Apply › Close) that opens on the detected phase; **Paste a job** and **Applications** in the top nav. Applications is a placeholder until sub-project 4. |
| 3 | Review shows what the scorer gives (score, reasons, matched skills, gaps) plus the full description. No LLM call per lead in v1. |
| 4 | Each CV has **Open** (default app) and **Show in folder** (file selected). |
| 5 | Calm, practical look; light and dark; dense only in the lead list. |

Carried over from the core-refactor spec: plain HTML/CSS/JS, no build step, no CDN, works
offline; binds to localhost only; single user, no login; imports the `jobs/` `run` functions
directly; DOCX always, PDF as an extra, "page count not checked" with a LibreOffice link
when neither Word nor LibreOffice exists; standard library only.

## Architecture

New package `jobs/ui/`:

| File | Responsibility |
|---|---|
| `__main__.py` | `python -m jobs.ui [--port N] [--no-browser]`. Default port 8765; if busy, the next free one. Opens the browser unless `--no-browser`; always prints the URL. Ctrl+C stops. |
| `server.py` | `ThreadingHTTPServer` on `127.0.0.1`. Routing, JSON bodies, static files, the token and Host checks, error mapping. No job logic. |
| `api.py` | One function per endpoint: takes a `Paths` and plain arguments, returns a JSON-able dict, raises the core errors. No HTTP. |
| `paths.py` | `Paths` dataclass: `root, sheet, data, briefs, store, tailored, applications`. `Paths.default()` is the repo layout; tests build one under `tmp_path`. |
| `tasks.py` | `TaskRunner`: runs at most one long job on a worker thread, queues its `Progress` events plus a final `done`/`failed` event, and lets one or more listeners read them. |
| `phase.py` | `detect(paths, today) -> Phase` — the `/jobhunt` phase table as a pure function. |
| `opener.py` | `open_path(path, reveal)` — `os.startfile` / `explorer /select,` on Windows, `open` / `open -R` on macOS, `xdg-open` (folder only for reveal) on Linux. |
| `static/` | `index.html`, `app.css`, `app.js`, `fonts/AtkinsonHyperlegible-{Regular,Bold}.woff2` + `OFL.txt`. |

Core addition: `review.set_mark(url, mark, *, sheet) -> Lead` rewrites one heading's marker,
found by the lead's `- **Link:**` URL. Raises `JobsError` when the URL is no longer on the sheet.

### Phase detection

Same order as `/jobhunt`, first match wins:

| Look at | If | Phase |
|---|---|---|
| `BRIEFS.md` | has briefs, some pending | `apply` |
| `BRIEFS.md` | has briefs, all marked | `close` |
| `TODAY_SCRAPING.md` | dated today, has `[x]`/`[-]` marks | `review` (picks made — Build is enabled) |
| `TODAY_SCRAPING.md` | dated today, all `[ ]` | `review` |
| `TODAY_SCRAPING.md` | missing or dated before today | `scrape` |

The step bar maps phases to steps; `build` is shown as the current step while a pick job runs
and right after it finishes, until the page is left or reloaded — after which the detector
says `apply`, which is correct.

### API

All responses are JSON. Every `POST` requires header `X-FYJ-Token: <token>`; every request
requires `Host: 127.0.0.1:<port>` or `localhost:<port>`.

| Method + path | Body | Returns |
|---|---|---|
| `GET /` and `/static/*` | — | the page; the token is injected into `index.html` as `<meta name="fyj-token">` |
| `GET /api/state` | — | `phase`, `sheet_date`, lead counts by mark, brief counts by state, `example_settings`, `pdf_engine` (`word`/`libreoffice`/`none`), `task` (running kind or null) |
| `GET /api/leads` | — | leads: `index, url, company, title, mark, score, reasons, variant, location, matched, gaps, description` |
| `POST /api/leads/mark` | `{url, mark}` (`take`/`skip`/`pending`) | the updated lead |
| `POST /api/scrape` | `{}` | `{task: "scrape"}`; 409 if a task is running |
| `POST /api/pick` | `{}` (uses the sheet's ticks) | `{task: "pick"}` |
| `GET /api/cvs` | — | CVs in `cv/out/tailored/*/`: `slug, company, title, docx, pdf` (paths relative to root) |
| `POST /api/paste/assess` | `{text, company, title, url, location, variant}` | verdict: `decision, source, reasons, blockers, gaps, questions, disagree, llm_error, variant, matched, gap_skills, id` |
| `POST /api/paste/build` | `{id, force}` | `{task: "paste"}`; 409 `Skipped` carries the verdict |
| `GET /api/briefs` | — | briefs: `index, company, title, state, reason, variant, url, cv` |
| `POST /api/briefs/mark` | `{selectors, state, reason}` | `changed, pending` |
| `POST /api/close` | `{dry_run, force}` | `filed: [{company, folder, app_id, problems}]`, `dropped: [{company, app_id}]`, `cleared` |
| `GET /api/events` | — | Server-Sent Events for the current task: `progress` (`stage, message, done, total, detail`), then one `done` (the task's result summary) or `failed` (`message`) |
| `POST /api/open` | `{path, reveal}` | `{}`; 400 if the path is outside `cv/out/` or `applications/` |

`pdf_engine` is detected once at startup (pywin32 + a Word COM class registered, else
`build._find_soffice()`, else none) and cached; detection must not launch Word.

### Security

A local server is reachable from any web page the user opens.

- **Token:** 32 random bytes (`secrets.token_urlsafe`) per server start, injected into
  `index.html`, required on every `POST`. A page on another origin cannot read it.
- **Host check:** requests whose `Host` is not `127.0.0.1:<port>` or `localhost:<port>` get 403
  — blocks DNS rebinding.
- **Open allowlist:** `/api/open` resolves the path and refuses anything not under
  `root/cv/out` or `root/applications`.
- No CORS headers are ever sent.

## Screens

Visual direction as in the mockup.

- **Tokens:** paper `#F6F7F9`/`#16191E`, ink `#1F2733`/`#E4E7EC`, rule `#D9DDE3`/`#2C323B`,
  signature blue `#2B4BA0`/`#8EA4EE` (current step, primary actions, "applied"), take
  `#2E7D4F`/`#6CC191`, drop `#8A8F98`/`#6B717B` (grey, not red), warning `#9A5B00`/`#E3B061`.
  Light by default, dark via `prefers-color-scheme`.
- **Type:** Atkinson Hyperlegible 400/700, bundled; tabular figures for scores and counts.
- **Step bar:** numbered 1–5; current step in signature blue; finished steps show their
  outcome ("41 leads found", "6 taken, 9 dropped", "6 CVs built", "2 of 6 sent"). No cards,
  no shadows; rules only between rows.
- **Progress line** under the step bar while a task runs: message plus a thin bar when
  `done/total` are known. The page stays usable; the start buttons are disabled.
- **Scrape:** summary of the last run, or the empty state "No sheet for today yet. Run the
  scrape to find leads." with **Run today's scrape**. Shows the example-settings warning when
  `example_settings`.
- **Review:** split view — lead rows (`[x]`/`[-]`/`[ ]`, score, company, role) left; detail right
  (title, company + location, Take / Drop for good / Decide later, score and reasons, CV,
  matched chips, gaps, description ≤ 72ch). Keys: `j`/`k` or arrows move, `x` take, `-` drop,
  space decide later. Footer: tally and **Build N CVs**.
- **Build:** one row per CV with **Open** and **Show in folder**; the "Page count not checked"
  note with the LibreOffice link when `pdf_engine` is `none`; **Start applying**.
- **Apply:** one row per brief with **Open CV**, **Mark applied**, **Not pursuing** (asks for an
  optional reason); footer **Review the close** once none are pending.
- **Close:** a dry-run preview ("File 2, withdraw 4, add today to the log") from
  `POST /api/close {dry_run: true}`, then **Close the day**.
- **Paste a job:** company, role, link, description; **Get a verdict**; verdict panel (call,
  reasons, blockers, matched, gaps, LLM note); **Build the CV**, or **Build anyway** on a skip.
- **Applications:** placeholder pointing to the CLI (`jobtrack list`) until sub-project 4.

Copy rules: buttons say what happens; an action keeps its name through the flow ("Mark
applied" → "Marked applied"); errors say what happened and what to do; empty states invite the
next action.

## Errors

| Situation | Response |
|---|---|
| `ValidationError`, `SettingsError`, bad JSON, unknown mark | 400 `{error}` |
| `JobsError` (nothing to do, briefs still pending, sheet changed) | 409 `{error}`; `StillPending` adds `pending: [labels]` |
| `Skipped` | 409 `{error, verdict}` |
| A task already running | 409 `{error: "A <kind> is already running."}` |
| Missing / wrong token, foreign Host | 403 `{error}` |
| Anything else | 500 `{error}`; traceback printed in the terminal |
| Task raises mid-run | `failed` event with the message; the runner is free again |

The page re-fetches `/api/state` after every action and on window focus, so edits made in the
CLI or a text editor show up; it keeps no copy of the sheet between actions.

## Testing

- `tests/test_ui_phase.py` — each row of the phase table, including "sheet dated yesterday".
- `tests/test_review.py` — `set_mark` round-trips through `read_sheet`; unknown URL raises.
- `tests/test_ui_api.py` — every `api` function against a `Paths` in `tmp_path`, with
  `cv_sandbox`; the scrape stubbed as in `tests/test_scrape_run.py`.
- `tests/test_ui_tasks.py` — one task at a time (second start raises), event order, a raising
  task yields `failed` and frees the runner.
- `tests/test_ui_server.py` — a real server on port 0 in a thread, `urllib` requests: page
  served with a token, `POST` without the token → 403, foreign `Host` → 403, error mapping,
  `/api/open` allowlist (opener stubbed), SSE stream delivers a stubbed task's events.
- No JavaScript unit tests; `app.js` stays a thin renderer over the API. The plan ends with a
  manual browser checklist against a sandbox copy of the example profile.
- Existing suite and `ruff` stay green.

## Out of scope

Setup forms (sub-project 3), the Applications dashboard (sub-project 4), LLM triage per lead,
cancelling a running task, `jobs.discover`, remote access, authentication beyond the token.
