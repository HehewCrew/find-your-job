# CV generator

All CV variants are generated from one file. Edit `profile.json`, never the
generated documents — regenerating overwrites them.

## First run

`profile.json` holds phone numbers, an email and credential IDs, so it is gitignored and a
fresh clone does not have one. Make yours from the CV you already have:

```bash
python cv/init.py              # interview -> cv/profile.json
python cv/init.py --force      # start over, replacing an existing profile
python cv/init.py --out x.json # write elsewhere
```

It asks for contact details, a headline and summary, your skill groups, one block per role
with its bullets, education, certifications and languages — then writes a profile that
`build.py` renders immediately. Nothing is written until the last question, so Ctrl-C is
free.

Copy `rules.example.md` to `rules.md` at the same time. `profile.json` holds the facts;
`rules.md` holds the rules for how they may be written — claims you will not make, and the
page limit. It is gitignored too, and the `/jobhunt` loop reads it before tailoring a CV or
drafting an application answer.

It creates **one** variant. That is deliberate: the eight in this repo grew one tailored
application at a time, and inventing eight up front just yields eight copies of the same
CV. Add the next one by copying its block in `profile.json` once you have a second audience
— [How a variant is assembled](#how-a-variant-is-assembled) below is the reference.

**One catch if you use the daily loop.** Each role in `jobs/settings.json` names the
variant it is built with (`cv`, plus any `cv_rules`). A name your profile does not have
stops the build on the first posting routed to it — so point every role at the variants you
actually keep. With the one variant `init.py` writes, that means every role's `cv` is that
one name until you add a second.

`cv/profile.example.json` is also the fastest way to see every optional key — merged role
blocks, per-variant density, dropping keyword lines or certificates — since `init.py` only
asks about the common ones.

## Building

```bash
python cv/build.py              # all variants -> .pdf
python cv/build.py --keep-docx  # keep the intermediate .docx as well
python cv/build.py --no-pdf     # .docx only (no Word required)
python cv/build.py sdet ai_qa   # just these variants
python cv/build.py --list       # show variant keys
python cv/build.py --clean      # wipe cv/out first
```

Output lands in `cv/out/<folder>/<filename>.pdf`.

Every variant carries the **same** `filename` — the file attached to an application form
should read as a CV, not as a build artefact. What tells them apart is the folder. Tailored
builds (`jobs.pick`, `jobs.paste`) follow the same rule and go to
`cv/out/tailored/<posting-slug>/<filename>.pdf`, one folder per application, so a CV is
ready to send without renaming it first.

No third-party packages. The `.docx` is an intermediate — a zip of XML parts, written here
with `zipfile` + stdlib XML — and it is deleted once its PDF exists. PDF export drives the
installed Microsoft Word over COM (`pywin32`), so the PDF is exactly what Word's own
*Save as PDF* produces. A variant whose conversion fails keeps its `.docx`, so a Word
hiccup never leaves you with nothing.

## How a variant is assembled

Facts live once. Each experience bullet carries a `tags` list, and a variant pulls only the
bullets tagged with its own key:

```json
{ "text": "Build and maintain n8n automation pipelines…", "tags": ["automation_qa", "ai_automation"] }
```

So `capgemini_bmw` is one role entry, but the Data Analyst CV shows its analytics bullets
while the SDET CV shows its testing bullets. Dates, employers and contact details exist in
exactly one place — they cannot drift between versions.

A variant declares: `headline`, `summary`, which `skill_groups` to print, which `roles` to
list, and optionally `creative`, `community`, and a `closing` pitch paragraph.
`title_overrides` lets one role display a different job title per variant.

`{years}` and `{ai_tools}` in a summary are substituted from `meta`, so bumping years of
experience is a one-line change.

## Guardrails

- **Empty-role warning.** If a variant lists a role but no bullet is tagged for it, the role
  would silently vanish and leave an unexplained gap in the timeline. The build prints a
  loud `!` warning instead. Do not ignore it.
- **`draft: true`** on a role excludes it from every build — use it for work you haven't
  finalised the wording on yet.
- **`_TODO` keys** mark facts awaiting confirmation. Search for them before sending anything.

## Two layout rules, enforced in code

**Never exceed 2 pages.** Word is the only thing that knows how the document really lays
out, so `to_pdf()` asks it for the page count during conversion and prints a loud `!!`
warning naming any CV over `MAX_PAGES`. Nothing over-length ships silently.

**Never end a page on a bare heading or job title.** Every heading, job title, education
entry and skill-group title carries `w:keepNext` + `w:keepLines`, so Word moves it to the
next page rather than stranding it with nothing underneath.

The two can fight: `keepNext` costs a page whenever a heading gets pushed over, which took
`automation_qa` from 2 pages to 3. That is what the per-variant `density` override is for.

## Layout tuning

Page count is controlled from config without touching code:

| Key | Effect |
|---|---|
| `meta.density` | Scales all vertical spacing. `0.9` is current; lower to compress. |
| `variants.<key>.density` | Overrides `meta.density` for one variant only. Prefer this — tightening `meta` degrades the CVs that already fit. |
| `meta.margin_twips` | Page margin (1440 = 1 inch). Currently 850. |
| `meta.font_size_pt` | Body text size. |
| `meta.name_size_pt` | Name in the header. |

All eight variants fit two pages. Two needed help: `automation_qa` takes `"density": 0.8`,
and `game_designer` — the longest, carrying a creative portfolio *and* community
involvement on top of the employment history — needed the three keys below.

## Shortening a variant without deleting facts

| Variant key | Effect |
|---|---|
| `merge_roles` | Collapse several roles at one employer into one dated entry. Bullets still live once in `roles`; only the grouping changes. |
| `show_keywords: false` | Drop the per-role `Keywords:` line. It is ATS keyword surface — worth a line on a QA CV, dead weight on a design one. |
| `show_certifications: false` | Drop the certificate list, keep the languages. The heading becomes just `Languages`. |

`merge_roles` derives the date span from its constituents (earliest start, latest end, or
open-ended if any role is still running) and unions their keywords:

```json
"merge_roles": [
  { "id": "capgemini_all",
    "ids": ["capgemini_ai_agent", "capgemini_bmw", "capgemini_cariad"],
    "title": "AI Test Engineer / Automation Engineer / Defect Manager",
    "org": "Capgemini — for Cariad & BMW" }
]
```

> **The merged `title` must be real.** List the actual titles held, as above. Inventing one
> unifying title that appears on no contract and no LinkedIn profile is the kind of claim
> that fails a reference check — the opposite of what the rest of this CV is built for.

Measured on `game_designer`: merging alone left it at 3 pages, and so did dropping
certifications alone. Only all three together reached 2.

> `pywin32` is not in the project `.venv`. Building there skips the PDF step **and the page
> check** — you get `.docx` only and no warning about length. Use a Python that has it.

## Conventions baked in

- No quantified task metrics (test-case counts, percentage gains) — deliberately removed so
  nothing invites a "prove it" question.
- No LLM version numbers. Model names age badly and a wrong one destroys credibility on an
  AI-focused CV; `meta.ai_tools` holds the single canonical phrasing.
- Every variant carries at least a LinkedIn hyperlink.

## Migrated

The old `CV_README.md` (the job-description decision guide, `src/CVs/`) was the manual
predecessor of this toolchain: paste a JD into a chat, get an apply/don't-apply call. It was
removed on 2026-09-09 — its criteria live in `jobs/priorities.md` and its CV-selection logic
in `jobs/score.py:pick_variant`, both of which `/jobhunt` runs automatically.
