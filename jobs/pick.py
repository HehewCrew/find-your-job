"""Turn the ticked leads on TODAY_SCRAPING.md into tailored CVs and briefs.

    python -m jobs.pick                # everything ticked [x] on the sheet
    python -m jobs.pick 1 4 9          # by number, without editing the sheet
    python -m jobs.pick roblox         # or by a fragment of the name
    python -m jobs.pick --list         # what is ticked, and what is still undecided
    python -m jobs.pick --dry-run      # what would be built, building nothing

This is the expensive half of the day: one Word round-trip per CV. It runs only against
postings you have actually chosen, which is the whole point of the review sheet.

For each chosen lead it records a `wishlist` entry in jobtrack, builds the tailored CV
(trimming keywords if the posting is dense enough to spill onto a third page), and
**appends** a brief to BRIEFS.md - so picking twice in a day adds to the worklist rather
than replacing it.

Leads marked `[-]` are recorded as `withdrawn`, which is what stops them coming back:
`jobs.scrape` skips anything already in jobtrack.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from jobs import review, settings  # noqa: E402
from jobs import tailor as tl  # noqa: E402
from jobs.brief import BRIEFS_PATH  # noqa: E402
from jobs.errors import JobsError  # noqa: E402
from jobs.progress import Report, emit, printer  # noqa: E402
from jobtrack.models import Application, ValidationError, today  # noqa: E402
from jobtrack.storage import Store, default_path  # noqa: E402


def _tailor_dict(lead: review.Lead) -> dict:
    """The tailor payload for a lead, rebuilt from the JSON sidecar.

    Falls back to re-deriving it only when the sidecar has no record - a sheet edited
    into a lead that was never scraped. Without a description there is nothing to match
    keywords against, so the CV comes out untailored rather than wrongly tailored.
    """
    if lead.data.get("tailor"):
        # Copied: fit() trims this list in place, and the sidecar stays the record of
        # what the posting actually asked for.
        t = dict(lead.data["tailor"])
        t["matched"] = list(t["matched"])
        t["gaps"] = list(t["gaps"])
        return t
    return {
        "company": lead.company,
        "job_title": lead.title or lead.label,
        # The first role's CV in settings.json: a sheet edited by hand has no variant of
        # its own, and a name hard-coded here would be one your profile may not have.
        "variant": lead.data.get("variant") or settings.current().default_cv,
        "matched": [],
        "gaps": [],
        "slug": tl.slugify(lead.company, lead.title or lead.label),
        "url": lead.url,
    }


def cmd_list(leads: list[review.Lead]) -> int:
    if not leads:
        print(f"No sheet at {review.SHEET_PATH.name}. Run: python -m jobs.scrape")
        return 1
    width = max(len(lead.company) for lead in leads)
    marks = {review.TAKE: "[x]", review.SKIP: "[-]", review.PENDING: "[ ]"}
    for lead in leads:
        title = lead.title or lead.label
        print(f"  {marks[lead.mark]} {lead.index:>3}  {lead.company:<{width}}  {title[:60]}")
    tally = {m: sum(1 for x in leads if x.mark == m) for m in marks}
    print(
        f"\n{len(leads)} lead(s): {tally[review.TAKE]} ticked, "
        f"{tally[review.SKIP]} dropped, {tally[review.PENDING]} undecided"
    )
    return 0


def choose(
    leads: list[review.Lead], selectors: list[str], sheet_name: str
) -> tuple[list[review.Lead], list[review.Lead]]:
    """(taken, dropped): the selectors if given, else the `[x]` ticks; `[-]` is dropped."""
    taken = (
        review.select(leads, selectors)
        if selectors
        else [lead for lead in leads if lead.mark == review.TAKE]
    )
    dropped = [lead for lead in leads if lead.mark == review.SKIP]
    if not taken and not dropped:
        raise JobsError(
            f"Nothing ticked on {sheet_name}. Mark a heading `[x]`, "
            "or pass lead numbers: python -m jobs.pick 1 4 9"
        )
    return taken, dropped


@dataclass
class PickResult:
    taken: list[review.Lead]
    dropped: list[review.Lead]
    cvs: list[tl.CvResult]
    briefs_path: Path


def cv_line(t: dict, cv: tl.CvResult) -> str:
    """The terminal line for one built CV - also the UI's progress message.

    Precedence is the old one: a missing PDF outranks an over-long one, which outranks
    a trimmed keyword block.
    """
    note = ""
    if cv.error:
        note = f"  !! failed: {cv.error}"
    elif not cv.pdf:
        note = "  (no PDF - Word unavailable, .docx only)"
    elif (cv.pages or 0) > tl.MAX_PAGES:
        note = f"  !! {cv.pages} pages - check before sending"
    elif cv.keywords_kept < cv.keywords_wanted:
        note = (
            f"  (trimmed {cv.keywords_wanted}->{cv.keywords_kept} keywords "
            f"to hold {tl.MAX_PAGES} pages)"
        )
    return f"  · {t['company'][:26]:<26} {t['variant']:<18}{note}"


def run(
    taken: list[review.Lead],
    dropped: list[review.Lead],
    *,
    briefs: Path,
    store: Store,
    report: Report | None = None,
) -> PickResult:
    """Record the leads in jobtrack, build one CV per taken lead, append their briefs.

    A CV that fails is recorded in its CvResult.error; the others still build and still
    get their briefs - one Word crash must not lose a batch.
    """
    tailors: list[dict] = []
    for lead in taken:
        t = _tailor_dict(lead)
        store.add(
            Application(
                id=store.next_id(),
                company=lead.company,
                role=lead.title or lead.label,
                status="wishlist",
                url=lead.url,
                location=lead.data.get("location") or "Remote",
                notes=[
                    f"score {lead.data.get('score', '?')} | cv: {t['variant']}"
                    + (f" | {', '.join(lead.data.get('reasons', []))}" if lead.data else ""),
                    f"source: {lead.data.get('source', 'review sheet')}",
                ],
            )
        )
        tl.write_jd(t["company"], t["job_title"], lead.data.get("description", ""), t["slug"])
        tailors.append(t)
    for lead in dropped:
        store.add(
            Application(
                id=store.next_id(),
                company=lead.company,
                role=lead.title or lead.label,
                status="withdrawn",
                url=lead.url,
                location=lead.data.get("location") or "",
                notes=[f"dropped at review {today()} - not pursued"],
            )
        )
    store.save()

    cvs: list[tl.CvResult] = []
    built: list[dict] = []
    if tailors:
        emit(report, "tailor", f"\nTailoring {len(tailors)} CV(s)…")
    for i, t in enumerate(tailors, 1):
        emit(
            report,
            "tailor",
            f"Tailoring {i}/{len(tailors)}: {t['company']}",
            done=i - 1,
            total=len(tailors),
            detail=True,
        )
        try:
            cv = tl.fit(t, report=report)
            built.append(t)
        # build_variant raises SystemExit for a variant missing from profile.json.
        except (Exception, SystemExit) as exc:  # noqa: BLE001
            cv = tl.CvResult(t["company"], t["variant"], error=str(exc) or type(exc).__name__)
        cvs.append(cv)
        emit(report, "tailor", cv_line(t, cv), done=i, total=len(tailors))
    if built:
        tl.append_briefs(built, briefs)
    return PickResult(taken, dropped, cvs, briefs)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    ap = argparse.ArgumentParser(prog="jobs.pick", description=__doc__.splitlines()[0])
    ap.add_argument("selectors", nargs="*", metavar="N", help="lead numbers or name fragments")
    ap.add_argument("--list", action="store_true", help="show the sheet's current marks")
    ap.add_argument("--dry-run", action="store_true", help="report without building anything")
    ap.add_argument("--sheet", type=Path, default=review.SHEET_PATH)
    ap.add_argument("--data", type=Path, default=review.DATA_PATH)
    ap.add_argument("--briefs", type=Path, default=BRIEFS_PATH)
    ap.add_argument("--file", type=Path, default=None, help="jobtrack data file")
    args = ap.parse_args(argv)

    leads = review.read_sheet(sheet=args.sheet, data=args.data)
    if args.list:
        return cmd_list(leads)
    if not leads:
        print(f"No sheet at {args.sheet.name}. Run: python -m jobs.scrape", file=sys.stderr)
        return 1

    try:
        taken, dropped = choose(leads, args.selectors, args.sheet.name)
    except JobsError as exc:
        print(exc, file=sys.stderr)
        return 1

    for lead in taken:
        print(f"  take  #{lead.index}  {lead.company} — {lead.title or lead.label}")
    for lead in dropped:
        print(f"  drop  #{lead.index}  {lead.company}")
    if args.dry_run:
        print(f"\n--dry-run: would build {len(taken)} CV(s), nothing written.")
        return 0

    result = run(
        taken,
        dropped,
        briefs=args.briefs,
        store=Store(args.file or default_path()),
        report=printer(),
    )
    built = [cv for cv in result.cvs if not cv.error]
    if built:
        print(f"\n  {len(built)} brief(s) appended to {tl.display_path(args.briefs)}")
    if dropped:
        print(f"  {len(dropped)} lead(s) recorded as withdrawn - they won't be scraped again.")
    print("\nNext: apply, then  python -m jobs.brief applied <n>")
    return 1 if len(built) < len(result.cvs) else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValidationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
