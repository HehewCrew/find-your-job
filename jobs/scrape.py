"""Daily job scrape: fetch -> filter against priorities -> rank -> write the review sheet.

    python -m jobs.scrape                     # fetch, rank, write TODAY_SCRAPING.md
    python -m jobs.scrape --limit 20          # show fewer in the console (the sheet is full)
    python -m jobs.scrape --sources remotive hackernews
    python -m jobs.scrape --min-score 70      # be pickier
    python -m jobs.scrape --save --cv         # old one-shot flow: straight to jobtrack + CVs

Nothing here writes jobtrack or builds a CV by default. Every qualifying lead goes to
TODAY_SCRAPING.md; you tick what is worth applying to and `jobs.pick` does the rest.
Building a CV costs a Word round-trip each, and most leads are dropped after a closer
read - so the choice comes first and the expense second.

`--limit` only shortens the console table. It used to truncate before saving too, which
silently discarded every lead past the cut - they were neither recorded nor written down.

Already-tracked roles are skipped, so running it every day surfaces only what is new.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from jobs import discover, review, settings, sources  # noqa: E402
from jobs import tailor as tl  # noqa: E402
from jobs.progress import Report, emit, printer  # noqa: E402
from jobs.score import (  # noqa: E402
    Scored,
    dedupe_key,
    rank,
)
from jobtrack.models import Application, today  # noqa: E402
from jobtrack.storage import Store, default_path  # noqa: E402

HERE = Path(__file__).resolve().parent
TARGETS = json.loads((HERE / "targets.json").read_text(encoding="utf-8"))


def collect(
    wanted: list[str] | None,
    ashby: list[str] | None = None,
    queries: list[str] | None = None,
    report: Report | None = None,
) -> tuple[list[sources.Posting], list[str]]:
    """Fetch every source in parallel. One failing board must not kill the run.

    `ashby` is the board list after jobs.discover's pruning; None means targets.json as-is.
    `queries` are the search terms for the feeds that need one (settings.search_queries).
    """
    terms = tuple(queries or sources.HIMALAYAS_QUERIES)
    jobs: dict[str, callable] = {
        "remotive": sources.remotive,
        "arbeitnow": sources.arbeitnow,
        "weworkremotely": sources.weworkremotely,
        "hackernews": sources.hackernews,
        "himalayas": lambda: sources.himalayas(terms),
        "jobicy": sources.jobicy,
    }
    for token in TARGETS.get("greenhouse", []):
        jobs[f"greenhouse:{token}"] = lambda t=token: sources.greenhouse(t)
    for token in TARGETS.get("lever", []):
        jobs[f"lever:{token}"] = lambda t=token: sources.lever(t)
    for token in TARGETS.get("ashby", []) if ashby is None else ashby:
        jobs[f"ashby:{token}"] = lambda t=token: sources.ashby(t)
    for token in TARGETS.get("workable", []):
        jobs[f"workable:{token}"] = lambda t=token: sources.workable(t)
    for token in TARGETS.get("smartrecruiters", []):
        jobs[f"smartrecruiters:{token}"] = lambda t=token: sources.smartrecruiters(t)

    import os

    adz = TARGETS.get("adzuna", {})
    app_id, app_key = os.environ.get("ADZUNA_APP_ID"), os.environ.get("ADZUNA_APP_KEY")
    if app_id and app_key:
        jobs["adzuna"] = lambda: sources.adzuna(
            app_id, app_key, adz.get("countries", []), adz.get("query", "")
        )

    if wanted:
        jobs = {k: v for k, v in jobs.items() if any(k.startswith(w) for w in wanted)}

    out: list[sources.Posting] = []
    problems: list[str] = []
    # 24, not 12: discovered Ashby boards run to four figures, each one small request.
    with cf.ThreadPoolExecutor(max_workers=24) as pool:
        futures = {pool.submit(fn): name for name, fn in jobs.items()}
        for done, fut in enumerate(cf.as_completed(futures), 1):
            name = futures[fut]
            try:
                got = fut.result()
                out.extend(got)
                mark = f"✓ {len(got)}"
            except Exception as exc:  # noqa: BLE001
                problems.append(f"{name}: {type(exc).__name__}")
                mark = "✗"
            # Emitted here, in the calling thread, never from a worker - see jobs.progress.
            emit(
                report,
                "fetch",
                f"{done}/{len(futures)} {name} {mark}",
                done=done,
                total=len(futures),
                detail=True,
            )
    return out, problems


# Boards from jobs.discover only contribute what was posted this week. Their first full
# run on 2026-09-17 put 431 leads on the sheet - two months of 2,000 companies' backlog -
# and an undecided lead comes back every day, so a backlog never clears on its own.
# Curated targets.json boards are exempt: someone chose to read those in full.
DISCOVERED_MAX_AGE_DAYS = 7


def drop_stale_discovered(postings, curated: list[str], today: date) -> list[sources.Posting]:
    keep = {t.lower() for t in curated}
    floor = (today - timedelta(days=DISCOVERED_MAX_AGE_DAYS)).isoformat()
    return [
        p
        for p in postings
        if not p.source.startswith("ashby:")
        or p.source.partition(":")[2].lower() in keep
        or not p.posted_at
        or p.posted_at >= floor
    ]


def _refresh_boards(state, polled, postings, problems, day, wanted) -> None:
    """Keep ashby_boards.json's freshness current, so pruning works between discover runs."""
    if wanted and not any("ashby".startswith(w) or w.startswith("ashby") for w in wanted):
        return
    failed = {p.split(": ")[0] for p in problems}
    by_board: dict[str, list] = {t: [] for t in polled if f"ashby:{t}" not in failed}
    for p in postings:
        token = p.source.partition(":")[2]
        if p.source.startswith("ashby:") and token in by_board:
            by_board[token].append(p)
    # Re-read rather than reuse `state`: a weekly sweep may have saved while this fetched.
    fresh = discover.load_state()
    for token, got in by_board.items():
        discover.record(fresh, token, got, day)
    discover.save_state(fresh)


def already_tracked(store: Store) -> set[str]:
    # Must use the same normalisation as the in-run deduper, or every daily run
    # re-adds jobs whose titles contain a dash or parenthetical.
    return {dedupe_key(a.company, a.role) for a in store}


def save_leads(store: Store, leads: list[Scored]) -> list[Application]:
    added = []
    for s in leads:
        p = s.posting
        app = Application(
            id=store.next_id(),
            company=p.company,
            role=p.title,
            status="wishlist",
            url=p.url,
            location=p.location or "Remote",
            notes=[
                f"score {s.score} | cv: {s.variant} | {', '.join(s.reasons)}",
                f"source: {p.source}" + (f" | posted {p.posted_at}" if p.posted_at else ""),
            ],
        )
        store.add(app)
        added.append(app)
    store.save()
    return added


def make_cvs(leads: list[Scored]) -> None:
    """Generate one CV tailored to each posting, plus a per-day briefing sheet.

    CLI helper - called only from main(), for the one-shot `--cv` flow.
    """
    from jobs import brief as br
    from jobs import tailor as tl

    tailors = [tl.tailor_for(s.posting, s.variant) for s in leads if s.variant]
    if not tailors:
        return
    print(f"\nTailoring {len(tailors)} CV(s)…")
    made = tl.build(tailors)
    unresolved = br.pending(br.BRIEFS_PATH)
    sheet = tl.write_briefs(tailors, br.BRIEFS_PATH)
    if unresolved:
        print(
            f"  note: {unresolved} unmarked brief(s) from the last run were replaced — "
            "kept in cv/out/tailored/BRIEFS.prev.md"
        )
    print(f"  {len(made)} tailored CV(s) in cv/out/tailored/")
    print(f"  briefing sheet: {tl.display_path(sheet)}")
    for t in tailors[:5]:
        gaps = ", ".join(t["gaps"][:4]) or "none"
        print(f"    · {t['company'][:28]:<28} matched {len(t['matched']):>2} | gaps: {gaps}")


def show(leads: list[Scored], limit: int) -> None:
    """The console table. CLI helper - called only from main()."""
    if not leads:
        print("No new qualifying postings today.")
        return
    w_co, w_ti = 24, 46
    print(f"\n{'#':>3}  {'SCORE':>5}  {'COMPANY':<{w_co}}  {'ROLE':<{w_ti}}  CV / SIGNALS")
    print("-" * 132)
    for i, s in enumerate(leads[:limit], 1):
        p = s.posting
        co = (p.company[: w_co - 1] + "…") if len(p.company) > w_co else p.company
        ti = (p.title[: w_ti - 1] + "…") if len(p.title) > w_ti else p.title
        print(
            f"{i:>3}  {s.score:>5}  {co:<{w_co}}  {ti:<{w_ti}}  {s.variant} · "
            f"{', '.join(s.reasons[:3])}"
        )
    print("-" * 132)


@dataclass
class ScrapeResult:
    raw: int
    problems: list[str]
    leads: list[Scored]
    sheet: Path | None
    boards: int
    pruned: int
    example_settings: bool


def run(
    *,
    sources: list[str] | None,
    min_score: int | None,
    store: Store,
    include_tracked: bool = False,
    report: Report | None = None,
) -> ScrapeResult:
    """Fetch every source, rank against settings.json, write TODAY_SCRAPING.md.

    Raises settings.SettingsError for unreadable settings. Writes nothing to jobtrack.
    """
    cfg = settings.current()
    if cfg.is_example:
        emit(
            report,
            "fetch",
            "note: scoring against jobs/settings.example.json - copy it to "
            "jobs/settings.json and make it yours, or these rankings are someone else's.",
        )
    # Ashby: the curated boards plus everything jobs.discover found, minus the pruned.
    board_state = discover.load_state()
    run_day = date.today()
    ashby, pruned = discover.ashby_plan(
        TARGETS.get("ashby", []), board_state, discover.rejections(store), run_day
    )
    emit(report, "fetch", f"Fetching sources… ({len(ashby)} Ashby boards, {len(pruned)} pruned)")
    if not board_state["boards"]:
        emit(report, "fetch", "  tip: `python -m jobs.discover` widens Ashby beyond targets.json")
    postings, problems = collect(sources, ashby, cfg.search_queries, report=report)
    raw = len(postings)
    emit(report, "fetch", f"  {raw} raw postings")
    if problems:
        emit(report, "fetch", f"  {len(problems)} source(s) unavailable: {', '.join(problems[:6])}")
    _refresh_boards(board_state, ashby, postings, problems, run_day, sources)
    postings = drop_stale_discovered(postings, TARGETS.get("ashby", []), run_day)
    seen = set() if include_tracked else already_tracked(store)

    # `exclude=` rather than filtering the result: the per-company cap inside rank()
    # must not spend a company's daily slots on roles already in jobtrack. See rank().
    leads = rank(postings, min_score=min_score, exclude=seen, settings=cfg)
    rejected = len(postings) - len(leads)
    emit(
        report,
        "rank",
        f"  {len(leads)} qualify after filtering ({rejected} filtered out or already tracked)",
    )
    for s in leads:
        s.tailor = tl.tailor_for(s.posting, s.variant)
    sheet = review.write_sheet(leads, today()) if leads else None
    if sheet:
        emit(report, "write", f"{len(leads)} lead(s) written", detail=True)
    return ScrapeResult(raw, problems, leads, sheet, len(ashby), len(pruned), cfg.is_example)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--limit", type=int, default=50, help="how many to surface (default 50)")
    ap.add_argument(
        "--min-score", type=int, default=None, help="default: scoring.min_score in settings"
    )
    ap.add_argument("--sources", nargs="*", help="restrict to these source prefixes")
    ap.add_argument("--save", action="store_true", help="record leads in jobtrack as 'wishlist'")
    ap.add_argument("--cv", action="store_true", help="build the matching CV variants")
    ap.add_argument("--json", type=Path, metavar="PATH", help="also dump results as JSON")
    ap.add_argument("--file", type=Path, default=None, help="jobtrack data file")
    ap.add_argument(
        "--include-tracked",
        action="store_true",
        help="don't skip leads already in jobtrack (e.g. to build their CVs)",
    )
    args = ap.parse_args(argv)

    store = Store(args.file or default_path())
    try:
        result = run(
            sources=args.sources,
            min_score=args.min_score,
            store=store,
            include_tracked=args.include_tracked,
            report=printer(),
        )
    except settings.SettingsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    leads = result.leads

    show(leads, args.limit)
    # --limit is a display setting. Everything that qualified goes to the sheet, so a
    # lead is never dropped just because it fell past the end of the printed table.
    if result.sheet:
        print(f"\n{len(leads)} lead(s) written to {tl.display_path(result.sheet)}")
        print("Tick the ones you want, then:  python -m jobs.pick")

    # --save/--cv keep the original one-shot behaviour for anyone who wants it, and are
    # the only things that still honour --limit as a cutoff.
    chosen = leads[: args.limit]

    if args.json:
        args.json.write_text(
            json.dumps(
                [
                    {
                        **vars(s.posting),
                        "score": s.score,
                        "variant": s.variant,
                        "reasons": s.reasons,
                    }
                    for s in chosen
                ],
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        print(f"\nJSON written to {args.json}")

    if args.save:
        added = save_leads(store, chosen)
        print(f"\nSaved {len(added)} lead(s) to {store.path} as 'wishlist'")
        print("Review with:  jobtrack list --status wishlist")

    if args.cv:
        make_cvs(chosen)

    return 0 if chosen else 1


if __name__ == "__main__":
    raise SystemExit(main())
