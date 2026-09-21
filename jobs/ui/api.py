"""What the page can ask for, one function per endpoint - no HTTP in here.

Every function takes a `Paths` and plain arguments, returns something JSON can carry, and
raises the core errors (`ValidationError`, `SettingsError`, `JobsError`, `Skipped`, `Busy`)
for server.py to turn into status codes. Long jobs go through a `TaskRunner`: the `start_*`
functions check what can be checked up front, so "nothing ticked" is an immediate answer
rather than a task that fails a second later.

The CV and brief folders come from the `jobs/` modules themselves (tailor.TAILORED_DIR and
friends); `Paths.tailored` must point at the same place, which Paths.default() does.
"""

from __future__ import annotations

import secrets
import sys
from collections import OrderedDict
from collections.abc import Callable
from datetime import date
from pathlib import Path

from jobs import brief, paste, pick, review, scrape, settings
from jobs import tailor as tl
from jobs.errors import JobsError, Skipped
from jobs.ui import phase
from jobs.ui.opener import open_path
from jobs.ui.paths import Paths
from jobs.ui.tasks import TaskRunner
from jobtrack.models import ValidationError
from jobtrack.storage import Store

# Verdicts waiting for "Build the CV", by id. A handful is plenty for one person.
_ASSESSMENTS: OrderedDict[str, tuple[paste.Assessment, str]] = OrderedDict()
_KEEP = 20


def _rel(path: Path | None, paths: Paths) -> str | None:
    if path is None:
        return None
    try:
        return path.relative_to(paths.root).as_posix()
    except ValueError:
        return str(path)


# --- state -------------------------------------------------------------------------


def pdf_engine() -> str:
    """What will turn CVs into PDFs: "word", "libreoffice" or "none". Never starts Word."""
    if sys.platform == "win32":
        try:
            import importlib.util
            import winreg

            if importlib.util.find_spec("win32com") is not None:
                winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "Word.Application"))
                return "word"
        except (OSError, ImportError):
            pass
    return "libreoffice" if tl.cvbuild._find_soffice() else "none"


def state(paths: Paths, runner: TaskRunner, engine: str, today: str | None = None) -> dict:
    today = today or date.today().isoformat()
    leads_ = review.read_sheet(sheet=paths.sheet, data=paths.data)
    _, briefs_ = brief.load(paths.briefs)
    try:
        example, settings_error = settings.current().is_example, ""
    except settings.SettingsError as exc:
        example, settings_error = False, str(exc)
    return {
        "phase": phase.detect(paths, today),
        "today": today,
        "sheet_date": review.sheet_date(paths.sheet),
        "leads": {
            m: sum(1 for x in leads_ if x.mark == m)
            for m in (review.TAKE, review.SKIP, review.PENDING)
        },
        "briefs": {s: sum(1 for b in briefs_ if b.state == s) for s in brief.STATES},
        "cvs": len(cvs(paths)),
        "example_settings": example,
        "settings_error": settings_error,
        "pdf_engine": engine,
        "task": runner.running(),
    }


# --- review ------------------------------------------------------------------------


def _lead(x: review.Lead) -> dict:
    d, t = x.data, x.data.get("tailor") or {}
    return {
        "index": x.index,
        "url": x.url,
        "company": x.company,
        "title": x.title or x.label,
        "mark": x.mark,
        "score": d.get("score"),
        "reasons": d.get("reasons", []),
        "variant": d.get("variant", ""),
        "location": d.get("location", ""),
        "matched": t.get("matched", []),
        "gaps": t.get("gaps", []),
        "description": d.get("description", ""),
        "posted_at": d.get("posted_at", ""),
    }


def leads(paths: Paths) -> list[dict]:
    return [_lead(x) for x in review.read_sheet(sheet=paths.sheet, data=paths.data)]


def mark_lead(paths: Paths, url: str, mark: str) -> dict:
    review.set_mark(url, mark, sheet=paths.sheet)
    return next(x for x in leads(paths) if x["url"] == url)


# --- build -------------------------------------------------------------------------


def _cv(cv: tl.CvResult, paths: Paths) -> dict:
    return {
        "company": cv.company,
        "variant": cv.variant,
        "docx": _rel(cv.docx, paths),
        "pdf": _rel(cv.pdf, paths),
        "engine": cv.engine,
        "pages": cv.pages,
        "kept": cv.keywords_kept,
        "wanted": cv.keywords_wanted,
        "error": cv.error,
    }


def cvs(paths: Paths) -> list[dict]:
    """Every tailored CV waiting in cv/out/tailored/, one entry per posting folder."""
    out = []
    if not paths.tailored.is_dir():
        return out
    for folder in sorted(p for p in paths.tailored.iterdir() if p.is_dir()):
        files = brief._cvs_in(folder)
        if not files:
            continue
        jd = folder / "jd.md"
        heading = jd.read_text(encoding="utf-8").splitlines()[0] if jd.exists() else ""
        company, _, title = heading.removeprefix("# ").partition(" — ")
        out.append(
            {
                "slug": folder.name,
                "company": company or folder.name,
                "title": title,
                "docx": next((_rel(f, paths) for f in files if f.suffix == ".docx"), None),
                "pdf": next((_rel(f, paths) for f in files if f.suffix == ".pdf"), None),
            }
        )
    return out


def start_pick(paths: Paths, runner: TaskRunner) -> None:
    taken, dropped = pick.choose(
        review.read_sheet(sheet=paths.sheet, data=paths.data), [], paths.sheet.name
    )

    def job(report) -> dict:
        result = pick.run(
            taken, dropped, briefs=paths.briefs, store=Store(paths.store), report=report
        )
        return {
            "cvs": [_cv(cv, paths) for cv in result.cvs],
            "taken": len(result.taken),
            "dropped": len(result.dropped),
        }

    runner.start("build", job)


# --- apply and close ---------------------------------------------------------------


def briefs(paths: Paths) -> list[dict]:
    _, found = brief.load(paths.briefs)
    return [
        {
            "index": i,
            "company": b.company,
            "title": b.title,
            "label": b.label,
            "state": b.state,
            "reason": b.reason,
            "variant": b.variant,
            "url": b.url,
            "cv": [_rel(p, paths) for p in brief._cvs_for(b.folder, paths.tailored)],
        }
        for i, b in enumerate(found, 1)
    ]


def mark_briefs(paths: Paths, selectors: list[str], state_: str, reason: str) -> dict:
    if state_ not in (brief.APPLIED, brief.ABORTED, brief.PENDING):
        raise ValidationError(f"unknown state {state_!r}")
    result = brief.mark_briefs(paths.briefs, [str(s) for s in selectors], state_, reason)
    return {"changed": [b.label for b in result.changed], "pending": result.pending}


def close(paths: Paths, dry_run: bool, force: bool) -> dict:
    result = brief.close(paths.briefs, paths.root, Store(paths.store), force=force, dry_run=dry_run)
    return {
        "dry_run": result.dry_run,
        "filed": [
            {
                "company": f.brief.company,
                "title": f.brief.title,
                "folder": f"{brief.FOLDER}/{f.brief.folder}/",
                "app_id": f.app_id,
                "problems": f.problems,
            }
            for f in result.filed
        ],
        "dropped": [
            {"company": b.company, "title": b.title, "app_id": i} for b, i in result.dropped
        ],
        "cleared": len({p.with_suffix("") for p in result.swept}),
    }


# --- paste -------------------------------------------------------------------------


def _verdict(v) -> dict:
    return {
        "decision": v.decision,
        "source": v.source,
        "reasons": v.reasons,
        "blockers": v.blockers,
        "gaps": v.gaps,
        "questions": v.questions,
    }


def assess(
    paths: Paths,
    *,
    text: str,
    company: str,
    title: str,
    url: str,
    location: str,
    variant: str,
) -> dict:
    """The verdict on a pasted posting - the same checks `jobs.paste` makes before building."""
    text = (text or "").strip()
    if len(text) < 100:
        raise ValidationError(
            f"Only {len(text)} characters of job description - paste the full posting so the "
            "keyword match means something."
        )
    if variant and variant not in paste.variants():
        raise ValidationError(f"No '{variant}' CV in your profile.")
    title = title or paste.guess_title(text)
    if not title:
        raise ValidationError("Could not work out the role title - type it in.")
    company = company or paste.company_from_url(url)
    if not company:
        raise ValidationError("Type the company name - it goes on the CV.")

    a = paste.assess(
        text, company=company, title=title, url=url, location=location, variant=variant
    )
    key = secrets.token_hex(8)
    _ASSESSMENTS[key] = (a, text)
    while len(_ASSESSMENTS) > _KEEP:
        _ASSESSMENTS.popitem(last=False)
    return {
        "id": key,
        **_verdict(a.verdict),
        "rules": _verdict(a.rules),
        "disagree": a.disagree,
        "llm_error": a.llm_error,
        "company": company,
        "title": title,
        "variant": a.tailor["variant"],
        "matched": a.tailor["matched"],
        "gap_skills": a.tailor["gaps"],
    }


def start_paste(paths: Paths, runner: TaskRunner, key: str, force: bool) -> None:
    if key not in _ASSESSMENTS:
        raise JobsError("That verdict has expired - get a new one.")
    a, text = _ASSESSMENTS[key]
    if a.verdict.decision == "skip" and not force:
        raise Skipped(a)

    def job(report) -> dict:
        result = paste.build(a, text, force=force, briefs=paths.briefs, report=report)
        return {"cv": _cv(result.cv, paths), "forced": result.forced}

    runner.start("paste", job)


# --- scrape ------------------------------------------------------------------------


def start_scrape(paths: Paths, runner: TaskRunner) -> None:
    def job(report) -> dict:
        result = scrape.run(
            sources=None,
            min_score=None,
            store=Store(paths.store),
            report=report,
            sheet=paths.sheet,
            data=paths.data,
        )
        return {
            "raw": result.raw,
            "problems": result.problems,
            "leads": len(result.leads),
            "sheet": _rel(result.sheet, paths),
        }

    runner.start("scrape", job)


# --- files -------------------------------------------------------------------------


def open_file(
    paths: Paths,
    path: str,
    reveal: bool,
    opener: Callable[[Path, bool], None] = open_path,
) -> None:
    """Open a CV or an application folder's file - and nothing else on the machine."""
    target = Path(path)
    target = (target if target.is_absolute() else paths.root / target).resolve()
    allowed = [paths.root / "cv" / "out", paths.tailored, paths.applications]
    if not any(target.is_relative_to(a.resolve()) for a in allowed):
        raise ValidationError("Only CVs and application folders can be opened from here.")
    if not target.exists():
        raise ValidationError("That file is no longer there - refresh the page.")
    opener(target, reveal)
