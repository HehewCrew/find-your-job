"""The day's shortlist as a review sheet, before any CV is built.

`jobs.scrape` writes TODAY_SCRAPING.md — every qualifying lead, nothing filtered by
`--limit` — and you tick the ones worth applying to. `jobs.pick` then reads the ticks
and does the expensive part: tailoring a CV per chosen posting and appending it to
BRIEFS.md.

Why the split: building a CV costs a Word round-trip each, and most leads get aborted
after a closer read. Deciding first and building second means the cost is only paid for
postings that are actually going somewhere.

Two files, deliberately:

  * **TODAY_SCRAPING.md** is the decision surface. It holds only what you need to judge
    a posting, and the checkbox he changes.
  * **TODAY_SCRAPING.json** is the data record - full description, score, matched keywords.
    Markdown cannot hold a 4,000-character job description without becoming unreadable, and
    re-deriving one from a summary would silently change what the CV is tailored against.

They are matched by URL, so re-ordering or deleting lines in the markdown is harmless.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHEET_PATH = ROOT / "TODAY_SCRAPING.md"
DATA_PATH = ROOT / "TODAY_SCRAPING.json"

TAKE, SKIP, PENDING = "take", "skip", "pending"

# "## [x] 3. Company — Role". The marker is the only part meant to be hand-edited.
_HEADING = re.compile(r"^##\s*\[(?P<mark>[^\]]?)\]\s*(?P<index>\d+)\.\s*(?P<label>.+)$")
_LINK = re.compile(r"^- \*\*Link:\*\*\s*(.+)$")
_DATE = re.compile(r"^# Today's scrape — (\d{4}-\d{2}-\d{2})")

_MARKS = {"x": TAKE, "X": TAKE, "-": SKIP, "": PENDING, " ": PENDING}

HEADER = """# Today's scrape — {date}

{count} lead(s) qualified. Tick the ones worth applying to, then build their CVs:

```
python -m jobs.pick              # everything ticked [x]
python -m jobs.pick 1 4 9        # or pick by number, no editing needed
python -m jobs.pick --list       # what is ticked so far
```

Mark a heading `[x]` to take it, `[-]` to drop it for good (it will not come back in
tomorrow's scrape), or leave it `[ ]` to decide later. Nothing here is in `jobtrack` yet —
`jobs.pick` is what records it.
"""


@dataclass
class Lead:
    """One reviewable posting. `data` is its entry from TODAY_SCRAPING.json."""

    index: int
    label: str
    url: str
    mark: str = PENDING
    data: dict = field(default_factory=dict)

    @property
    def company(self) -> str:
        return self.data.get("company", self.label.split(" — ")[0])

    @property
    def title(self) -> str:
        return self.data.get("title", "")


def _block(i: int, s) -> list[str]:
    """One reviewable section. `s` is a jobs.score.Scored."""
    p = s.posting
    matched = ", ".join(s.tailor["matched"]) or "—"
    gaps = ", ".join(s.tailor["gaps"]) or "none flagged"
    return [
        f"## [ ] {i}. {p.company} — {p.title}",
        f"- **Score:** {s.score} · {', '.join(s.reasons)}",
        f"- **CV:** `{s.variant}`",
        f"- **Where:** {p.location or 'not stated'}",
        f"- **Link:** {p.url}",
        f"- **Matched skills:** {matched}",
        f"- **Gaps to expect:** {gaps}",
        f"- **Source:** {p.source}" + (f" · posted {p.posted_at}" if p.posted_at else ""),
        "",
    ]


def write_sheet(
    leads: list, when: str, *, sheet: Path = SHEET_PATH, data: Path = DATA_PATH
) -> Path:
    """Render every lead to the review sheet and its JSON sidecar.

    Overwrites both: the sheet is one day's shortlist, and a lead left un-ticked is not a
    decision worth preserving - it comes back in tomorrow's scrape unless marked `[-]`.
    """
    lines = [HEADER.format(date=when, count=len(leads))]
    for i, s in enumerate(leads, 1):
        lines += _block(i, s)
    sheet.write_text("\n".join(lines), encoding="utf-8")

    data.write_text(
        json.dumps(
            [
                {
                    "index": i,
                    "url": s.posting.url,
                    "company": s.posting.company,
                    "title": s.posting.title,
                    "location": s.posting.location,
                    "description": s.posting.description,
                    "source": s.posting.source,
                    "posted_at": s.posting.posted_at,
                    "score": s.score,
                    "reasons": s.reasons,
                    "variant": s.variant,
                    "tailor": s.tailor,
                }
                for i, s in enumerate(leads, 1)
            ],
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return sheet


def read_sheet(*, sheet: Path = SHEET_PATH, data: Path = DATA_PATH) -> list[Lead]:
    """Parse the ticks back, joined to the JSON record by URL.

    URL is the join key rather than the index so that re-ordering, deleting or
    re-numbering sections by hand cannot silently tailor a CV against the wrong posting.
    """
    if not sheet.exists():
        return []
    records = {}
    if data.exists():
        records = {r["url"]: r for r in json.loads(data.read_text(encoding="utf-8"))}

    leads: list[Lead] = []
    for line in sheet.read_text(encoding="utf-8").splitlines():
        heading = _HEADING.match(line)
        if heading:
            leads.append(
                Lead(
                    index=int(heading.group("index")),
                    label=heading.group("label").strip(),
                    url="",
                    mark=_MARKS.get(heading.group("mark"), PENDING),
                )
            )
            continue
        link = _LINK.match(line)
        if link and leads:
            leads[-1].url = link.group(1).strip()
            leads[-1].data = records.get(leads[-1].url, {})
    return leads


def select(leads: list[Lead], selectors: list[str]) -> list[Lead]:
    """Resolve explicit picks: 1-based numbers as printed, or a name fragment.

    Picking by number is the common case - it saves editing the file at all - so the
    number must mean what the sheet says, not a position in this list.
    """
    from jobtrack.models import ValidationError

    chosen: list[Lead] = []
    by_index = {lead.index: lead for lead in leads}
    for raw in selectors:
        token = raw.strip().lower()
        if token == "all":
            found = list(leads)
        elif token.isdigit():
            lead = by_index.get(int(token))
            if lead is None:
                raise ValidationError(f"no lead #{token} on the sheet")
            found = [lead]
        else:
            found = [lead for lead in leads if token in lead.label.lower()]
            if not found:
                raise ValidationError(f"no lead matches {raw!r}")
            if len(found) > 1:
                raise ValidationError(
                    f"{raw!r} is ambiguous: " + "; ".join(f"#{x.index} {x.label}" for x in found)
                )
        for lead in found:
            if lead not in chosen:
                chosen.append(lead)
    return chosen


_MARK_CHAR = {TAKE: "x", SKIP: "-", PENDING: " "}


def sheet_date(sheet: Path = SHEET_PATH) -> str | None:
    """The day a sheet was scraped, from its header - None if there is no sheet."""
    if not sheet.exists():
        return None
    for line in sheet.read_text(encoding="utf-8").splitlines()[:3]:
        found = _DATE.match(line)
        if found:
            return found.group(1)
    return None


def set_mark(url: str, mark: str, *, sheet: Path = SHEET_PATH) -> None:
    """Rewrite one lead's `[x]`/`[-]`/`[ ]`, found by its link - the UI's tick.

    By URL, like read_sheet, so a sheet re-scraped or edited by hand since the page loaded
    can never have the mark land on a different posting.
    """
    from jobs.errors import JobsError
    from jobtrack.models import ValidationError

    if mark not in _MARK_CHAR:
        raise ValidationError(f"unknown mark {mark!r}; use one of {', '.join(_MARK_CHAR)}")
    lines = sheet.read_text(encoding="utf-8").splitlines() if sheet.exists() else []
    heading = None
    for i, line in enumerate(lines):
        if _HEADING.match(line):
            heading = i
            continue
        link = _LINK.match(line)
        if link and heading is not None and link.group(1).strip() == url:
            marker = f"## [{_MARK_CHAR[mark]}]"
            lines[heading] = re.sub(r"^##\s*\[[^\]]?\]", marker, lines[heading])
            sheet.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return
    raise JobsError("The sheet changed since the page loaded - refresh to see today's leads.")
