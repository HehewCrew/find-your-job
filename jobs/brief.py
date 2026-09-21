"""Work through the day's BRIEFS.md: mark each posting applied or aborted, then file it.

    python -m jobs.brief                       # today's briefs and where each one stands
    python -m jobs.brief applied 3             # mark #3 applied (index, or a name fragment)
    python -m jobs.brief applied anthropic 7   # several at once
    python -m jobs.brief aborted 5 --reason "US-only"
    python -m jobs.brief aborted rest          # everything still pending
    python -m jobs.brief close                 # file the applied ones, empty BRIEFS.md

BRIEFS.md stays the single source of truth - `applied`/`aborted` just rewrite a `**Status:**`
line, so hand-editing the file works exactly as well as the commands.

`close` is the end of the day. For every posting marked applied it creates
`applications/<posting-slug>/` holding the tailored CV and a `questions.md` to fill in, flips
the jobtrack entry to `applied` (aborted ones become `withdrawn`), appends the day to
`applications/LOG.md`, and leaves BRIEFS.md empty for tomorrow's scrape.

The folder is a working folder, not an archive - answers are usually drafted in it *before*
the form is submitted, and `close` never overwrites what it finds there.
"""

from __future__ import annotations

import argparse
import contextlib
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from jobs.score import dedupe_key  # noqa: E402
from jobs.tailor import BRIEFS_HEADER, slugify  # noqa: E402
from jobtrack.models import Application, ValidationError, today  # noqa: E402
from jobtrack.storage import Store, default_path  # noqa: E402

BRIEFS_PATH = ROOT / "cv" / "out" / "tailored" / "BRIEFS.md"
TAILORED_DIR = ROOT / "cv" / "out" / "tailored"
HEADER = BRIEFS_HEADER

# One working folder per application, alongside the hand-written ones already there.
# Not to be confused with applications.json, which is the jobtrack store.
FOLDER = "applications"

PENDING, APPLIED, ABORTED = "pending", "applied", "aborted"
STATES = (PENDING, APPLIED, ABORTED)

# Aborted is not a jobtrack status - "withdrawn" is the one that already means
# "decided not to pursue". Don't add a status to STATUSES; --sort status depends on its order.
JOBTRACK_STATUS = {APPLIED: "applied", ABORTED: "withdrawn"}

_FIELD = re.compile(r"^- \*\*(.+?):\*\*\s*(.*)$")
_CV = re.compile(r"`([^`]+)`\s*→\s*`([^`]+)`")
# The slug is the folder: cv/out/tailored/<slug>/<name>.pdf
_CV_SLUG_DIR = re.compile(r"tailored/([^/]+)/[^/]+\.pdf$")
# How it used to be written, when the slug was part of the filename: *__<slug>.pdf
_CV_SLUG_SUFFIX = re.compile(r"__([^/]+)\.pdf$")
_SEP = " · "


# -- parsing ------------------------------------------------------------


@dataclass
class Brief:
    """One `## Company — Role` section of BRIEFS.md."""

    company: str
    title: str
    variant: str = ""
    slug: str = ""
    url: str = ""
    matched: str = ""
    gaps: str = ""
    state: str = PENDING
    marked_on: str = ""
    reason: str = ""
    heading: int = 0  # line index of the '## ' heading
    status_line: int | None = None  # line index of the '**Status:**' bullet, if present

    @property
    def label(self) -> str:
        return f"{self.company} — {self.title}"

    @property
    def folder(self) -> str:
        return self.slug or slugify(self.company, self.title)


def parse_cv(value: str) -> tuple[str, str]:
    """`(variant, slug)` from a **CV:** bullet, or `("", "")` if it can't be read.

    The slug used to be part of the filename (`*__<slug>.pdf`) and is now the folder
    (`tailored/<slug>/*.pdf`), so that every CV can be called the same thing - the file
    attached to an application form should read as a CV, not as a build artefact. Both
    shapes are accepted, so a BRIEFS.md written before the change still files correctly.
    """
    match = _CV.search(value)
    if not match:
        return "", ""
    variant, path = match.group(1), match.group(2).replace("\\", "/")
    slug = _CV_SLUG_DIR.search(path) or _CV_SLUG_SUFFIX.search(path)
    return variant, slug.group(1) if slug else ""


def parse(text: str) -> list[Brief]:
    briefs: list[Brief] = []
    for i, line in enumerate(text.splitlines()):
        if line.startswith("## "):
            company, _, title = line[3:].partition(" — ")
            briefs.append(Brief(company=company.strip(), title=title.strip(), heading=i))
            continue
        match = _FIELD.match(line)
        if not match or not briefs:
            continue
        key, value = match.group(1).strip().lower(), match.group(2).strip()
        b = briefs[-1]
        if key == "cv":
            variant, slug = parse_cv(value)
            if variant:
                b.variant, b.slug = variant, slug
        elif key == "link":
            b.url = value
        elif key == "matched skills":
            b.matched = value
        elif key == "gaps to expect":
            b.gaps = value
        elif key == "status":
            b.status_line = i
            b.state, b.marked_on, b.reason = parse_status(value)
    return briefs


_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")
# Whatever a human reached for between the fields. The written separator is
# "·", which no keyboard offers - so anything plausible has to be accepted.
_LEADING_SEP_RE = re.compile(r"^[\s·|,;:.\-–—]+")


def parse_status(value: str) -> tuple[str, str, str]:
    """Read a status bullet written by the tool *or* typed by hand.

    Splitting on a fixed separator is what broke here: reasons contain hyphens
    ("under-qualified") and dates contain them too, so no single split is safe.
    Instead, take the state off the front by name, then the date if one follows,
    and treat whatever remains as the reason.
    """
    text = value.strip()
    if not text:
        return PENDING, "", ""

    lowered = text.lower()
    state = next((s for s in STATES if lowered.startswith(s)), "")
    if not state:
        # Unrecognised - hand it back verbatim so it shows up as wrong rather
        # than being silently rounded to "pending" and filed as unmarked.
        return text, "", ""

    rest = _LEADING_SEP_RE.sub("", text[len(state) :])
    marked_on = ""
    if date_match := _DATE_RE.match(rest):
        marked_on = date_match.group(1)
        rest = _LEADING_SEP_RE.sub("", rest[date_match.end() :])
    return state, marked_on, rest.strip()


def load(path: Path = BRIEFS_PATH) -> tuple[list[str], list[Brief]]:
    if not path.exists():
        return [], []
    text = path.read_text(encoding="utf-8")
    return text.splitlines(), parse(text)


def pending(path: Path = BRIEFS_PATH) -> int:
    """How many briefs are still unresolved - a scrape would overwrite these."""
    return sum(1 for b in load(path)[1] if b.state == PENDING)


# -- marking ------------------------------------------------------------


def status_bullet(state: str, reason: str = "", when: str = "") -> str:
    bits = [state, when or today()]
    if reason:
        bits.append(reason)
    return "- **Status:** " + _SEP.join(bits)


def mark(lines: list[str], chosen: list[Brief], state: str, reason: str = "") -> list[str]:
    """Rewrite each chosen brief's status bullet, inserting one if the file predates it."""
    out = list(lines)
    # Back to front, so inserting a line cannot shift an index we still need.
    for b in sorted(chosen, key=lambda b: b.heading, reverse=True):
        bullet = status_bullet(state, reason)
        if b.status_line is not None:
            out[b.status_line] = bullet
        else:
            out.insert(b.heading + 1, bullet)
    return out


def select(briefs: list[Brief], selectors: list[str]) -> list[Brief]:
    """Resolve 1-based indices, name fragments, `rest` (still pending) and `all`."""
    chosen: list[Brief] = []
    for raw in selectors:
        token = raw.strip().lower()
        if token == "all":
            found = list(briefs)
        elif token == "rest":
            found = [b for b in briefs if b.state == PENDING]
            if not found:
                raise ValidationError("nothing is still pending")
        elif token.isdigit():
            index = int(token)
            if not 1 <= index <= len(briefs):
                raise ValidationError(f"no brief #{index}; there are {len(briefs)}")
            found = [briefs[index - 1]]
        else:
            found = [b for b in briefs if token in b.label.lower()]
            if not found:
                raise ValidationError(f"no brief matches {raw!r}")
            if len(found) > 1:
                names = "; ".join(b.label for b in found)
                raise ValidationError(f"{raw!r} is ambiguous: {names}")
        for b in found:
            if b not in chosen:
                chosen.append(b)
    return chosen


# -- filing -------------------------------------------------------------


QUESTIONS_TEMPLATE = """# {label}

- **Applied on:** {date}
- **Posting:** {url}
- **jobtrack id:** {app_id}
- **CV attached:** {cv} (variant `{variant}`)
- **Matched skills:** {matched}
- **Gaps to expect:** {gaps}

---

## Questions asked

No questions recorded yet. Paste each one from the application form as a `### Q` heading
below and keep the answer under it. If the form asked nothing, say so and delete the rest.

### Q1 —

"""


CV_SUFFIXES = (".pdf", ".docx")


def _cvs_for(slug: str, tailored: Path) -> list[Path]:
    """Every generated CV file for one posting: `cv/out/tailored/<slug>/<name>.{pdf,docx}`.

    The DOCX is always built and the PDF only when Word or LibreOffice is installed, so
    either may be alone. Falls back to the old flat `*__<slug>.pdf`, so a CV built before
    the layout changed is still found and filed rather than reported missing.
    """
    found = _cvs_in(tailored / slug)
    if found:
        return found
    return sorted(p for s in CV_SUFFIXES for p in tailored.glob(f"*__{slug}{s}"))


def _cvs_in(folder: Path) -> list[Path]:
    # `~$name.docx` is the lock file Word keeps beside a document it has open.
    return sorted(
        p for p in folder.glob("*") if p.suffix in CV_SUFFIXES and not p.name.startswith("~$")
    )


def find_app(store: Store, b: Brief) -> Application | None:
    """URL first - company names drift between a scrape and the brief it wrote."""
    if b.url:
        for a in store:
            if a.url == b.url:
                return a
    key = dedupe_key(b.company, b.title)
    for a in store:
        if dedupe_key(a.company, a.role) == key:
            return a
    return None


@dataclass
class Filed:
    brief: Brief
    app_id: int
    folder: Path | None = None
    cvs: list[Path] = field(default_factory=list)
    jd: Path | None = None
    kept_answers: bool = False
    problems: list[str] = field(default_factory=list)


def file_brief(b: Brief, store: Store, root: Path, *, dry_run: bool = False) -> Filed:
    """Move one applied brief into applications/<slug>/ and flip its jobtrack entry."""
    when = b.marked_on or today()
    app = find_app(store, b)
    if app is None:
        app = Application(
            id=store.next_id(),
            company=b.company,
            role=b.title,
            status="applied",
            applied_on=when,
            url=b.url,
            notes=["added by jobs.brief close - no matching wishlist entry"],
        )
        if not dry_run:
            store.add(app)
    else:
        app.status = "applied"
        app.applied_on = when
        app.notes.append(f"applied {when}{_SEP}cv {b.variant}{_SEP}{FOLDER}/{b.folder}/")
        app.touch()

    filed = Filed(brief=b, app_id=app.id)
    dest = root / FOLDER / b.folder
    filed.folder = dest
    questions = dest / "questions.md"
    # The normal case for a form with questions: the answers were drafted here before
    # submitting. Never overwrite them, and don't report it as though something broke.
    filed.kept_answers = questions.exists()
    sources = _cvs_for(b.folder, TAILORED_DIR)
    if not sources and not _cvs_in(dest):
        filed.problems.append(f"no tailored CV found in cv/out/tailored/{b.folder}/")
    if dry_run:
        return filed

    dest.mkdir(parents=True, exist_ok=True)
    if sources:
        for source in sources:
            target = dest / source.name
            if not target.exists():
                shutil.copy2(source, target)
            if target.exists():
                filed.cvs.append(target)
    else:
        # A CV put here by hand counts; not every variant comes from the tailored build.
        filed.cvs = _cvs_in(dest)

    source_jd = TAILORED_DIR / b.folder / "jd.md"
    dest_jd = dest / "jd.md"
    if source_jd.exists() and not dest_jd.exists():
        shutil.copy2(source_jd, dest_jd)
    filed.jd = dest_jd if dest_jd.exists() else None

    if not filed.kept_answers:
        questions.write_text(
            QUESTIONS_TEMPLATE.format(
                label=b.label,
                date=when,
                url=b.url or "—",
                app_id=app.id,
                cv=", ".join(f"`{p.name}`" for p in filed.cvs) or "not found",
                variant=b.variant or "—",
                matched=b.matched or "—",
                gaps=b.gaps or "none flagged",
            ),
            encoding="utf-8",
        )
    return filed


def abandon(b: Brief, store: Store, *, dry_run: bool = False) -> int:
    """Withdraw the jobtrack entry, creating one if the posting was never saved.

    An aborted posting with no entry would come straight back in tomorrow's scrape -
    `already_tracked()` reads the store, so the entry is what makes the decision stick.
    """
    when = b.marked_on or today()
    note = f"not pursued {when}" + (f"{_SEP}{b.reason}" if b.reason else "")
    app = find_app(store, b)
    if app is None:
        app = Application(
            id=store.next_id(),
            company=b.company,
            role=b.title,
            status=JOBTRACK_STATUS[ABORTED],
            applied_on=when,
            url=b.url,
            notes=[note],
        )
        if not dry_run:
            store.add(app)
        return app.id
    app.status = JOBTRACK_STATUS[ABORTED]
    app.notes.append(note)
    app.touch()
    return app.id


def log_entry(filed: list[Filed], aborted: list[Brief], when: str) -> str:
    lines = [f"## {when}", ""]
    if filed:
        lines.append(f"### Applied ({len(filed)})")
        lines.append("")
        for f in filed:
            lines.append(f"- **{f.brief.label}** — jobtrack #{f.app_id} — [posting]({f.brief.url})")
            lines.append(f"  - CV `{f.brief.variant}` → `{FOLDER}/{f.brief.folder}/`")
            if f.kept_answers:
                lines.append(f"  - Answers on file: `{FOLDER}/{f.brief.folder}/questions.md`")
            if f.brief.gaps and f.brief.gaps != "none flagged":
                lines.append(f"  - Gaps flagged: {f.brief.gaps}")
            for problem in f.problems:
                lines.append(f"  - ⚠️ {problem}")
        lines.append("")
    if aborted:
        lines.append(f"### Not pursued ({len(aborted)})")
        lines.append("")
        for b in aborted:
            lines.append(f"- **{b.label}**" + (f" — {b.reason}" if b.reason else ""))
        lines.append("")
    return "\n".join(lines)


def write_log(entry: str, path: Path, when: str) -> None:
    """Newest day first. A second close on the same day extends that day's section."""
    title = "# Application log"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{title}\n\n{entry}\n", encoding="utf-8")
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    heading = f"## {when}"
    if heading in lines:
        # Drop the entry's own date heading and splice its body under the existing one.
        body = entry.splitlines()[2:]
        at = lines.index(heading) + 2
    else:
        body = entry.splitlines() + [""]
        at = 2 if lines[:1] == [title] else 0
    path.write_text("\n".join(lines[:at] + body + lines[at:]).rstrip() + "\n", encoding="utf-8")


def empty_briefs(path: Path = BRIEFS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(HEADER, encoding="utf-8")


def sweep_tailored(filed: list[Filed], aborted: list[Brief]) -> list[Path]:
    """Delete the generated tailored CVs for the briefs just filed.

    Closing empties BRIEFS.md, and the brief is what a tailored CV is rebuilt from -
    so once it is gone the CV cannot be regenerated. An applied brief's CV is therefore
    only removed after its copy under applications/<slug>/ is confirmed on disk; that
    copy is the record of what was actually sent. Aborted briefs keep nothing.

    Only the generated locations are touched - the posting's own `<slug>/` subfolder, or
    the old flat `*__<slug>.pdf` name - and both the PDF and the DOCX there. A CV dropped
    into cv/out/tailored/ by hand sits at the top level under some other name, matches
    neither, and is never deleted.
    """
    removed: list[Path] = []
    for f in filed:
        source_jd = TAILORED_DIR / f.brief.folder / "jd.md"
        if f.jd is not None and f.jd.exists():
            source_jd.unlink(missing_ok=True)
        sent = {p.name for p in f.cvs if p.exists()}
        for source in _cvs_for(f.brief.folder, TAILORED_DIR):
            if source.name not in sent or f.folder / source.name == source:
                continue
            source.unlink(missing_ok=True)
            removed.append(source)
            _prune(source.parent)
    for b in aborted:
        (TAILORED_DIR / b.folder / "jd.md").unlink(missing_ok=True)
        for source in _cvs_for(b.folder, TAILORED_DIR):
            source.unlink(missing_ok=True)
            removed.append(source)
            _prune(source.parent)
    return removed


def _prune(folder: Path) -> None:
    """Drop a per-application folder once its CV is gone, leaving no empty shells behind.

    Guarded against removing cv/out/tailored/ itself, which holds BRIEFS.md - and which is
    where `source.parent` points for a CV found by the old flat fallback.
    """
    if folder != TAILORED_DIR and folder.is_dir() and not any(folder.iterdir()):
        with contextlib.suppress(OSError):
            folder.rmdir()


# -- commands -----------------------------------------------------------


def cmd_list(args: argparse.Namespace, briefs: list[Brief]) -> int:
    if not briefs:
        print(f"No briefs in {args.briefs}. Run: python -m jobs.scrape --save --cv")
        return 1
    width = max(len(b.company) for b in briefs)
    print(f"{'#':>3}  {'STATUS':<8}  {'COMPANY':<{width}}  ROLE")
    print("-" * (17 + width + 40))
    for i, b in enumerate(briefs, 1):
        note = f"  ({b.reason})" if b.reason else ""
        state = b.state if b.state in STATES else "??"
        print(f"{i:>3}  {state:<8}  {b.company:<{width}}  {b.title[:60]}{note}")
    counts = {s: sum(1 for b in briefs if b.state == s) for s in STATES}
    unreadable = [(i, b) for i, b in enumerate(briefs, 1) if b.state not in STATES]
    print("-" * (17 + width + 40))
    tally = ", ".join(f"{n} {s}" for s, n in counts.items() if n)
    if unreadable:
        tally += f", {len(unreadable)} unreadable"
    print(f"{len(briefs)} brief(s): {tally}")

    # Never report "all marked" off the back of a status nobody can read.
    if unreadable:
        print(f"\n{len(unreadable)} status line(s) could not be read:", file=sys.stderr)
        for i, b in unreadable:
            print(f"  #{i} {b.company}: {b.state!r}", file=sys.stderr)
        print(
            f"Expected one of: {', '.join(STATES)} — optionally followed by a date and a reason.",
            file=sys.stderr,
        )
        return 2
    if counts[PENDING] == 0:
        print("\nAll marked. File them with:  python -m jobs.brief close")
    return 0


def cmd_mark(args: argparse.Namespace, briefs: list[Brief]) -> int:
    if not briefs:
        print(f"No briefs in {args.briefs}.", file=sys.stderr)
        return 1
    lines, _ = load(args.briefs)
    chosen = select(briefs, args.selectors)
    args.briefs.write_text(
        "\n".join(mark(lines, chosen, args.state, args.reason)) + "\n", encoding="utf-8"
    )
    for b in chosen:
        print(f"{args.state}: {b.label}" + (f" ({args.reason})" if args.reason else ""))
    left = sum(1 for b in briefs if b.state == PENDING and b not in chosen)
    print(f"{left} still pending." if left else "All marked. Next: python -m jobs.brief close")
    return 0


def cmd_close(args: argparse.Namespace, briefs: list[Brief]) -> int:
    applied = [b for b in briefs if b.state == APPLIED]
    aborted = [b for b in briefs if b.state == ABORTED]
    still_pending = [b for b in briefs if b.state not in (APPLIED, ABORTED)]
    if not applied and not aborted:
        print("Nothing marked applied or aborted yet.", file=sys.stderr)
        return 1
    if still_pending and not args.force:
        for b in still_pending:
            print(f"  unmarked: {b.label}", file=sys.stderr)
        print(
            f"{len(still_pending)} brief(s) still pending. Mark them, or pass --force to "
            "close anyway (pending briefs are discarded).",
            file=sys.stderr,
        )
        return 1

    store = Store(args.file or default_path())
    when = today()
    log = args.root / FOLDER / "LOG.md"

    # Every mutation happens before the first print. Console encoding varies (a legacy
    # Windows codepage raises on the em dash in a posting title), and a crash while
    # reporting must never leave the store unsaved on top of folders already written.
    filed = [file_brief(b, store, args.root, dry_run=args.dry_run) for b in applied]
    dropped = [(b, abandon(b, store, dry_run=args.dry_run)) for b in aborted]
    swept: list[Path] = []
    if not args.dry_run:
        store.save()
        write_log(log_entry(filed, aborted, when), log, when)
        # Sweep before emptying: the briefs are what say which CVs these were.
        if not args.keep_cvs:
            swept = sweep_tailored(filed, aborted)
        empty_briefs(args.briefs)

    for f in filed:
        print(f"{FOLDER}/{f.brief.folder}/  <- jobtrack #{f.app_id}, CV {f.brief.variant}")
        if f.kept_answers:
            print("    answers already on file, kept as they are")
        for problem in f.problems:
            print(f"    !  {problem}")
    for b, app_id in dropped:
        print(f"not pursued: {b.label} (jobtrack #{app_id} -> withdrawn)")

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return 0
    print(f"\nLogged {len(filed)} application(s) to {log.relative_to(args.root)}")
    if swept:
        print(f"Cleared {len(swept)} tailored CV(s); the ones you sent are in {FOLDER}/.")
    print(f"{args.briefs.name} is empty and ready for tomorrow's scrape.")
    return 0


# -- entry point --------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jobs.brief", description=__doc__.splitlines()[0])
    parser.add_argument("--briefs", type=Path, default=BRIEFS_PATH, help="BRIEFS.md to work on")
    parser.add_argument("--root", type=Path, default=ROOT, help=f"where {FOLDER}/ lives")
    sub = parser.add_subparsers(dest="command")

    p_list = sub.add_parser("list", help="show the briefs and their status")
    p_list.set_defaults(func=cmd_list)

    for state in (APPLIED, ABORTED):
        p = sub.add_parser(state, help=f"mark briefs as {state}")
        p.add_argument("selectors", nargs="+", metavar="SELECTOR", help="index, name, rest, all")
        p.add_argument("--reason", default="", help="short note, kept in BRIEFS.md and the log")
        p.set_defaults(func=cmd_mark, state=state)

    p_close = sub.add_parser("close", help="file the applied ones and empty BRIEFS.md")
    p_close.add_argument("--dry-run", action="store_true", help="show what would happen")
    p_close.add_argument("--force", action="store_true", help="close with briefs still pending")
    p_close.add_argument(
        "--keep-cvs",
        action="store_true",
        help="don't clear the tailored CVs (they are normally deleted once filed)",
    )
    p_close.add_argument("--file", type=Path, default=None, help="jobtrack data file")
    p_close.set_defaults(func=cmd_close)

    parser.set_defaults(func=cmd_list, command="list")
    return parser


def main(argv: list[str] | None = None) -> int:
    # Company and role come from job boards and are full of em dashes; a legacy Windows
    # console codepage raises on them. Degrade to '?' rather than kill the command.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    args = build_parser().parse_args(argv)
    _, briefs = load(args.briefs)
    try:
        return args.func(args, briefs)
    except ValidationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
