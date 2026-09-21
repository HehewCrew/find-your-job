"""Setup: create and edit the four personal files from the page - no text editor, no JSON.

    profile.json    your facts, and the CV variants built from them
    settings.json   who you are and what you are looking for; the scrape ranks against it
    priorities.md   which postings are worth an application (read by /jobhunt and the LLM)
    rules.md        what a CV may claim

The JSON files are edited as data: the page draws a form from the loaded object, guided by
SCHEMAS below, and sends the whole object back. Keys the form never shows - `_note`s, rare
options - therefore survive a save. Every save is checked by the real loader first
(settings.from_dict, or building every CV variant), written atomically, and the previous
version is kept as `<file>.bak`.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

from jobs import settings as st
from jobs import tailor as tl
from jobs.ui.paths import Paths
from jobtrack.models import ValidationError

JSON_FILES = ("profile", "settings")
MD_FILES = ("priorities", "rules")
FILES = JSON_FILES + MD_FILES

LABELS = {
    "profile": "Your CV facts",
    "settings": "What you are looking for",
    "priorities": "What is worth applying to",
    "rules": "What your CV may claim",
}


def _paths_for(paths: Paths, name: str) -> tuple[Path, Path]:
    if name not in FILES:
        raise ValidationError(f"No setup file called {name!r}.")
    return getattr(paths, name), getattr(paths, f"{name}_example")


# --- checking ----------------------------------------------------------------------


def check_profile(profile: dict) -> list[str]:
    """Build every CV variant into a throwaway folder. Returns build_variant's warnings;
    raises ValidationError naming the first variant that cannot build."""
    variants = profile.get("variants") if isinstance(profile, dict) else None
    if not isinstance(variants, dict) or not variants:
        raise ValidationError("Add at least one CV variant.")
    warnings: list[str] = []
    # ignore_cleanup_errors: on Windows a virus scanner can still hold a just-written .docx
    # when the folder is removed; a leftover temp folder is harmless, a failed save is not.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        for key in variants:
            try:
                tl.cvbuild.build_variant(profile, key, warnings=warnings, out_dir=Path(tmp))
            # build_variant raises SystemExit for a variant it cannot find.
            except (Exception, SystemExit) as exc:  # noqa: BLE001
                detail = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
                raise ValidationError(f"The CV '{key}' does not build - {detail}") from None
    return warnings


def _check(name: str, data: dict) -> list[str]:
    if name == "settings":
        try:
            st.from_dict(data)
        except st.SettingsError as exc:
            raise ValidationError(str(exc)) from None
        return []
    return check_profile(data)


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError(
            f"{path.name} is not valid JSON ({exc.msg}, line {exc.lineno}) - fix it in a text "
            f"editor, or put back {path.name}.bak."
        ) from None


def status(paths: Paths) -> dict:
    files: dict[str, dict] = {}
    for name in FILES:
        path, _ = _paths_for(paths, name)
        entry = {"label": LABELS[name], "file": path.name, "exists": path.exists(), "valid": True}
        entry["error"] = ""
        if entry["exists"] and name in JSON_FILES:
            try:
                _check(name, _read_json(path))
            except ValidationError as exc:
                entry["valid"], entry["error"] = False, str(exc)
        files[name] = entry
    return {
        "files": files,
        "missing": [n for n in FILES if not files[n]["exists"]],
        "warnings": _cross_warnings(paths, files),
    }


def _cross_warnings(paths: Paths, files: dict) -> list[str]:
    """Problems between files: a role in settings.json whose CV the profile does not have."""
    ok = files["settings"]["exists"] and files["settings"]["valid"] and files["profile"]["exists"]
    if not ok or not files["profile"]["valid"]:
        return []
    variants = set(_read_json(paths.profile).get("variants", {}))
    s = st.from_dict(_read_json(paths.settings))
    out = []
    for role in s.roles:
        for cv in [role.cv, *(rule.cv for rule in role.cv_rules)]:
            if cv not in variants:
                out.append(
                    f"The job type '{role.name}' uses the CV '{cv}', which your profile does "
                    f"not have. Add that CV, or pick one of: {', '.join(sorted(variants))}."
                )
    return out


def missing(paths: Paths) -> list[str]:
    """Cheap: which files do not exist yet (for /api/state, polled on every action)."""
    return [n for n in FILES if not _paths_for(paths, n)[0].exists()]


# --- Markdown ----------------------------------------------------------------------


def _split(text: str) -> tuple[str, list[dict]]:
    preamble: list[str] = []
    sections: list[dict] = []
    for line in text.splitlines():
        if line.startswith("## "):
            sections.append({"heading": line[3:].strip(), "lines": []})
        elif sections:
            sections[-1]["lines"].append(line)
        else:
            preamble.append(line)
    return "\n".join(preamble).strip(), [
        {"heading": s["heading"], "body": "\n".join(s["lines"]).strip()} for s in sections
    ]


def _join(preamble: str, sections: list[dict]) -> str:
    parts = [preamble.strip()] if preamble.strip() else []
    for s in sections:
        body = str(s.get("body", "")).strip()
        parts.append(f"## {s['heading']}" + (f"\n\n{body}" if body else ""))
    return "\n\n".join(parts) + "\n"


# --- load and save -----------------------------------------------------------------


def load(paths: Paths, name: str) -> dict:
    path, example = _paths_for(paths, name)
    source = path if path.exists() else example
    from_example = source == example
    if name in JSON_FILES:
        data = _read_json(source)
        return {
            "name": name,
            "label": LABELS[name],
            "data": data,
            "from_example": from_example,
            "schema": SCHEMAS[name],
            "context": _context(paths),
        }
    preamble, sections = _split(source.read_text(encoding="utf-8"))
    guidance = {s["heading"]: s["body"] for s in _split(example.read_text(encoding="utf-8"))[1]}
    for s in sections:
        s["help"] = guidance.get(s["heading"], "")
    return {
        "name": name,
        "label": LABELS[name],
        "preamble": preamble,
        "sections": sections,
        "from_example": from_example,
    }


def _context(paths: Paths) -> dict:
    """Choices the settings form offers that live in the other file: your CV variants."""
    source = paths.profile if paths.profile.exists() else paths.profile_example
    try:
        variants = sorted(_read_json(source).get("variants", {}))
    except ValidationError:
        variants = []
    return {"variants": variants}


def _write(path: Path, text: str) -> None:
    """Atomic, with the previous version kept as <name>.bak."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak"))
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def save(paths: Paths, name: str, payload: dict) -> dict:
    path, _ = _paths_for(paths, name)
    if name in JSON_FILES:
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ValidationError("Nothing to save - the form sent no data.")
        warnings = _check(name, data)
        _write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        return {"saved": path.name, "warnings": warnings}
    sections = payload.get("sections")
    if not isinstance(sections, list):
        raise ValidationError("Nothing to save - the form sent no sections.")
    for s in sections:
        heading = str(s.get("heading", "")).strip() if isinstance(s, dict) else ""
        if not heading or "\n" in heading:
            raise ValidationError("Every section needs a one-line heading.")
    _write(path, _join(str(payload.get("preamble", "")), sections))
    return {"saved": path.name, "warnings": []}


# --- form overrides ----------------------------------------------------------------
# Keys are dotted paths; `*` matches any list index or map key. Everything not listed is
# drawn from the data itself. Choices naming a source ("variants", "roles", ...) are
# filled in by the page from the data being edited, or from `context`.

_BULLETS = {
    "label": "Bullets",
    "template": {"text": "", "tags": []},
}
_BULLET_TAGS = {"label": "Show on these CVs", "choices": "variants", "multi": True}

SCHEMAS: dict[str, dict[str, dict]] = {
    "settings": {
        "you": {"label": "About you"},
        "you.home_country": {"label": "Country you live in"},
        "you.home_places": {"label": "Cities and areas you live in, one per line"},
        "you.authorized_countries": {"label": "Countries you may work in without a visa"},
        "you.regions": {"label": "Regions that include you (Europe, EMEA, ...)"},
        "you.utc_offset": {"label": "Your time zone, as hours from UTC"},
        "you.years_experience": {"label": "Years of paid experience"},
        "you.needs_visa_sponsorship": {"label": "I need visa sponsorship to move abroad"},
        "location": {"label": "Where you would work"},
        "location.on_site_at_home": {
            "label": "Office jobs in your home country",
            "choices": ["ok", "reject"],
        },
        "location.relocation_targets": {
            "label": "Countries you would move to",
            "template": {"country": "", "places": []},
        },
        "location.accept_country_locks": {"label": "Country-locked remote jobs you accept"},
        "location.max_timezone_gap_hours": {"label": "Largest time-zone gap you accept (hours)"},
        "location.preferred_regions": {"label": "Regions you prefer"},
        "roles": {
            "label": "Kinds of job you apply for, best first",
            "template": {
                "name": "",
                "titles": [],
                "cv": "",
                "points": 50,
                "reason": "",
                "max_level": "mid",
            },
        },
        "roles.*.name": {"label": "Short name"},
        "roles.*.titles": {"label": "Job titles this covers (* = any letters)"},
        "roles.*.not_titles": {"label": "Titles that are not this, even if they match"},
        "roles.*.not_titles_unless": {"label": "...unless the title also says"},
        "roles.*.cv": {"label": "CV to send", "choices": "variants"},
        "roles.*.cv_rules": {
            "label": "Send a different CV when",
            "template": {"title": [], "cv": ""},
        },
        "roles.*.cv_rules.*.cv": {"label": "CV to send instead", "choices": "variants"},
        "roles.*.points": {"label": "Points for a match"},
        "roles.*.reason": {"label": "Label shown on the lead"},
        "roles.*.interest": {"label": "Theme this role counts as", "choices": "interests"},
        "roles.*.max_level": {
            "label": "Most senior level you apply for",
            "choices": list(st.LEVELS),
        },
        "roles.*.above_level": {
            "label": "Above that level",
            "choices": ["penalise", "reject"],
        },
        "roles.*.internships": {
            "label": "Internships (true, false, or a theme that makes one worth it)",
            "boolstr": True,
        },
        "exclude_titles": {"label": "Never show jobs whose title contains"},
        "exclude_departments": {"label": "Never show jobs in these departments"},
        "interests": {
            "label": "Themes worth extra points",
            "template": {"name": "", "terms": [], "title_points": 10, "text_points": 5},
        },
        "description_bonus": {
            "label": "Words in a description worth extra points",
            "template": {"terms": [], "points": 5},
        },
        "search_queries": {"label": "Search terms for the job boards that need one"},
        "scoring": {"label": "Scoring"},
        "scoring.min_score": {"label": "Lowest score that makes the sheet"},
        "scoring.max_per_company": {"label": "Most leads from one company per day"},
        "tailoring": {"label": "Tailoring"},
        "tailoring.skills": {
            "label": "Skills a CV may headline, and the words that mean each",
            "map": True,
            "template": [],
        },
        "tailoring.gaps": {
            "label": "Skills you lack, and the words that mean each (never printed)",
            "map": True,
            "template": [],
        },
        "tailoring.weak": {"label": "Skills too thin to headline alone"},
        "llm": {"label": "LLM verdicts for Paste a job (optional)"},
        "llm.provider": {
            "label": "Provider (leave empty for none)",
            "choices": ["", *st.LLM_PROVIDERS],
        },
        "llm.api_key_env": {"label": "Environment variable holding the key"},
    },
    "profile": {
        "meta": {"label": "Layout"},
        "meta.accent": {"label": "Accent colour (hex, without #)"},
        "meta.density": {"label": "Density (lower fits more on a page)"},
        "contact": {"label": "Contact details"},
        "links": {
            "label": "Links",
            "map": True,
            "template": {"label": "", "url": ""},
        },
        "roles": {
            "label": "Work experience, newest first",
            "template": {
                "id": "",
                "title": "",
                "org": "",
                "location": "",
                "start": "",
                "end": "Present",
                "bullets": [{"text": "", "tags": []}],
            },
        },
        "roles.*.id": {"label": "Short id (letters, digits, _)"},
        "roles.*.start": {"label": "Start (MM/YYYY)"},
        "roles.*.end": {"label": "End (MM/YYYY or Present)"},
        "roles.*.bullets": _BULLETS,
        "roles.*.bullets.*.text": {"label": "Bullet", "long": True},
        "roles.*.bullets.*.tags": _BULLET_TAGS,
        "creative": {"label": "Creative and personal projects"},
        "creative.*.bullets": _BULLETS,
        "creative.*.bullets.*.text": {"label": "Bullet", "long": True},
        "creative.*.bullets.*.tags": _BULLET_TAGS,
        "skill_groups": {
            "label": "Skill groups",
            "map": True,
            "template": {"title": "", "items": []},
        },
        "education": {
            "label": "Education",
            "template": {"degree": "", "school": "", "start": "", "end": ""},
        },
        "certifications": {"label": "Certifications, one per line"},
        "variants": {"label": "CV variants", "map": True},
        "variants.*.summary": {"label": "Summary", "long": True},
        "variants.*.roles": {"label": "Roles on this CV", "choices": "roles", "multi": True},
        "variants.*.creative": {
            "label": "Projects on this CV",
            "choices": "creative",
            "multi": True,
        },
        "variants.*.links": {"label": "Links on this CV", "choices": "links", "multi": True},
        "variants.*.skill_groups": {
            "label": "Skill groups on this CV",
            "choices": "skill_groups",
            "multi": True,
        },
    },
}


# --- the guided start: cv/init.py's interview as one form --------------------------


def _init_module():
    """cv/init.py, which is a script rather than a package - loaded by path."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("fyj_cv_init", tl.ROOT / "cv" / "init.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def guided_template() -> dict:
    """The blank answers the guided start's form begins from."""
    return {
        "contact": {"name": "", "phone": "", "email": "", "location": ""},
        "links": {"linkedin": {"label": "LinkedIn", "url": ""}},
        "headline": "",
        "years": "nearly 5 years",
        "ai_tools": "",
        "summary": "",
        "skill_groups": {"core": {"title": "", "items": []}},
        "roles": [
            {
                "id": "",
                "title": "",
                "org": "",
                "location": "",
                "start": "",
                "end": "Present",
                "bullets": [],
            }
        ],
        "education": [{"degree": "", "school": "", "start": "", "end": ""}],
        "certifications": [],
        "languages": "English (fluent)",
        "variant_key": "",
        "filename": "",
        "folder": "",
    }


GUIDED_SCHEMA: dict[str, dict] = {
    "contact": {"label": "Contact details (printed at the top of every CV)"},
    "contact.phone": {"label": "Phone, with country code"},
    "contact.location": {"label": "Location, as it should print"},
    "links": {
        "label": "Links (LinkedIn, portfolio...)",
        "map": True,
        "template": {"label": "", "url": ""},
    },
    "headline": {"label": "Headline under your name, e.g. Automation engineer | QA"},
    "years": {"label": "Years of experience, in words"},
    "ai_tools": {"label": "AI tools you use daily (optional)"},
    "summary": {
        "label": "Professional summary - {years} and {ai_tools} fill themselves in",
        "long": True,
    },
    "skill_groups": {
        "label": "Skill groups, two to four",
        "map": True,
        "template": {"title": "", "items": []},
    },
    "skill_groups.*.items": {"label": "Skills, one per line"},
    "roles": {"label": "Work experience, newest first", "template": guided_template()["roles"][0]},
    "roles.*.id": {"label": "Short id (letters, digits, _), e.g. acme_qa"},
    "roles.*.start": {"label": "Start (MM/YYYY)"},
    "roles.*.end": {"label": "End (MM/YYYY, or Present)"},
    "roles.*.bullets": {"label": "What you owned and changed, one per line"},
    "education": {"label": "Education", "template": guided_template()["education"][0]},
    "education.*.start": {"label": "Start (MM/YYYY)"},
    "education.*.end": {"label": "End (MM/YYYY)"},
    "certifications": {"label": "Certifications, one per line (optional)"},
    "languages": {"label": "Languages, as one line"},
    "variant_key": {"label": "A short name for this first CV (letters, digits, _), e.g. qa"},
    "filename": {"label": "File name for the CV (optional)"},
    "folder": {"label": "Folder name under cv/out/ (optional)"},
}


def _require(value, what: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValidationError(f"Fill in {what}.")
    return text


def start_profile(paths: Paths, answers: dict) -> dict:
    """Create profile.json from the guided form - the same questions as cv/init.py."""
    from jobs.errors import JobsError

    if paths.profile.exists():
        raise JobsError("You already have a profile - edit it instead.")
    init = _init_module()

    def date(value, what: str, *, present: bool = False) -> str:
        text = _require(value, what)
        if present and text.lower() in ("present", "now", "current"):
            return "Present"
        if not init.DATE_RE.match(text):
            raise ValidationError(f"{what}: write it as MM/YYYY, e.g. 03/2021.")
        return text

    def slug(value, what: str) -> str:
        text = _require(value, what)
        if not init.SLUG_RE.match(text):
            raise ValidationError(
                f"{what}: use lower-case letters, digits and _ only, starting with a letter."
            )
        return text

    c = answers.get("contact") or {}
    contact = {
        "name": _require(c.get("name"), "your name"),
        "phone": str(c.get("phone") or "").strip(),
        "email": _require(c.get("email"), "your email"),
        "location": str(c.get("location") or "").strip(),
    }
    if not init.EMAIL_RE.match(contact["email"]):
        raise ValidationError("Your email does not look like an email address.")

    links = {}
    for key, link in (answers.get("links") or {}).items():
        if isinstance(link, dict) and str(link.get("url") or "").strip():
            links[slug(key, "Each link's short name")] = {
                "label": _require(link.get("label"), f"the text shown for the {key} link"),
                "url": str(link["url"]).strip(),
            }

    groups = {}
    for key, group in (answers.get("skill_groups") or {}).items():
        items = [str(i).strip() for i in (group or {}).get("items", []) if str(i).strip()]
        if items:
            groups[slug(key, "Each skill group's short name")] = {
                "title": _require(group.get("title"), f"a heading for the {key} skill group"),
                "items": items,
            }
    if not groups:
        raise ValidationError("Add at least one skill group with skills in it.")

    roles = []
    for i, r in enumerate(answers.get("roles") or [], 1):
        if not any(str(r.get(k) or "").strip() for k in ("title", "org", "id")):
            continue  # a blank row the form left behind
        role = {
            "id": slug(r.get("id"), f"Role {i}'s short id"),
            "title": _require(r.get("title"), f"role {i}'s job title"),
            "org": _require(r.get("org"), f"role {i}'s employer"),
            "start": date(r.get("start"), f"Role {i}'s start date"),
            "end": date(r.get("end"), f"Role {i}'s end date", present=True),
            "bullets": [str(b).strip() for b in r.get("bullets") or [] if str(b).strip()],
        }
        if not role["bullets"]:
            raise ValidationError(f"Role {i} needs at least one bullet.")
        if str(r.get("location") or "").strip():
            role["location"] = str(r["location"]).strip()
        roles.append(role)
    if not roles:
        raise ValidationError("Add at least one role - the CV is built around them.")

    education = []
    for i, e in enumerate(answers.get("education") or [], 1):
        if not any(str(e.get(k) or "").strip() for k in ("degree", "school")):
            continue
        education.append(
            {
                "degree": _require(e.get("degree"), f"education {i}'s degree"),
                "school": _require(e.get("school"), f"education {i}'s institution"),
                "start": date(e.get("start"), f"Education {i}'s start date"),
                "end": date(e.get("end"), f"Education {i}'s end date"),
            }
        )

    key = slug(answers.get("variant_key"), "The CV's short name")
    surname = contact["name"].split()[-1]
    filename = str(answers.get("filename") or "").strip()
    folder = str(answers.get("folder") or "").strip()
    profile = init.assemble(
        {
            "contact": contact,
            "links": links,
            "headline": _require(answers.get("headline"), "a headline").upper(),
            "years": str(answers.get("years") or "").strip(),
            "ai_tools": str(answers.get("ai_tools") or "").strip(),
            "summary": _require(answers.get("summary"), "a summary"),
            "skill_groups": groups,
            "roles": roles,
            "education": education,
            "certifications": [
                str(x).strip() for x in answers.get("certifications") or [] if str(x).strip()
            ],
            "languages": str(answers.get("languages") or "").strip(),
            "variant_key": key,
            "variant": {
                "filename": filename or f"{surname}_{key.title().replace('_', '')}_CV",
                "folder": folder or key.replace("_", " ").title(),
            },
        }
    )
    return save(paths, "profile", {"data": profile})


# --- preview -----------------------------------------------------------------------


def preview(paths: Paths, variant: str, report) -> dict:
    """Build one CV variant into cv/out/preview/, with the PDF step, to look at."""
    from jobs.errors import JobsError
    from jobs.progress import emit

    if not paths.profile.exists():
        raise JobsError("Save your profile first - the preview builds from the saved file.")
    profile = _read_json(paths.profile)
    if variant not in (profile.get("variants") or {}):
        raise ValidationError(f"Your profile has no CV called {variant!r}.")
    emit(report, "tailor", f"Building the {variant} CV…")
    warnings: list[str] = []
    pages: dict[Path, int] = {}
    engines: dict[Path, str] = {}
    docx, _ = tl.cvbuild.build_variant(profile, variant, warnings=warnings, out_dir=paths.preview)
    made = tl.cvbuild.to_pdf(
        [docx], keep_docx=True, pages_out=pages, engines_out=engines, warnings=warnings
    )
    pdf = made[0] if made else None

    def rel(p: Path | None) -> str | None:
        return None if p is None else p.relative_to(paths.root).as_posix()

    return {
        "variant": variant,
        "docx": rel(docx),
        "pdf": rel(pdf),
        "pages": pages.get(pdf) if pdf else None,
        "engine": engines.get(pdf) if pdf else None,
        "warnings": warnings,
    }
