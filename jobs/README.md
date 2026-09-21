# Daily job scrape

> Prefer a page to the command line? `python -m jobs.ui` runs this whole loop in your
> browser - see the [main README](../README.md#using-the-web-page).

Fetches postings from public job APIs, filters them against **your** settings
([settings.json](#making-it-yours)), ranks what survives, and writes them to a review sheet
to choose from.

```bash
python -m jobs.scrape                      # fetch, rank, write TODAY_SCRAPING.md
python -m jobs.scrape --limit 20           # show fewer in the console (the sheet is full)
python -m jobs.scrape --min-score 35       # widen the net (default: scoring.min_score)
python -m jobs.scrape --sources remotive hackernews
python -m jobs.scrape --json today.json    # machine-readable dump
```

Leads already in `jobtrack` are skipped, so a daily run only surfaces what is new.

> `--limit` only shortens the printed table. It used to truncate before saving as well,
> which silently discarded every lead past the cut — they were neither recorded nor
> written down anywhere. The sheet always holds everything that qualified.

## Choosing what to apply to — `jobs.pick`

The scrape writes **`TODAY_SCRAPING.md`** at the repo root: one section per lead with its
score, location, matched skills and gaps. Nothing is in `jobtrack` yet and no CV has been
built. Tick the headings worth applying to, then:

```bash
python -m jobs.pick                # everything ticked [x]
python -m jobs.pick 1 4 9          # or by number, without editing the file
python -m jobs.pick roblox         # or by a fragment of the name
python -m jobs.pick --list         # what is ticked, dropped, still undecided
python -m jobs.pick --dry-run      # what would be built
```

| Mark | Meaning |
|---|---|
| `[ ]` | undecided — comes back in tomorrow's scrape |
| `[x]` | take it — jobtrack `wishlist`, tailored CV, brief appended to BRIEFS.md |
| `[-]` | drop it — jobtrack `withdrawn`, so it is never scraped again |

**Why the split.** Building a CV costs a Word round-trip each, and most leads get dropped
after a closer read. Deciding first and building second means that cost is only paid for
postings actually going somewhere.

`jobs.pick` **appends** to BRIEFS.md, so picking twice in a day adds to the worklist
instead of replacing it.

> Two files are written, on purpose. `TODAY_SCRAPING.md` is the decision surface — only
> what you need to judge a posting. `TODAY_SCRAPING.json` is the data record, holding the
> full 4,000-character description each CV is tailored against. They are joined **by URL**,
> so re-ordering or deleting sections in the markdown by hand is safe.

The old one-shot flow still works: `python -m jobs.scrape --save --cv` goes straight to
jobtrack and builds a CV for every lead, skipping the review step.

## A posting you found yourself — `jobs.paste`

The scrape only sees the feeds and boards listed under [Sources](#sources). For anything else — LinkedIn, a
referral, a recruiter's email — paste the description in. It tells you whether to apply,
and why not if not, then builds the same tailored CV and the same brief:

```bash
python -m jobs.paste --company Bungie             # then paste, Ctrl-Z (Win) / Ctrl-D
python -m jobs.paste --company Bungie --file jd.txt
python -m jobs.paste --company Bungie --file jd.txt --dry-run    # the verdict only
python -m jobs.paste --company Bungie --file jd.txt --force      # build even on a skip
python -m jobs.paste --company Bungie --file jd.txt --no-llm     # rules only
python -m jobs.paste --company Bungie --file jd.txt --variant sdet
python -m jobs.paste --company Bungie --file jd.txt --url https://... --save
```

The role title is guessed from the text (skipping "Apply now"-style furniture); pass
`--title` when the guess is wrong. `--company` is required unless `--url` points at an
employer's own domain — an ATS host like `boards.greenhouse.io` is refused rather than
printing "Greenhouse" on the CV.

### The verdict: apply, consider, or skip

Before anything is built, the posting is judged twice:

- **The rules** — the same eligibility, role and seniority checks the scrape runs against
  your settings. Reliable for what a title and a location line state ("US only", a
  country lock, a senior title above your band); blind to anything buried in the text.
- **An LLM, if you set one up** — it reads the *whole* posting against your settings,
  `priorities.md` and your CV's summary and skills, and answers apply / consider / skip
  with reasons, blockers, gaps to prepare for, and questions to ask. This is what catches
  a tester role that needs a native language you don't speak, which the rules score highly.
  On a terminal you can then keep asking it about the posting before deciding.

A **skip** stops there and says why. Answer yes at the prompt, or pass `--force`, to build
the CV anyway — you found the posting, the verdict only advises. Where the model and the
rules disagree the model's call wins, since it read the whole text, and the disagreement is
printed. `--save` records the verdict, and whether you overrode it, in the jobtrack note.

Exit codes: `0` built (or `--dry-run`), `1` skipped, `2` bad input.

**Setting up the LLM** — optional, off by default. Add an `llm` block to `settings.json`
and put the key in an environment variable; it is never read from a file:

| Provider | settings.json | Key from |
|---|---|---|
| Anthropic | `{"provider": "anthropic"}` — model defaults to `claude-opus-5` | `ANTHROPIC_API_KEY` |
| OpenAI | `{"provider": "openai", "model": "<model id>"}` | `OPENAI_API_KEY` |
| OpenAI-compatible — OpenRouter, Groq, Mistral, a local Ollama | `{"provider": "openai-compatible", "base_url": "...", "model": "...", "api_key_env": "MY_KEY"}` | the variable `api_key_env` names (none needed for localhost) |

Each call sends the posting, a summary of your settings, `priorities.md` and your CV's
summary and skills to that provider, and costs a few cents. It is plain HTTPS through the
standard library — no SDK to install.

The **brief is appended**, not rewritten — a pasted posting usually turns up midway through
the day's list, and overwriting would discard the marks already made.

If the matched keywords push the CV past two pages, they are dropped a few at a time until
it fits, and the command says how many survived. On a variant with no slack the block comes
off entirely — it will tell you when that happens.

## Working through the day — `jobs.brief`

`--cv` writes `cv/out/tailored/BRIEFS.md`, one section per posting. That file is the day's
worklist: mark each posting `applied` or `aborted`, then `close` files them.

```bash
python -m jobs.brief                          # the day's briefs and where each one stands
python -m jobs.brief applied 3                # by index, or by a fragment of the name
python -m jobs.brief applied anthropic 7      # several at once
python -m jobs.brief aborted 5 --reason "US-only"
python -m jobs.brief aborted rest             # everything still pending
python -m jobs.brief close --dry-run          # what close would do
python -m jobs.brief close
```

Marking only rewrites a `**Status:**` line, so editing BRIEFS.md by hand works just as well.

`close` requires every brief to be marked (`--force` overrides, discarding the unmarked) and
then, for each one applied:

- creates `applications/<posting-slug>/` holding the tailored CV (the `.docx`, and the
  `.pdf` when Word or LibreOffice made one), the full job description
  (`jd.md`, if one was saved — see below), and a `questions.md` for whatever the application
  form asked.
- flips the `jobtrack` entry to `applied`, dated when you marked it, with the CV noted.
- appends the day to `applications/LOG.md`, newest first — the record of what went out.

`jobs.pick` and `jobs.paste` both save the full posting text as `jd.md` next to the tailored
CV, in `cv/out/tailored/<slug>/`, the moment the CV is built — `TODAY_SCRAPING.json` is
overwritten by the next day's scrape, and a hand-pasted JD was never kept anywhere at all
before this, so either source loses the description for good unless it's copied out. `close`
carries `jd.md` across into `applications/<slug>/` alongside the CV, then deletes the
`tailored/` copy so the folder can still be pruned once emptied — the same "keep only the
sent copy" rule the CV already follows.

Aborted postings become `withdrawn` in `jobtrack` with the reason. If one was never saved as
a lead, an entry is created anyway: `already_tracked()` reads the store, so without it the
posting comes straight back in tomorrow's scrape.

Then the tailored CVs are cleared and BRIEFS.md is emptied, ready for the next run. If a
scrape overwrites a sheet you never closed, the old one is kept as `BRIEFS.prev.md`.

### Why clearing the CVs is safe

A tailored CV is rebuilt from its brief, and closing empties the briefs — so after `close`
the CV cannot be regenerated. Two rules make the sweep safe:

- An applied brief's CV is deleted **only after** its copy under `applications/<slug>/` is
  confirmed on disk. That copy is the record of what was actually sent.
- Only the generated locations are touched: the posting's own `tailored/<slug>/` subfolder
  (emptied, then removed), or the old flat `*__<slug>.pdf` name. A CV you put in
  `cv/out/tailored/` by hand sits at the top level under another name, matches neither, and
  is never deleted.

`--keep-cvs` skips the sweep entirely.

### `applications/` is a working folder, not an archive

Answers are normally drafted there *before* the form is submitted, so the folder usually
exists by the time `close` runs. An existing `questions.md` is never overwritten — `close`
just notes "answers on file" in the log. To start one early, create the folder yourself.

> Gitignored: salary expectations, personal reasoning, and the CV with your phone number on
> it. Don't confuse it with `applications.json`, which is the jobtrack store.

## Sources

All are public APIs, official board endpoints, or RSS. **No scraping, no browser
automation.** LinkedIn and Indeed are deliberately excluded: both block automated access and
ban accounts for it, and your LinkedIn account is worth more than the extra listings.

| Source | Notes |
|---|---|
| Remotive, We Work Remotely | Remote-first boards, free APIs/RSS |
| Himalayas, Jobicy | Cross-company remote feeds. Himalayas is searched per role family (last 14 days); Jobicy gives its newest 100 |
| Arbeitnow | EU roles with an explicit **visa-sponsorship flag** |
| Hacker News "Who is hiring" | Free Algolia API; strong for AI startups |
| Greenhouse / Lever | Per-company public JSON; edit `targets.json` |
| Ashby | `targets.json` **plus** every board `jobs.discover` found, minus the pruned ones |
| Workable / SmartRecruiters | Same, for the Gulf's tech employers. SmartRecruiters costs one request per posting — small boards only |
| Adzuna | Optional. Set `ADZUNA_APP_ID` + `ADZUNA_APP_KEY`, else skipped |

Add or remove companies in [targets.json](targets.json). Board tokens go stale in both
directions, so probe before trusting a comment: huggingface, cohere, hashicorp, sentry and
unity 404 on Greenhouse, while **openai moved *to* Ashby** and is now the largest single
board in the list. Every entry there was verified live on 2026-08-04.

**Country-restricted remote.** Himalayas and Jobicy say where a role hires as data, not
prose. A list naming none of your countries or regions ("Remote - United States" when you
are not American) is dropped — unless it names a country in your `relocation_targets` or
`accept_country_locks`, in which case it stays on the sheet with the lock penalty.

### Finding Ashby boards — `jobs.discover`

Ashby has no search across companies, so a board can only be read if its token is known.
`jobs.discover` gets tokens from Common Crawl's public URL index (about 2,000 on
2026-09-17, against 25 curated), probes each one, and writes `ashby_boards.json`
(gitignored). Run it **weekly**:

```bash
python -m jobs.discover             # crawl + probe everything (a few minutes)
python -m jobs.discover --list      # what the scrape will poll, and what is pruned and why
```

It will never be all of Ashby. VA4U's board wasn't in any crawl, so `jobs.paste` is still
how those postings get in.

A board is **pruned**, meaning skipped rather than deleted, when:

- it has had **no new posting in 30 days**, or no postings at all, or its board is gone;
- that company has **rejected you more than once**. Every `rejected` entry in jobtrack
  counts, aged-out ones included.

A SessionStart hook runs the sweep in the background: in the first chat opened on a Monday,
or in the first chat after that if no chat was opened on Monday. Its output goes to
`jobs/discover.log`. `/discover` runs it by hand.

A discovered board only adds roles **posted in the last 7 days**. Without that, the first
run put 431 leads from old backlogs on the sheet. Curated boards are read in full.

The pruning rules apply to curated `targets.json` boards too. Each scrape updates the freshness
dates, and each discover run re-probes pruned boards, so one that starts hiring again
comes back.

## How ranking works

`score.py` holds the mechanics; **what you want is data**, in `jobs/settings.json`. In order
of weight:

1. **Eligibility — a hard filter.** A posting locked to a country you cannot work in, a
   region you are not in, or a working timezone too far from yours is dropped or heavily
   penalised. "Remote (US)" almost always means *authorized to work in the US* — a wall for
   anyone who is not, and no lock at all for someone who is.
2. **Location, in tiers** — fully remote anywhere, then remote with hours to keep, then plain
   remote, then on-site where you live (or dropped, if leaving is the point), then on-site in
   a relocation target, then anywhere else. The tiers are spaced wider than any bonus, so
   location decides the order and everything else sorts *within* a tier.
3. **Role families** — the title must match one of your `roles` or the posting is dropped.
   Each family carries its own points, CV variant and seniority ceiling.
4. **Interests** — themes you want more of (AI, gaming, climate…), bigger when they are in
   the title.

**Seniority is judged per family, never globally.** Five years of paid QA makes "Senior QA
Engineer" credible; a portfolio of hobby games does not make "Senior Game Designer"
credible. So each role has its own `max_level`, and above it the posting is either penalised
or — with `"above_level": "reject"` — dropped. A lead you cannot win is worse than no lead:
it costs a slot on the sheet.

Two gates run before scoring: `exclude_titles` drops other professions anywhere in the
title, and `exclude_departments` drops business functions at the head of it ("Data
Analyst, Marketing" is a data job; "Marketing Manager" is not yours). A role's
`allow_excluded` lets an adjacent stack back in — "QA/DevOps Engineer" is still QA.

> **Studios write "Designer", not "Design".** A phrase closes on a word boundary, so
> `"game design"` never matches "Game Designer" — the "e" after "design" defeats it. Write
> `"game design*"`. This exact bug hid every "Gameplay Designer" from the scrape for weeks.

> **Classification reads the title, not the description.** An early version matched keywords
> against the whole 4,000-character description, and nearly everything came back looking like
> a gaming or data job. The description only contributes small bonus points and can never
> decide what a job *is*.

Each lead is tagged with the CV variant its role — or one of the role's `cv_rules` — names.

## Making it yours

Three files, all gitignored, each with a tracked example beside it:

```bash
cp jobs/settings.example.json jobs/settings.json      # what the scrape ranks for
cp jobs/priorities.example.md jobs/priorities.md      # the judgement a rule cannot hold
cp cv/rules.example.md cv/rules.md                    # what may be written onto the CV
```

Until `settings.json` exists the scrape uses the example, says so on every run, and ranks
leads for the made-up person in it — a QA engineer in Lisbon.

**`settings.json`** — every section is annotated in the example. Work down it once:

| Section | What it decides |
|---|---|
| `you` | Where you live, where you may work without sponsorship (`"EU"` and `"EEA"` expand to their members), the region words that include you, your UTC offset and years of experience. |
| `location` | Whether an office job at home is fine or the thing you are escaping; relocation targets (cities, or a whole country); countries whose "hires only in" lists you still want to see; the timezone gap you can cover. |
| `roles` | **The most important one.** The job titles you would take, best first — each with its CV variant, points, seniority ceiling and whether internships count. A title matching none of them is dropped before scoring. |
| `exclude_titles`, `exclude_departments` | Professions and functions to drop outright. |
| `interests`, `description_bonus` | What earns extra points. |
| `search_queries` | What to search for on the boards that need a query. |
| `scoring` | The bar for the sheet, and how many leads one employer may take. |
| `tailoring` | The skills a tailored CV may headline (else your profile's short skill names), known gaps, and skills too thin to headline alone. |
| `llm` | Optional — see [the verdict](#the-verdict-apply-consider-or-skip). |

Every list of words is a list of **phrases**, matched case-insensitively on whole words:
`"qa"` matches "QA Engineer" but not "Qatar"; `*` is any letters; a phrase starting `re:` is
a raw regular expression. In JSON a regex word boundary needs two backslashes — one
backslash-b is a backspace character, and the loader refuses it rather than let it
silently disable the pattern.

Every `cv` a role names must be a variant in your `cv/profile.json`. `cv/init.py` makes one
variant; point every role at it until you add more.

[targets.json](targets.json) is the list of company boards polled directly — replace it
with employers you want to work for.

`tests/test_score.py` pins the scorer's behaviour against a fixed persona
(`tests/fixtures/settings_tunisia.json`), and `tests/test_settings.py` checks it for people
who live elsewhere and want other things. A change to the mechanics shows up as a failing
test naming the case it broke. Read the failure before assuming it is wrong: some of those
tests exist because a regex silently matched nothing for weeks.

## Expect a handful of leads/day, not dozens

A measured run (2026-08-04, before the board list was widened):

```
raw postings              3129
in-run duplicates          829   same role posted per-country
after dedupe              2300
  ✗ not a target role family    1196
  ✗ different profession        1072
passed the role gate        31   1.3%
  below min_score=45          19
above threshold             12
  already tracked            12
NEW today                    3
```

Two things that follow from those numbers, and are easy to get wrong:

- **`min_score` is not the bottleneck.** Only 4 postings sat in the 25–44 band. Lowering the
  threshold buys almost nothing; the role gate is what decides the yield.
- **The board list is the real constraint.** 2,268 of 2,300 die at the role gate, and most of
  that is legitimate — Anthropic alone posts ~390 roles, nearly all backend or research. More
  boards is the only lever that produces more *good* leads.

So: add companies to `targets.json`, and run it daily — the leads are *new* each day. Use
`jobs.paste` for anything the boards can't see.
