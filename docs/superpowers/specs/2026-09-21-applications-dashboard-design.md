# Applications dashboard — design

Date: 2026-09-21. Status: decisions approved; implemented on branch `applications-dashboard`
(branched from `setup-forms`). Sub-project 4 of 4.

## Goal

The **Applications** tab does everything the `jobtrack` CLI does — list, filter, search, show,
add, update, note, delete, stats, CSV export — so the command line is optional.

## Decisions

| # | Decision |
|---|---|
| 1 | Review-style split: filterable list left (status, search, Open only, Quiet only), the selected application right (fields, notes, actions); stats as a summary line above. |
| 2 | CSV export is a browser download of the current filtered list. |
| 3 | An open application untouched for 14+ days is **quiet**: badged, filterable, and offered **No reply** (→ rejected, noted) and **Heard back** (→ screening, noted). |

Carried over: delete asks for confirmation; standard library only; the store stays
`applications.json`, shared with the CLI and the daily loop.

## Changes

- `jobtrack/models.py` — `Application.update(**fields) -> list[str]`: the one place field edits
  are validated (company and role non-empty after stripping, status normalised, date checked,
  unknown fields refused); touches on change. `Application.is_quiet(reference=None)` and
  `QUIET_DAYS = 14`, measured from `updated_at`. Validation stays in models, per CLAUDE.md.
- `jobs/ui/api.py` — `apps_list(paths, statuses, open_only, quiet, q)` returning the rows
  (newest update first) and stats (total, open, closed, per status, quiet, offer rate of decided);
  `app_get`, `app_add`, `app_update`, `app_note`, `app_delete`, `app_quick(action)`, `apps_csv`
  (via `report.to_csv`). An unknown id raises `Missing` → 404. Writes are refused (409) while a
  **build** task runs, since `pick.run` saves the store from its worker thread.
- `jobs/ui/server.py` — `GET /api/applications[.csv]?status=&open=&quiet=&q=`,
  `GET /api/applications/<id>`, `POST /api/applications` (add), `POST /api/applications/<id>`
  (update), `.../note`, `.../delete`, `.../quick`. The CSV is served with
  `Content-Disposition: attachment; filename="applications-<date>.csv"`.
- `jobs/ui/static/apps.js` — the tab.

## Testing

`tests/test_models.py` (update validation, quiet), `tests/test_ui_api.py` (each function,
filters, stats, quick actions, the build lock), `tests/test_ui_server.py` (routes, 404, CSV
headers, delete). `node --check`; browser check with the parked one.

## Not changed

The CLI's `update` still sets fields without `Application.update`'s checks; switching it over
is a one-line follow-up, left out to keep this branch to the UI.
