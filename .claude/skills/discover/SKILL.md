---
name: discover
description: Run the weekly Ashby board discovery - find company job boards via Common Crawl, probe them, and prune the ones with no new jobs or that rejected the user twice. Use when the user says "run discovery", "discover boards", "refresh the Ashby list", "which boards are pruned", or invokes /discover. A SessionStart hook already runs it in the background on Mondays; this skill is for running it by hand or checking what that run did.
---

# Ashby discovery

`python -m jobs.discover` widens the daily scrape from the 25 curated Ashby boards in
`jobs/targets.json` to every board Common Crawl has seen (~3,700), then prunes. Details:
the "Finding Ashby boards" section of [jobs/README.md](../../../jobs/README.md).

## 1. Check what already ran

A SessionStart hook (`.claude/settings.local.json`) runs `jobs.discover --if-due` in the
background in the first chat of each week, from Monday on. Read its log first:

```bash
tail -n 20 jobs/discover.log
```

- A `weekly sweep starting` line with no `The scrape will poll` after it **and**
  `jobs/.discover.lock` present means a sweep is still running. Say so and stop. Don't
  start a second one; the lock would refuse it anyway.
- A finished sweep dated this week means there's nothing to run. Report its numbers
  (step 3) unless the user asked for a fresh sweep.

## 2. Run it

```bash
.venv/Scripts/python.exe -m jobs.discover
```

It takes about 3–5 minutes (three crawls, then ~3,700 board probes), so run it with
`run_in_background` and report when it finishes. `--indexes 1` reads only the newest crawl
if the user wants it faster. Common Crawl's index returns 502s under load. The script
retries, and a crawl it still can't read is reported as `unavailable` and skipped, not
treated as fatal.

For the plan only, with no network sweep:

```bash
.venv/Scripts/python.exe -m jobs.discover --list
```

## 3. Report

Give the user:

- boards found per crawl, and the probe tally (`live`, `empty`, `gone`, `failed`);
- **how many boards the scrape will poll, and how many were pruned, by reason**;
- any **curated** board or **rejected-twice** company in the pruned list, by name. `--list`
  prints those. These are the ones the user may want to argue with.

Don't edit `targets.json` to "fix" a pruned board. Pruning is a skip, not a deletion, and
the board comes back on its own once it posts again.
