---
name: jobhunt
description: Run the daily job-hunt loop end to end — scrape, triage the leads against the priorities in jobs/priorities.md, build tailored CVs, then file the day's applications. Use when the user says "what we have today", "what do we have today", "run the job hunt", "do today's scrape", "let's do the daily loop", "close out today", or invokes /jobhunt. "What we have today" is the user's standard way to start or resume the day's loop — treat it as an invocation, not a question. Detects which phase the repo is in and resumes there.
---

# The daily loop

Six phases. **Work out which one applies before doing anything** — this skill is
invoked both at the start of a day and halfway through one.

```
scrape → triage → pick → apply → mark → close
  A        B        C      D       E      F
```

A, C, E and F are commands. **B is where you earn your keep.** D and the
confirmation before C are human gates — stop there and wait.

## Setup

Run every command with the project venv, from the repo root:

```
.venv/Scripts/python.exe -m jobs.<module>
```

Not bare `python` — the venv is where pywin32 lives, and without it CVs stop at
`.docx` with no PDF and no page-count check.

Before phase B, read these from the repo root (they are the actual decision criteria,
they change, and they are the user's own — never assume yesterday's):

- `jobs/priorities.md` — which jobs are worth applying to
- `cv/rules.md` — how the CV and application answers may be written
- `jobs/settings.json` — where they live and may work, their role families and seniority
  bands. The scorer already applied it; read it so a recommendation never contradicts it.

They are gitignored, so a fresh clone has neither. If one is missing, say so and point at
the `.example.md` beside it (`jobs/priorities.example.md`, `cv/rules.example.md`) to be
copied and rewritten. **Do not triage without them** — the score alone is not a decision
criterion, and guessing someone's constraints is how a lead they cannot take gets a CV
built for it. Phase A is still fine to run; stop at the gate.

## Phase detection

Check in this order and enter at the first match:

| Look at | If | Phase |
|---|---|---|
| `cv/out/tailored/BRIEFS.md` | has `## ` sections, some `**Status:** pending` | **E** — day in progress |
| `cv/out/tailored/BRIEFS.md` | has `## ` sections, all marked | **F** — ready to close |
| `TODAY_SCRAPING.md` | exists, dated today, has `[x]`/`[-]` marks | **C** — picks already made |
| `TODAY_SCRAPING.md` | exists, dated today, all `[ ]` | **B** — needs triage |
| `TODAY_SCRAPING.md` | missing or dated before today | **A** — fresh scrape |

Say which phase you're entering and why, in one line. If the user passed an explicit
argument (`scrape`, `triage`, `pick`, `close`, `paste`), that wins over detection.

An unclosed BRIEFS.md outranks a stale sheet: finish yesterday's applications
before scraping new leads, or `jobs.scrape --cv` semantics get confusing and the
old briefs end up in `BRIEFS.prev.md`.

---

## A — Scrape

```
.venv/Scripts/python.exe -m jobs.scrape
```

Takes a minute or two; it polls 36 company boards plus the remote job APIs. It
writes `TODAY_SCRAPING.md` and
`TODAY_SCRAPING.json` and touches **nothing** in jobtrack.

**Never pass `--save` or `--cv`.** Those are the old one-shot flow: `--save`
writes every lead to jobtrack unreviewed, and `--cv` *overwrites* BRIEFS.md and
builds a CV for all of them. Both skip the review step this whole loop exists for.

Report the funnel numbers the command prints. Expect a handful of new leads, not
dozens — 2–5 on a normal day, sometimes 0. Zero is a normal outcome, not a bug;
say so plainly and stop rather than widening `--min-score` to manufacture leads.

Then go straight into B.

## B — Triage

`score.py` ranks on the **title** plus small description bonuses. It cannot read a
JD. This phase is a second pass over the full text, and its main job is one thing:

> **Find the eligibility blockers that are invisible in the summary.** On 2026-08-04,
> four of eleven rejections were location constraints only findable in the full JD —
> Sydney relocation required, US travel required, US-only, US-only. Every one of them
> had scored well.

Read `TODAY_SCRAPING.json` (the `description` field holds ~4,000 chars per lead;
the markdown sheet does not). For each lead, propose one of:

| | Meaning |
|---|---|
| `[x]` | apply — build a CV |
| `[-]` | drop for good — never scraped again |
| `[ ]` | undecided — comes back tomorrow |

`jobs/priorities.md` decides *what* counts as a blocker. These are the shapes they
take in a JD — read them as where to look, not as the criteria themselves:

- **Work authorization** — "must be authorized to work in the US/UK/…", "no visa
  sponsorship", an employer-of-record limited to countries the user isn't in. This
  is a blocker, not a preference. "Remote (US)" almost always means exactly this.
- **Relocation required** to somewhere that isn't a target, or on-site/hybrid in a
  city they can't be in.
- **Timezone floor** they cannot cover from where they live: "4h overlap with PST",
  "US business hours".
- **Seniority mismatch, per family.** `priorities.md` gives a band per family and
  they differ — a ceiling that is right for a hobby family is wrong for the one
  they were paid to do. Never apply one band globally.
- Hard requirements plainly lacking: a named stack never used, a degree gate, a
  security clearance.

Recommend `[x]` when it matches a preferred theme or location tier and nothing
above blocks it.

Leave `[ ]` when the JD genuinely doesn't say. Undecided is a real answer; it costs
nothing and the lead returns tomorrow.

Present it as a compact table — number, company, role, score, **your mark, and a
short reason grounded in a quote or fact from the JD**, not a restatement of the
score. Flag anything where you disagree with the scorer in either direction.

**Then stop and ask for confirmation.** Do not edit the sheet or run `pick` off
your own recommendation. Offer the shortcut: they can just say "yes", or
"yes but drop 4 and 7". Also offer the no-edit path — `jobs.pick 1 4 9` takes the
numbers directly and never touches the sheet.

## C — Pick

Apply the agreed marks by editing **only the marker character** in each heading:

```
## [ ] 3. Roblox — Quality Analyst      →      ## [x] 3. Roblox — Quality Analyst
```

Nothing else in the sheet may change. In particular **never touch the
`- **Link:**` line** — it is the join key between the markdown and the JSON
sidecar, and a wrong URL tailors a CV against a different posting.

Then:

```
.venv/Scripts/python.exe -m jobs.pick --dry-run     # confirm the selection
.venv/Scripts/python.exe -m jobs.pick
```

This is the expensive half: one Word round-trip per CV. It records each `[x]` as a
jobtrack `wishlist` entry, builds the tailored CV, appends a brief to
`cv/out/tailored/BRIEFS.md`, and records each `[-]` as `withdrawn`.

Read the output for these and surface them:

- `(trimmed N->M keywords to hold 2 pages)` — normal. The keyword block is the only
  length-variable part of a tailored CV. **Trimming to zero keywords is expected on
  tight variants**, not a failure.
- `!! N pages - check before sending` — real problem, say so.
- `(no PDF - Word unavailable, .docx only)` — pywin32/Word issue, say so.

Then go into D.

## D — Apply · human gate

Hand over a worklist. Per brief: **number, company, role, the URL to open, the exact
CV file path to upload, and the gaps flagged for it** so they can prepare answers.

Then stop. Do not offer to fill in application forms, and do not mark anything
applied. Ask them to come back with what went out — "1, 2 and 5 done, 3 was US-only".

If a form asks something worth drafting, offer to write it into
`applications/<slug>/questions.md`. Re-read `cv/rules.md` first: it governs
application answers as much as the CV, and the strongest supporting evidence is
often in a portfolio or repo that this project cannot see — ask rather than invent.

## E — Mark

Translate what they report:

```
.venv/Scripts/python.exe -m jobs.brief                              # current state
.venv/Scripts/python.exe -m jobs.brief applied 1 2 5
.venv/Scripts/python.exe -m jobs.brief aborted 3 --reason "US-only"
.venv/Scripts/python.exe -m jobs.brief aborted rest --reason "…"    # everything pending
```

Indexes or name fragments both work. Always record a reason on an abort — it lands
in jobtrack as the `withdrawn` note and is the only record of why.

**Only mark what they actually told you.** Applied means the form was submitted.

## F — Close

`close` is the irreversible one: it files each applied posting, flips jobtrack,
appends to `applications/LOG.md`, **deletes the tailored CVs** and empties
BRIEFS.md. A CV is rebuilt from its brief, and closing empties the briefs — after
this, they cannot be regenerated.

So always:

```
.venv/Scripts/python.exe -m jobs.brief close --dry-run
```

show what it will do, get a yes, then:

```
.venv/Scripts/python.exe -m jobs.brief close
```

If it refuses because briefs are unmarked, **go back to E** — do not reach for
`--force`, which discards them. Only use `--force` if the user asks for it after
being told what it drops.

Close out with `.venv/Scripts/jobtrack.exe stats` and a two-line summary
of the day: what went out, what was dropped and why.

---

## Off-loop: a posting the boards never saw

LinkedIn, a referral, a recruiter's email. Same tailored CV, same brief, appended
to the existing worklist so it can run at any point in the day:

```
.venv/Scripts/python.exe -m jobs.paste --company Bungie --file jd.txt
.venv/Scripts/python.exe -m jobs.paste --company Bungie --file jd.txt --url https://… --save
```

Write the JD to the scratchpad and pass `--file`; don't try to pipe it on stdin.
`--company` is required unless `--url` is on the employer's own domain — an ATS
host like `boards.greenhouse.io` is refused rather than printing "Greenhouse" on
the CV.

`paste` gives a verdict before building — the rules, plus an LLM's if `settings.json`
has an `llm` block — and exits `1` on a `skip` without building anything. Report the
verdict and its reasons to the user; only rerun with `--force` when they say to build it
anyway.

Then pick up at D.

## Rules that hold in every phase

- **Standard library only** for anything you write in this repo. Ask before adding a
  runtime dependency.
- Never edit `cv/profile.json` content as part of this loop. CV *tailoring* is
  `jobs.tailor`'s job; changing the profile changes all 8 variants and several have
  no page slack left.
- Don't hand-write into BRIEFS.md beyond the `**Status:**` line. `jobs.pick`,
  `jobs.paste` and `jobs.scrape --cv` all write it and `jobs.brief` parses it — the
  field names and the `**CV:**` shape are a contract.
- If a phase fails, say so with the output and stop. Don't work around a failing
  command by doing its job by hand — a lead written to jobtrack without a CV, or a
  CV built without a jobtrack entry, is worse than a clean failure.
