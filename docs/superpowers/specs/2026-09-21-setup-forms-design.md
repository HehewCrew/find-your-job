# Setup forms — design

Date: 2026-09-21. Status: decisions approved; implemented on branch `setup-forms` (branched
from `ui-daily-loop`). Sub-project 3 of 4.

## Goal

A **Setup** tab in the local UI where someone with basic IT skills creates and edits the four
personal files — `cv/profile.json`, `jobs/settings.json`, `jobs/priorities.md`, `cv/rules.md` —
without opening a text editor or knowing JSON.

## Decisions

| # | Decision |
|---|---|
| 1 | **Data-driven forms.** One generic renderer draws a form from the loaded file itself (objects, strings, numbers, yes/no, phrase lists, repeatable groups, keyed maps), with a thin Python schema of overrides: labels, choices, long-text fields, map/choice hints. Help text comes from the file's own `_note` keys. The form edits the loaded object in place, so keys it does not show survive a save. |
| 2 | **First run.** Profile: a guided start asking `cv/init.py`'s questions and reusing its `assemble()`; the full form afterwards. Settings, priorities, rules: start from the tracked example and edit. |
| 3 | **Saving.** Validate with the real loaders, write atomically (temp file + replace), keep the previous version as `<file>.bak`. An invalid save is refused with the loader's message. |
| 4 | **Checking.** "Preview CV" builds one profile variant into `cv/out/preview/` and offers Open. Cross-file problems (a settings role naming a CV variant the profile lacks) show as warnings on the Setup page, not as save blockers. |

## Architecture

- `jobs/ui/paths.py` gains `settings, settings_example, profile, profile_example, priorities,
  priorities_example, rules, rules_example, preview`.
- `cv/build.py`: `build_variant(..., out_dir=None)` so a preview or a validation build can go
  somewhere other than `OUT_DIR`.
- **`jobs/ui/setup.py`** (no HTTP):
  - `status(paths)` — per file: `exists`, `valid`, `error`; plus `warnings` (cross-file) and
    `missing` (files not yet created).
  - `load(paths, name)` — JSON files: `{data, from_example, schema}`; Markdown files:
    `{preamble, sections: [{heading, body, help}], from_example}` where `help` is the example's
    text under the same heading.
  - `save(paths, name, payload)` — JSON: `settings.from_dict()` or `check_profile()` must pass;
    Markdown: rebuilt from preamble + sections. Atomic write, `.bak` of the previous file.
  - `check_profile(profile)` — builds every variant into a temp folder; any exception is a
    `ValidationError` naming the variant; `build_variant` warnings are returned.
  - `start_profile(paths, answers)` — validates like `cv/init.py` (dates `MM/YYYY`, email,
    slugs), calls `init.assemble()`, saves. Refuses when a profile already exists.
  - `preview(paths, variant, report)` — builds one variant into `paths.preview` with the PDF
    step; returns the CV summary. Runs as a `preview` task.
  - `SCHEMAS` — overrides per file (see below).
- `api.py` / `server.py`: `GET /api/setup`, `GET /api/setup/<name>`, `POST /api/setup/<name>`,
  `POST /api/setup/profile/start`, `POST /api/setup/preview`. `GET /api/state` gains
  `setup_missing`. `/api/open` already allows `cv/out/`.
- Page: a **Setup** nav tab (marked while something is missing), opening automatically on first
  run; a file list with status and warnings; one editor per file; the guided profile start;
  Preview CV on the profile editor.

## The renderer

Types come from the data, with schema overrides winning:

| Value | Control |
|---|---|
| string | text input; textarea when longer than 80 characters or `long: true` |
| number | number input |
| boolean | checkbox |
| list of strings | textarea, one item per line (phrase lists) |
| list of objects | repeatable group: add (a blank copy of the schema template or the first item), remove, move up/down |
| object | a section; `_note` shown as help; other `_*` keys hidden and kept |
| `map: true` object | keyed entries with an editable key (skill groups, tailoring skills, variants, links) |
| `choices` | select (single) or checkboxes (`multi: true`); choices may be dynamic (`"variants"`, `"roles"`, `"links"`, `"skill_groups"`, `"interests"`) |
| `boolstr` | text accepting true / false / a name (roles' `internships`) |

Paths are addressed as arrays (`["roles", 0, "max_level"]`); a `*` in a schema path matches
any list index or map key.

## Errors and safety

- Saves go through `POST` with the page token like every other write.
- Validation failures → 400 with the loader's message (it already names the field, e.g.
  `roles[1].max_level: one of intern, junior, ...`).
- A file changed on disk since it was loaded is simply overwritten after the `.bak` is taken —
  single user, and the backup is the undo.
- `start_profile` on an existing profile → 409.

## Testing

- `tests/test_ui_setup.py`: status for missing/valid/invalid files and cross-file warnings;
  JSON load from example and from file; save round-trips unknown and `_note` keys; invalid
  settings and profiles refused with the loader's message and nothing written; `.bak` created;
  Markdown split/join round-trip; guided start builds a profile whose every variant builds;
  preview builds into `paths.preview` (with `cv_sandbox`).
- `tests/test_ui_server.py`: the setup routes, the 400 on a bad save, 409 on a second start.
- `node --check` on `app.js`. Browser click-through deferred, like sub-project 2's.

## Out of scope

Editing `targets.json`, adding CV variants by cloning (copy a block in the form instead), a
visual CV editor, multi-user editing.
