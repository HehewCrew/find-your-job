# CLAUDE.md

Context for Claude Code working in this repository.

## What this is

A job-hunt toolchain, in three parts:

1. **`jobtrack`** — a dependency-free Python CLI for tracking job applications.
   Data lives in a single JSON file so it stays greppable and diffable.
2. **`cv/`** — generates tailored CV variants (DOCX + PDF) from one `profile.json`.
3. **`jobs/`** — daily scrape of public job APIs, filtered and ranked against the user's
   `jobs/settings.json`, feeding leads into `jobtrack` as `wishlist` entries.

The daily loop: `python -m jobs.scrape` → tick the leads worth having in
`TODAY_SCRAPING.md` → `python -m jobs.pick`, which records them in `jobtrack`, builds a
tailored CV each, and appends to `cv/out/tailored/BRIEFS.md` → apply manually →
`python -m jobs.brief applied <n>` / `aborted <n>` as you go → `python -m jobs.brief
close`, which files each applied posting under `applications/<slug>/`, logs the day,
updates `jobtrack`, and empties BRIEFS.md.

The scrape/pick split exists because a CV costs a Word round-trip and most leads are
dropped after a closer read. **Nothing is written to `jobtrack` until `pick` runs.**

`python -m jobs.paste --company X --file jd.txt` joins the same loop from the other end,
for a posting the boards never saw (LinkedIn, a referral). It gives a verdict first —
the scorer's rules, plus an LLM's read of the whole JD when `settings.json` has an `llm`
block — and builds nothing on a `skip` unless `--force`. Like `pick` it appends to
BRIEFS.md, so it can run at any point in the day without disturbing what is already
marked.

## Layout

```
src/jobtrack/
  models.py    Application dataclass, status vocabulary, validation
  storage.py   Store: JSON load/save (atomic), query helpers
  report.py    Table / detail / stats rendering, CSV export
  cli.py       argparse wiring; one cmd_* function per subcommand
cv/
  profile.json Single source of truth for every CV variant  (gitignored - personal data;
               profile.example.json is a tracked template that builds all 8 as-is)
  rules.md     What may be claimed, and the 2-page rule  (gitignored; .example.md tracked)
  build.py     stdlib DOCX writer + PDF export via Word COM
jobs/
  sources.py   Public API fetchers (no scraping, no browser automation)
  settings.py  Loads settings.json: who is searching and for what (phrases -> regexes)
  settings.example.json  Tracked template; used when settings.json is missing
  settings.json  The user's own  (gitignored)
  score.py     Eligibility filter + ranking + CV-variant selection, all driven by settings
  llm.py       Optional LLM verdict for paste: Anthropic / OpenAI / OpenAI-compatible, urllib
  tailor.py    Per-posting keyword matching; writes BRIEFS.md
  scrape.py    CLI entry point; writes the review sheet
  discover.py  Weekly: find Ashby boards via Common Crawl, prune stale/rejecting ones
  ashby_boards.json  discover's board list + freshness  (gitignored, regenerable)
  review.py    TODAY_SCRAPING.md + .json: render the day's leads, read the ticks back
  pick.py      Build CVs for the ticked leads, feed jobtrack, append to BRIEFS.md
  paste.py     Verdict on a hand-pasted JD, then a tailored CV; appends to BRIEFS.md
  brief.py     Mark BRIEFS.md applied/aborted, then file it into applied/
  progress.py  Progress events for long jobs (scrape, pick, paste)
  errors.py    JobsError / Skipped - raised by run functions, mapped to exit 1 by main
  targets.json Greenhouse/Lever/Ashby company boards to poll
  priorities.md  Apply / don't-apply criteria, read in triage  (gitignored; .example tracked)
TODAY_SCRAPING.md    Today's leads, awaiting [x]/[-]  (regenerated each scrape)
TODAY_SCRAPING.json  Their full descriptions; joined to the .md by URL
applications/  One working folder per application: CV sent + questions.md  (gitignored)
  LOG.md       Running record of what went out, newest day first
               NB: the folder, not applications.json — that's the jobtrack store
tests/         pytest suite mirroring the module layout
```

See [cv/README.md](cv/README.md) and [jobs/README.md](jobs/README.md) for the details of
each. **The decision criteria live in [jobs/priorities.md](jobs/priorities.md) and
[cv/rules.md](cv/rules.md)** — read both before editing CV content or the scoring weights.
Each is gitignored and personal, with a tracked `.example.md` beside it to copy from; a
fresh clone has neither, and `/jobhunt` refuses to triage until they exist.

## Commands

```bash
pip install -e ".[dev,pdf]"   # setup (pdf = pywin32, for the Word PDF export)
pytest                     # run tests
ruff check . && ruff format --check .
jobtrack --help
```

Point at a specific data file with `--file PATH` or `$JOBTRACK_FILE`.

`/jobhunt` ([.claude/skills/jobhunt/](.claude/skills/jobhunt/SKILL.md)) drives the whole
daily loop below. It detects which phase the repo is in and resumes there, so the same
command starts the morning scrape and closes out the evening. It stops at the two human
gates — confirming the triage, and applying — and never marks a posting applied on its own.

**"What we have today" runs the loop.** It's the everyday phrasing for `/jobhunt` — a
request to enter the pipeline at whatever phase the repo is in, not a question about the
repo. Don't answer it by describing the state; run the skill.

## Conventions

- **Standard library only** for runtime code. Dev dependencies (pytest, ruff) are fine.
  If a third-party runtime dependency seems necessary, ask first.
- **Layers don't skip.** `cli.py` calls `storage`/`report`; `storage` uses `models`;
  `models` depends on nothing internal. Keep it that way.
- **No printing outside `cli.py` or a module's `main()`.** Other functions return data
  or raise; long jobs report through `jobs.progress`. Each `jobs/` command is a
  `run`-style function (`scrape.run`, `pick.run`, `paste.build`, `brief.close`, ...)
  plus a `main` that prints its result - the UI calls the former. `jobs/errors.py`
  holds the "nothing to do" errors `main` turns into exit 1. `tests/golden/` pins
  the CLI's exact output; a diff there is a behaviour change, not noise.
- **Validation lives in `models.py`** and raises `ValidationError`; `cli.main` catches it
  and exits 2. Don't scatter `sys.exit` calls through command functions.
- Exit codes: `0` success, `1` not found / no results, `2` invalid input.
- Every subcommand needs a test in `tests/test_cli.py` covering the success path
  and at least one failure path.
- Line length 100, ruff-formatted, `from __future__ import annotations` at the top of
  every module.

## Adding a subcommand

1. Add a parser block in `build_parser()` with `set_defaults(func=cmd_<name>)`.
2. Write `cmd_<name>(args, store) -> int` alongside the other commands.
3. Put any rendering in `report.py`, not inline in the command.
4. Add tests, then update the command list in `README.md`.

## Watch out for

- `Store` loads lazily; mutating methods call `_ensure_loaded()` first. Preserve that
  if you add new ones.
- `save()` writes to a temp file then `replace()`s — don't "simplify" it to a direct write.
- `normalize_status` accepts prefixes, so `"w"` is deliberately ambiguous
  (wishlist/withdrawn) and raises. There's a test pinning this.
- Status order in `STATUSES` drives `--sort status`. Reordering it changes output.
- `aborted` in BRIEFS.md is not a jobtrack status — `jobs.brief` maps it to `withdrawn`.
  Don't add one to `STATUSES` to make it symmetrical.
- `jobs.brief` matches a brief to its jobtrack entry **by URL first**. Company names drift
  between the scrape that saved the lead and the brief written later (`pretty_company`).
- `jobs.pick` and `jobs.paste` **append** to BRIEFS.md; `jobs.scrape --cv` **overwrites** it
  (stashing to `BRIEFS.prev.md`). All three render via `tailor.brief_block`, and `jobs.brief`
  parses what they write — the field names and the `**CV:**` shape are a contract.
- **Every variant builds to the same filename** (the profile's `filename`) — what identifies a
  CV is its folder, so the file can be attached to a form without renaming it. A tailored CV
  therefore lands in `cv/out/tailored/<posting-slug>/`, one folder per application, and
  `close` prunes that folder once the CV is filed. The slug used to be a `__<slug>` filename
  suffix; `brief.parse_cv` and `brief._cvs_for` still read that older shape, so don't drop the
  fallbacks while a pre-change BRIEFS.md or CV could still be lying around. A tailored
  folder holds the `.docx` always and the `.pdf` when Word or LibreOffice exists; `close`
  files and sweeps both.
- `review.read_sheet` joins the markdown to its JSON sidecar **by URL, not by index**, so a
  section deleted or reordered by hand can't tailor a CV against a different posting. Keep
  the `- **Link:**` line in `_block()` if you change the sheet layout; it's the join key.
- **Nothing personal is hard-coded in `score.py` or `tailor.py`.** Home country, work
  authorization, relocation targets, role families, seniority bands, interests and the skill
  whitelist all come from `settings.json`. A new rule that encodes one person's search goes
  in the settings schema, not in a regex constant.
- Settings lists are **phrases**, not regexes: whole-word, `*` for any letters, `re:` for raw.
  `phrase()` refuses a backspace character, because a `\b` typed into JSON arrives as one
  and silently disables the pattern.
- `tests/test_score.py` runs against `tests/fixtures/settings_tunisia.json` (conftest sets
  `$JOBHUNT_SETTINGS`), the search the scorer was built around. `tests/test_settings.py`
  covers other personas. A scorer change must keep both green.
- `jobs.llm` reads the API key **only** from an environment variable, never from
  settings.json, so a key can't be committed with the file.
- `Scored.tailor` is filled in by `jobs.scrape`, not by `score()`. Scoring must stay
  independent of tailoring — `models` → `score` → `tailor` is the direction.
- Ashby pruning counts **every** `rejected` entry, aged-out ones included. Those were
  confirmed as real rejections (Voodoo); don't reintroduce an exemption for them.
  `Posting.countries` empty means "not stated", not "hires anywhere".
- Every value in a `targets.json` array is fetched as a board token. Annotations belong in
  the top-level `_*` keys; one placed inside an array becomes a request for a board that
  does not exist.
- The keyword block is the only length-variable part of a tailored CV, so `tailor.fit` trims
  it to hold the 2-page rule. Some variants have no room for it *at all* — `fit` dropping to
  zero keywords is expected, not a bug. Anything that stops short of an empty list will
  never converge on those.
