"""Judge a job description pasted by hand, then tailor a CV to it.

    python -m jobs.paste --company Acme --title "QA Engineer"      # paste, then Ctrl-Z / Ctrl-D
    python -m jobs.paste --file jd.txt                             # read it from a file
    Get-Content jd.txt | python -m jobs.paste --company Acme       # or pipe it in
    python -m jobs.paste --file jd.txt --dry-run                   # the verdict only
    python -m jobs.paste --file jd.txt --force                     # build even on a "skip"
    python -m jobs.paste --file jd.txt --variant qa_gaming         # override the CV choice
    python -m jobs.paste --file jd.txt --url https://... --save    # also track it

The daily scrape only sees the boards in targets.json. This is the escape hatch for
everything else - a posting from LinkedIn, a referral, an email from a recruiter - so the
same tailoring and the same BRIEFS.md workflow apply to it.

It says whether to apply before it spends anything:

  * **The rules** - the same eligibility and seniority checks the scrape runs (score.py
    against your settings.json). They catch what a title and a location line state.
  * **An LLM, if you configured one** (settings.json `llm`, see jobs/llm.py). It reads
    the whole posting against your settings, priorities.md and CV, which is where the
    blockers a regex cannot see live - a required language, a residency clause buried in
    paragraph six. On a terminal you can then keep asking it about the posting.

A "skip" stops there, with its reasons. `--force` (or answering yes at the prompt) builds
the CV anyway: you found this posting yourself, and the verdict advises, it does not own
the decision. Where the model and the rules disagree, the model wins - it read the whole
text - and the disagreement is printed.

The brief is *appended*. A pasted posting turns up midway through working the day's
scrape, and overwriting BRIEFS.md would discard the marks already made.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from jobs import llm, settings  # noqa: E402
from jobs import tailor as tl  # noqa: E402
from jobs.brief import BRIEFS_PATH  # noqa: E402
from jobs.score import Scored, _families, pick_variant, score  # noqa: E402
from jobs.sources import Posting  # noqa: E402
from jobtrack.models import Application, ValidationError  # noqa: E402
from jobtrack.storage import Store, default_path  # noqa: E402

# Lines job boards leave in a copy-paste that are never the job title.
_NOISE = re.compile(
    r"^(apply|apply now|share|save|back to|posted|about the role|about us|job description"
    r"|full[- ]time|part[- ]time|contract|remote|hybrid|on-?site)\b",
    re.I,
)


def profile() -> dict:
    """Your profile.json, or the example when you have none yet."""
    return tl._profile()


def variants() -> list[str]:
    """The CV variants --variant may name: whatever profile.json defines."""
    return sorted((profile().get("variants") or {}).keys())


def read_text(path: Path | None) -> str:
    if path:
        return path.read_text(encoding="utf-8", errors="replace")
    if sys.stdin.isatty():
        eof = "Ctrl-Z then Enter" if sys.platform == "win32" else "Ctrl-D"
        print(f"Paste the job description, then press {eof}:\n", file=sys.stderr)
    return sys.stdin.read()


def guess_title(text: str) -> str:
    """First line that looks like a role title rather than board furniture."""
    for raw in text.splitlines():
        line = raw.strip(" \t#*-—–|")
        if 3 <= len(line) <= 90 and not _NOISE.match(line) and not line.endswith((".", ":")):
            return line
    return ""


# Hosts that identify the ATS, not the employer - the company name is elsewhere in
# the URL, so guessing from the host would put "Greenhouse" on the CV.
_ATS_HOSTS = {
    "greenhouse",
    "lever",
    "ashbyhq",
    "workday",
    "myworkdayjobs",
    "smartrecruiters",
    "workable",
    "teamtailor",
    "recruitee",
    "bamboohr",
    "jazzhr",
    "linkedin",
    "indeed",
    "glassdoor",
    "wellfound",
    "otta",
    "welcometothejungle",
}


def company_from_url(url: str) -> str:
    """Best-effort employer name from a careers URL. Empty when it would be a guess."""
    match = re.match(r"https?://([^/]+)", url.strip())
    if not match:
        return ""
    host = re.sub(r"^(www|jobs|boards|boards-api|careers|apply|job-boards)\.", "", match.group(1))
    name = host.split(".")[0]
    return "" if name.lower() in _ATS_HOSTS else name.replace("-", " ").title()


def analyse(
    text: str, company: str, title: str, url: str, location: str, variant: str
) -> tuple[dict, Posting]:
    posting = Posting(
        source="paste",
        company=company,
        title=title,
        url=url,
        location=location,
        # Same cap as the scraped feeds, so a pasted posting and a scraped one match the
        # same keywords. The LLM, when there is one, reads the full text.
        description=text[:4000],
    )
    if not variant:
        variant = pick_variant(_families(title.lower()), title.lower(), text.lower(), company)
    return tl.tailor_for(posting, variant), posting


def rules_verdict(scored: Scored, min_score: int) -> llm.Verdict:
    """What the scrape filter would have decided, phrased as a verdict."""
    if not scored.ok:
        return llm.Verdict("skip", blockers=[scored.rejected])
    blockers = [r for r in scored.reasons if r.startswith("eligibility(")]
    if scored.score < min_score:
        return llm.Verdict(
            "consider",
            reasons=[f"scores {scored.score}, under your threshold of {min_score}"],
            blockers=blockers,
        )
    return llm.Verdict("apply", reasons=[f"scores {scored.score}: {', '.join(scored.reasons)}"])


@dataclass
class Assessment:
    tailor: dict
    posting: Posting
    scored: Scored
    rules: llm.Verdict
    model: llm.Verdict | None = None
    conversation: llm.Conversation | None = None
    llm_error: str = ""

    @property
    def verdict(self) -> llm.Verdict:
        """The model's call when there is one - it read the whole posting - else the rules'."""
        return self.model or self.rules

    @property
    def disagree(self) -> bool:
        return self.model is not None and (self.model.decision == "skip") != (
            self.rules.decision == "skip"
        )


def assess(
    text: str,
    *,
    company: str,
    title: str,
    url: str = "",
    location: str = "",
    variant: str = "",
    use_llm: bool = True,
    cfg: settings.Settings | None = None,
) -> Assessment:
    """Everything `paste` decides, without printing or building anything."""
    cfg = cfg or settings.current()
    t, posting = analyse(text, company, title, url, location, variant)
    scored = score(posting, cfg)
    a = Assessment(t, posting, scored, rules_verdict(scored, cfg.min_score))
    if not use_llm:
        return a
    try:
        client = llm.Client.from_settings(cfg)
    except llm.LLMError as exc:
        a.llm_error = str(exc)
        return a
    if client is None:
        return a
    opening = llm.opening_message(
        text,
        company=company,
        title=title,
        s=cfg,
        cv=llm.describe_cv(profile(), t["variant"]),
        rules=a.rules,
    )
    a.conversation = llm.Conversation(client, opening)
    try:
        a.model = a.conversation.verdict()
    except llm.LLMError as exc:
        a.llm_error = str(exc)
    return a


def _print_verdict(v: llm.Verdict, heading: str) -> None:
    print(f"\n  {heading}: {v.decision.upper()}")
    for label, items in (
        ("blocker", v.blockers),
        ("why", v.reasons),
        ("gap", v.gaps),
        ("ask", v.questions),
    ):
        for item in items:
            print(f"    {label:<7} {item}")


def _confirm(prompt: str) -> bool:
    try:
        return input(prompt).strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        print()
        return False


def _follow_ups(a: Assessment) -> None:
    """Keep the thread open on a terminal: ask the model anything about the posting."""
    print("\n  Ask about this posting, or press Enter to move on.")
    while True:
        try:
            question = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not question:
            return
        try:
            answer = a.conversation.ask(question)
        except llm.LLMError as exc:
            print(f"  !  {exc}")
            return
        print("\n" + "\n".join(f"    {line}" for line in answer.strip().splitlines()) + "\n")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="jobs.paste", description=__doc__.splitlines()[0])
    ap.add_argument("--file", type=Path, help="read the description from here instead of stdin")
    ap.add_argument("--company", default="", help="employer name (inferred only from --url)")
    ap.add_argument("--title", default="", help="role title (guessed from the text if omitted)")
    ap.add_argument("--url", default="", help="link to the posting")
    ap.add_argument("--location", default="", help="e.g. 'Remote' or 'Amsterdam, NL'")
    ap.add_argument(
        "--variant", default="", help="force a CV variant instead of choosing one from the title"
    )
    ap.add_argument(
        "--force", action="store_true", help="tailor a CV even when the verdict is skip"
    )
    ap.add_argument("--no-llm", action="store_true", help="rules only, even if an LLM is set up")
    ap.add_argument("--save", action="store_true", help="also record it in jobtrack as 'wishlist'")
    ap.add_argument(
        "--file-store", type=Path, default=None, dest="store_file", help="jobtrack data file"
    )
    ap.add_argument("--briefs", type=Path, default=BRIEFS_PATH, help="BRIEFS.md to append to")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="show the verdict, variant, keywords and gaps without building anything",
    )
    return ap


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    args = build_parser().parse_args(argv)
    try:
        cfg = settings.current()
    except settings.SettingsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.variant and args.variant not in variants():
        print(
            f"error: no '{args.variant}' variant in your profile.json "
            f"(have: {', '.join(variants()) or 'none'})",
            file=sys.stderr,
        )
        return 2

    text = read_text(args.file).strip()
    if len(text) < 100:
        print(
            f"error: only {len(text)} characters of job description - "
            "paste the full posting so the keyword match means something.",
            file=sys.stderr,
        )
        return 2

    title = args.title or guess_title(text)
    if not title:
        print("error: could not work out the role title; pass --title.", file=sys.stderr)
        return 2
    company = args.company or company_from_url(args.url)
    if not company:
        print("error: pass --company; it goes on the CV and names the brief.", file=sys.stderr)
        return 2

    a = assess(
        text,
        company=company,
        title=title,
        url=args.url,
        location=args.location,
        variant=args.variant,
        use_llm=not args.no_llm,
        cfg=cfg,
    )
    t = a.tailor

    print(f"\n  Company : {company}")
    print(f"  Role    : {title}")
    print(f"  CV      : {t['variant']}" + ("  (forced)" if args.variant else "  (auto)"))
    print(f"  Matched : {', '.join(t['matched']) or '— nothing above the threshold to headline'}")
    print(f"  Gaps    : {', '.join(t['gaps']) or 'none flagged'}")
    _print_verdict(a.rules, "Rules")
    if a.model:
        _print_verdict(a.model, f"LLM ({a.model.source})")
        if a.disagree:
            print(
                "\n  note: the LLM and the rules disagree; going with the LLM, which read it all."
            )
    elif a.llm_error:
        print(f"\n  !  LLM unavailable, rules only: {a.llm_error}")

    # Prompts need a person at a console. Piped input (CI, a script, the future UI) never
    # blocks on one: it gets the verdict and the exit code, and --force decides.
    interactive = sys.stdin.isatty()
    if a.conversation and a.model and interactive:
        _follow_ups(a)

    if args.dry_run:
        print("\n--dry-run: no CV built, nothing appended.")
        return 0

    forced = a.verdict.decision == "skip"
    if (
        forced
        and not args.force
        and not (interactive and _confirm("\n  Verdict is skip. Tailor a CV anyway? [y/N] "))
    ):
        print("\n  Skipped - nothing built. Rerun with --force to tailor it anyway.")
        return 1

    wanted = len(t["matched"])
    r = tl.fit(t)
    made, pages, kept = ([r.pdf] if r.pdf else []), r.pages or 0, r.keywords_kept
    if kept < wanted:
        # The brief is written from `t` after this, so it reports what the CV really says.
        detail = (
            f"kept the strongest {kept}"
            if kept
            else f"the {t['variant']} variant has no room for the block at all, so it "
            "was left off - the CV is untailored apart from the folder it lands in"
        )
        print(f"\n  note: {wanted} keywords ran past {tl.MAX_PAGES} pages; {detail}.")
    tl.append_briefs([t], args.briefs)
    tl.write_jd(company, title, text, t["slug"])
    for path in made:
        print(f"\n  CV      -> {tl.display_path(path)}" + (f"  ({pages} pages)" if pages else ""))
    if pages > tl.MAX_PAGES:
        print(
            f"  !! still {pages} pages with no keyword block left to trim - "
            "check cv/profile.json before sending.",
            file=sys.stderr,
        )
    if not made:
        print("  !  no PDF produced (Word unavailable); the .docx is in cv/out/tailored/")
    print(f"  Brief   -> appended to {tl.display_path(args.briefs)}")

    if args.save:
        store = Store(args.store_file or default_path())
        verdict = a.verdict
        app = Application(
            id=store.next_id(),
            company=company,
            role=title,
            status="wishlist",
            url=args.url,
            location=args.location or "Remote",
            notes=[
                f"pasted by hand | cv: {t['variant']}",
                f"verdict: {verdict.decision} ({verdict.source})"
                + (" - tailored anyway" if forced else ""),
                "source: paste",
            ],
        )
        store.add(app)
        store.save()
        print(f"  Tracked -> jobtrack #{app.id} (wishlist)")

    print(f'\nNext: apply, then  python -m jobs.brief applied "{company}"')
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValidationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
