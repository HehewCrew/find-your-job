"""Per-posting CV tailoring.

Tailoring here means one thing only: surfacing skills you **already have** that the job
description also asks for, so the ATS keyword match improves. Nothing is ever added to a
CV because a posting mentions it. `have()` is the whitelist - settings.json's
`tailoring.skills`, or failing that the skills your profile.json lists; anything outside it
can only be reported back as a gap, never printed onto the document.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "cv"))

import build as cvbuild  # noqa: E402  (cv/build.py - stdlib only, no import side effects)

from jobs import settings  # noqa: E402

MAX_PAGES = cvbuild.MAX_PAGES
TAILORED_DIR = ROOT / "cv" / "out" / "tailored"


PROFILE_PATH = ROOT / "cv" / "profile.json"
EXAMPLE_PROFILE_PATH = ROOT / "cv" / "profile.example.json"


def display_path(path: Path) -> str:
    """Repo-relative when it is inside the repo, absolute otherwise.

    --briefs, --file and test sandboxes can point anywhere, and Path.relative_to raises
    rather than falling back - which turned a successful build into a traceback after
    the work was already done.
    """
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _profile() -> dict:
    path = PROFILE_PATH if PROFILE_PATH.exists() else EXAMPLE_PROFILE_PATH
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def skills_from_profile(profile: dict) -> dict[str, str]:
    """Every skill_groups item as its own whitelist entry, matched literally.

    The fallback when settings.json lists no `tailoring.skills`: a CV may only headline
    what it already claims, and the profile is where the claims are. Profile items are
    written for a reader, so only their name is searched for: "Python (pytest)" and
    "Python - scripting and reporting" both become "Python". An item that is a whole
    sentence names no single skill and is left out - list those in settings instead.
    """
    out: dict[str, str] = {}
    for group in (profile.get("skill_groups") or {}).values():
        if not isinstance(group, dict):
            continue
        for item in group.get("items") or []:
            name = re.split(r"\s+[—–-]\s+|:\s", str(item).strip(), maxsplit=1)[0]
            name = re.sub(r"\s*\(.*?\)", "", name).strip()
            if name and len(name.split()) <= 3 and name not in out:
                out[name] = settings.phrase(name.lower())
    return out


def have(s: settings.Settings | None = None) -> dict[str, str]:
    """Canonical skill name -> pattern that means it in a job description.

    The whitelist: nothing outside it is ever printed on a CV. Order matters - fit() trims
    from the end, so the generic entries belong last.
    """
    s = s or settings.current()
    return s.skills or skills_from_profile(_profile())


def lack(s: settings.Settings | None = None) -> dict[str, str]:
    """Known gaps. Never printed on a CV - reported so you can judge the application."""
    return (s or settings.current()).gaps


def _weak(s: settings.Settings | None = None) -> set[str]:
    """Real skills that still look thin as the whole keyword block - languages, soft skills."""
    return (s or settings.current()).weak_skills


def slugify(*parts: str) -> str:
    raw = "-".join(p for p in parts if p)
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9]+", "-", raw.lower())).strip("-")[:70]


def analyse(text: str, s: settings.Settings | None = None) -> tuple[list[str], list[str]]:
    """Return (skills you have that this posting wants, gaps this posting wants)."""
    low = (text or "").lower()
    matched = [name for name, pat in have(s).items() if re.search(pat, low, re.I)]
    gaps = [name for name, pat in lack(s).items() if re.search(pat, low, re.I)]
    return matched, gaps


# A "Most Relevant to X" block reading only "English (bilingual)" looks worse than no
# block at all, so weak skills (settings tailoring.weak) never count toward this minimum.
MIN_KEYWORDS = 3


def tailor_for(posting, variant: str, *, max_keywords: int = 14) -> dict:
    haystack = f"{posting.title}\n{posting.description}"
    matched, gaps = analyse(haystack)
    strong = [m for m in matched if m not in _weak()]
    # Only headline the block when there is genuine substance behind it.
    matched = matched if len(strong) >= MIN_KEYWORDS else []
    return {
        "company": posting.company,
        "job_title": posting.title,
        "variant": variant,
        "matched": matched[:max_keywords],
        "gaps": gaps,
        "slug": slugify(posting.company, posting.title),
        "url": posting.url,
    }


def write_jd(
    company: str, title: str, text: str, slug: str, tailored: Path | None = None
) -> Path | None:
    """Save the full posting text next to its tailored CV, so it survives past the day it
    was pasted or scraped - TODAY_SCRAPING.json is overwritten by the next day's scrape,
    and jobs.paste never kept the raw text at all. `jobs.brief close` copies this on into
    applications/<slug>/ when the posting is actually applied to.
    """
    tailored = tailored or TAILORED_DIR
    text = (text or "").strip()
    if not text:
        return None
    dest = tailored / slug / "jd.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(f"# {company} — {title}\n\n{text}\n", encoding="utf-8")
    return dest


def build(
    tailors: list[dict],
    *,
    pdf: bool = True,
    pages_out: dict[Path, int] | None = None,
    warn: bool = True,
) -> list[Path]:
    """Generate one tailored CV per posting. Returns the PDFs, or the .docx
    intermediates when pdf=False (build.py deletes those once converted).

    `pages_out` is passed straight through to build.to_pdf - see fit() below.
    """
    profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    made: list[Path] = []
    for t in tailors:
        if not t["variant"]:
            continue
        path, _ = cvbuild.build_variant(profile, t["variant"], tailor=t)
        made.append(path)
    if pdf and made:
        return cvbuild.to_pdf(made, pages_out=pages_out, warn=warn)
    return made


# How many keywords to drop per attempt when a tailored CV runs long.
_SHRINK_STEP = 3


def shrink(matched: list[str]) -> list[str]:
    """One step of the fit() loop: the next shorter keyword list to try.

    Split out from fit() because fit() cannot run without Word, and the rule this
    encodes - never stop on a list too short to deserve its own header - is the part
    worth pinning. Always reaches [] so the loop terminates.
    """
    trimmed = matched[:-_SHRINK_STEP]
    return trimmed if len(trimmed) >= MIN_KEYWORDS else []


def fit(t: dict) -> tuple[list[Path], int, int]:
    """Build one tailored CV, dropping keywords until it obeys the two-page rule.

    The "Most Relevant to <company>" block is the only part of a tailored CV whose
    length varies with the posting, so it is the only part worth trimming: a
    keyword-dense description can match 14 skills and push an otherwise two-page
    variant onto a third. Nothing else is touched, and keywords are dropped from the
    end - `analyse()` yields them in whitelist order, which is roughly strongest-first.

    The last attempt is always an empty list, which makes build_variant drop the block
    header as well as the bullet. That matters: on a variant with no slack, two
    keywords still cost the two lines that spill the page, so trimming that stops
    short of empty never converges.

    Returns (pdfs, pages, keywords actually built) - the third value describes the file
    on disk, not the trimming that was attempted, because the brief is written from it.
    pages == 0 means Word was unavailable and nothing could be measured.
    """
    made: list[Path] = []
    worst = 0
    while True:
        pages: dict[Path, int] = {}
        kept = len(t["matched"])
        # Only the final, reported build is allowed to warn - see to_pdf(warn=...).
        made = build([t], pages_out=pages, warn=False)
        if not made or not pages:
            return made, 0, kept
        worst = max(pages.values())
        if worst <= MAX_PAGES or kept == 0:
            return made, worst, kept
        t["matched"] = shrink(t["matched"])


def _stash(out: Path) -> Path | None:
    """Keep a copy of a briefing sheet that still has entries in it.

    `jobs.brief close` empties this file, so anything left here is a day you haven't
    finished working through - overwriting it would silently lose the applied/aborted marks.
    """
    if not out.exists() or "\n## " not in out.read_text(encoding="utf-8"):
        return None
    stash = out.with_name(f"{out.stem}.prev{out.suffix}")
    stash.write_bytes(out.read_bytes())
    return stash


# Both write_briefs and `jobs.brief close` write this, so it lives in one place.
BRIEFS_HEADER = """# Tailored applications

> Mark each one as you go: `python -m jobs.brief applied <n>` / `aborted <n> --reason "…"`,
> or edit the **Status:** line by hand. Then `python -m jobs.brief close` files them.
"""


def brief_block(t: dict) -> list[str]:
    """The `## Company — Role` section for one tailored posting.

    `jobs.brief` parses these back, so the field names and the `**CV:**` shape are a
    contract - keep them in step with `brief._FIELD` / `brief._CV`.
    """
    return [
        f"## {t['company']} — {t['job_title']}",
        "- **Status:** pending",
        f"- **CV:** `{t['variant']}` → `cv/out/tailored/{t['slug']}/*.pdf`",
        f"- **Link:** {t['url']}",
        f"- **Matched skills:** {', '.join(t['matched']) or '—'}",
        f"- **Gaps to expect:** {', '.join(t['gaps']) or 'none flagged'}",
        "",
    ]


def write_briefs(tailors: list[dict], out: Path) -> Path:
    """One markdown file summarising every tailored application for the day."""
    _stash(out)
    lines = [BRIEFS_HEADER]
    for t in tailors:
        lines += brief_block(t)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def append_briefs(tailors: list[dict], out: Path) -> Path:
    """Add postings to BRIEFS.md, keeping whatever is already in it.

    A hand-pasted job description arrives whenever you find one, which is usually
    partway through working the day's scrape. Overwriting here would throw away the
    applied/aborted marks already made - so unlike write_briefs, this never stashes
    and never truncates.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    existing = out.read_text(encoding="utf-8").rstrip() if out.exists() else BRIEFS_HEADER.rstrip()
    lines = [existing, ""]
    for t in tailors:
        lines += brief_block(t)
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


if __name__ == "__main__":
    print("Use: python -m jobs.scrape --cv")
    print(f"Skill whitelist: {len(have())} | known gaps: {len(lack())}")
