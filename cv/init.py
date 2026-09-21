"""Interview a new user and write their cv/profile.json.

    python cv/init.py                 # -> cv/profile.json, then run build.py
    python cv/init.py --out mine.json # write somewhere else
    python cv/init.py --force         # overwrite an existing profile.json

The point of entry for someone who has a CV but not a profile.json. It asks for
what is on that CV, in the order a CV is written, and emits a profile that
`build.py` can render immediately -- one variant, every bullet tagged for it.

Deliberately one variant. The eight-variant setup in this repo grew one tailored
application at a time; asking a new user to invent eight up front would produce
eight copies of the same CV. Add the second variant by hand once there is a real
second audience for it -- cv/README.md says how.

No prompt here contains a non-ASCII character: this runs on a Windows console
under cp1252, where printing an em-dash raises UnicodeEncodeError. The JSON it
writes is UTF-8 and has no such limit.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROFILE = HERE / "profile.json"

DATE_RE = re.compile(r"^(0[1-9]|1[0-2])/(19|20)\d{2}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SLUG_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# Mirrors the meta block build.py expects. Only years_experience and ai_tools are
# asked for; the rest is typography the user can tune later without a rebuild of
# their answers.
META_DEFAULTS = {
    "font": "Calibri",
    "font_size_pt": 10,
    "name_size_pt": 18,
    "accent": "1F3864",
    "margin_twips": 850,
    "density": 0.9,
}


class Abort(Exception):
    """Ctrl-C or EOF. Caught in main so the user gets a sentence, not a traceback."""


# -- asking -------------------------------------------------------------


def _input(prompt: str) -> str:
    try:
        return input(prompt)
    except (KeyboardInterrupt, EOFError) as exc:
        raise Abort from exc


def ask(
    prompt: str,
    *,
    default: str = "",
    required: bool = True,
    validate: Callable[[str], str] | None = None,
) -> str:
    """One line of input. `validate` returns an error string, or "" if the answer is fine."""
    suffix = f" [{default}]" if default else ""
    while True:
        answer = _input(f"{prompt}{suffix}: ").strip() or default
        if not answer:
            if not required:
                return ""
            print("   (required)")
            continue
        problem = validate(answer) if validate else ""
        if problem:
            print(f"   {problem}")
            continue
        return answer


def ask_lines(prompt: str, *, required: bool = True) -> list[str]:
    """Repeated input until a blank line. Used for bullets, skills, certifications."""
    print(f"{prompt}")
    print("   (one per line; blank line when done)")
    items: list[str] = []
    while True:
        line = _input("   - ").strip()
        if not line:
            if items or not required:
                return items
            print("   (at least one)")
            continue
        items.append(line)


def ask_csv(prompt: str, *, required: bool = False) -> list[str]:
    raw = ask(prompt, required=required)
    return [part.strip() for part in raw.split(",") if part.strip()]


def confirm(prompt: str, *, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    while True:
        answer = _input(f"{prompt} [{hint}]: ").strip().lower()
        if not answer:
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False


def heading(text: str) -> None:
    print()
    print(text)
    print("-" * len(text))


# -- validators ---------------------------------------------------------


def valid_date(value: str) -> str:
    return "" if DATE_RE.match(value) else "use MM/YYYY, e.g. 04/2026"


def valid_email(value: str) -> str:
    return "" if EMAIL_RE.match(value) else "that does not look like an email address"


def valid_url(value: str) -> str:
    return (
        "" if value.startswith(("http://", "https://")) else "must start with http:// or https://"
    )


def valid_slug(value: str) -> str:
    if not SLUG_RE.match(value):
        return "lowercase letters, digits and underscores; must start with a letter"
    return ""


# -- sections -----------------------------------------------------------


def section_contact() -> dict:
    heading("1. Contact details")
    print("These go in the header of every CV this builds.")
    return {
        "name": ask("Full name"),
        "phone": ask("Phone (with country code)"),
        "email": ask("Email", validate=valid_email),
        "location": ask("Location, as it should print", default="City, Country"),
    }


def section_links() -> dict:
    heading("2. Links")
    print("Printed in the header next to your contact details. LinkedIn is usual;")
    print("a portfolio or a published article is worth adding if you have one.")
    links: dict[str, dict] = {}
    if confirm("Add a LinkedIn profile?"):
        links["linkedin"] = {
            "label": "LinkedIn",
            "url": ask("   LinkedIn URL", validate=valid_url),
        }
    while confirm("Add another link?", default=False):
        key = ask("   Short key (e.g. portfolio, github)", validate=valid_slug)
        links[key] = {
            "label": ask("   Text to show on the CV"),
            "url": ask("   URL", validate=valid_url),
        }
    return links


def section_summary() -> tuple[str, str, str, str]:
    heading("3. Headline and summary")
    print("The headline sits under your name, in caps, e.g.")
    print("   AUTOMATION ENGINEER | QA SPECIALIST")
    headline = ask("Headline").upper()
    years = ask("Years of experience, in words", default="nearly 5 years")
    ai_tools = ask(
        "AI tools you use daily (skip if none apply)",
        default="GitHub Copilot and Claude",
        required=False,
    )
    print()
    print("Now the Professional Summary paragraph. Write it as one block of prose.")
    print("You may use {years} and {ai_tools} as placeholders for the two answers above.")
    summary = ask("Summary")
    return headline, years, ai_tools, summary


def section_skills() -> dict:
    heading("4. Core skills")
    print("Grouped, because a flat list of thirty skills reads as noise. Two to four")
    print("groups is normal, e.g. 'QA & Test Engineering', 'Programming & Data'.")
    groups: dict[str, dict] = {}
    while True:
        key = ask(f"Group {len(groups) + 1} short key (e.g. qa_test)", validate=valid_slug)
        groups[key] = {
            "title": ask("   Group heading, as it prints"),
            "items": ask_lines("   Skills in this group"),
        }
        if not confirm("Add another skill group?", default=len(groups) < 2):
            return groups


def section_roles() -> list[dict]:
    heading("5. Work experience")
    print("Newest first. Bullets are the substance of the CV -- what you owned and")
    print("what changed because you were there, not a list of duties.")
    roles: list[dict] = []
    while True:
        print()
        print(f"Role {len(roles) + 1}")
        role_id = ask("   Short id for this role (e.g. acme_qa)", validate=valid_slug)
        title = ask("   Job title")
        org = ask("   Employer")
        location = ask("   Location", required=False)
        start = ask("   Start date (MM/YYYY)", validate=valid_date)
        current = confirm("   Is this your current role?", default=not roles)
        end = "" if current else ask("   End date (MM/YYYY)", validate=valid_date)
        bullets = ask_lines("   Bullets")
        keywords = ask_csv("   Keywords, comma separated (optional, printed in small italics)")

        role: dict = {
            "id": role_id,
            "title": title,
            "org": org,
            "start": start,
            "end": "Present" if current else end,
            "bullets": bullets,  # tagged later, once the variant key is known
        }
        if location:
            role["location"] = location
        if keywords:
            role["keywords"] = {"default": keywords}
        roles.append(role)

        if not confirm("Add another role?", default=True):
            return roles


def section_education() -> list[dict]:
    heading("6. Education")
    entries: list[dict] = []
    while True:
        entries.append(
            {
                "degree": ask("   Degree or qualification"),
                "school": ask("   Institution"),
                "start": ask("   Start (MM/YYYY)", validate=valid_date),
                "end": ask("   End (MM/YYYY)", validate=valid_date),
            }
        )
        if not confirm("Add another education entry?", default=False):
            return entries


def section_extras() -> tuple[list[str], str]:
    heading("7. Certifications and languages")
    certs: list[str] = []
    if confirm("Do you have certifications to list?", default=False):
        certs = ask_lines("   Certifications (include the credential id if you have one)")
    languages = ask("Languages, as one line", default="English (fluent)")
    return certs, languages


def section_variant(name: str) -> tuple[str, dict]:
    heading("8. Output")
    print("A 'variant' is one CV aimed at one kind of role. You are creating the")
    print("first; add more later by copying the block in profile.json.")
    key = ask("Variant key (e.g. automation_qa)", validate=valid_slug)
    surname = name.split()[-1] if name.split() else "CV"
    default_file = f"{surname}_{key.title().replace('_', '')}_CV"
    return key, {
        "filename": ask("PDF filename, without extension", default=default_file),
        "folder": ask("Subfolder under cv/out/", default=key.replace("_", " ").title()),
    }


# -- assembly -----------------------------------------------------------


def assemble(answers: dict) -> dict:
    """Fold the answers into the shape build.py reads.

    Every bullet is tagged with the one variant key. build.py drops bullets that
    carry no tag for the variant being rendered and warns that the role vanished
    from the timeline -- untagged bullets would make a new user's first build
    print warnings and produce a CV with no jobs on it.
    """
    key = answers["variant_key"]
    roles = []
    for role in answers["roles"]:
        role = dict(role)
        role["bullets"] = [{"text": text, "tags": [key]} for text in role["bullets"]]
        if not role["end"] or role["end"] == "Present":
            role["end"] = "Present"
        roles.append(role)

    variant = dict(answers["variant"])
    variant.update(
        {
            "headline": answers["headline"],
            "summary": answers["summary"],
            "links": list(answers["links"]),
            "skill_groups": list(answers["skill_groups"]),
            "roles": [r["id"] for r in roles],
        }
    )
    if not answers["certifications"]:
        variant["show_certifications"] = False

    return {
        "_comment": (
            "Single source of truth for every CV variant. Edit here, then run: python cv/build.py"
        ),
        "meta": {
            "years_experience": answers["years"],
            "ai_tools": answers["ai_tools"] or "",
            **META_DEFAULTS,
        },
        "contact": answers["contact"],
        "links": answers["links"],
        "roles": roles,
        "creative": [],
        "skill_groups": answers["skill_groups"],
        "education": answers["education"],
        "certifications": answers["certifications"],
        "languages": answers["languages"],
        "variants": {key: variant},
    }


def interview() -> dict:
    print("This asks for what is already on your CV and writes cv/profile.json.")
    print("Ten minutes, and nothing here is final -- profile.json is a text file you")
    print("can edit afterwards. Ctrl-C to stop; nothing is written until the end.")

    contact = section_contact()
    links = section_links()
    headline, years, ai_tools, summary = section_summary()
    skill_groups = section_skills()
    roles = section_roles()
    education = section_education()
    certifications, languages = section_extras()
    variant_key, variant = section_variant(contact["name"])

    return assemble(
        {
            "contact": contact,
            "links": links,
            "headline": headline,
            "years": years,
            "ai_tools": ai_tools,
            "summary": summary,
            "skill_groups": skill_groups,
            "roles": roles,
            "education": education,
            "certifications": certifications,
            "languages": languages,
            "variant_key": variant_key,
            "variant": variant,
        }
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Create cv/profile.json by interview.")
    ap.add_argument("--out", type=Path, default=PROFILE, help="where to write")
    ap.add_argument("--force", action="store_true", help="overwrite an existing file")
    args = ap.parse_args(argv)

    if args.out.exists() and not args.force:
        print(f"{args.out} already exists. Pass --force to overwrite it.", file=sys.stderr)
        return 2
    if not sys.stdin.isatty():
        print(
            "This needs an interactive terminal -- run it directly, not from a pipe.",
            file=sys.stderr,
        )
        return 2

    try:
        profile = interview()
    except Abort:
        print("\nStopped. Nothing was written.", file=sys.stderr)
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(profile, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")

    variant = next(iter(profile["variants"]))
    print()
    print(f"Written: {args.out}")
    print(f"Variant: {variant} ({len(profile['roles'])} roles)")
    print()
    print("Next:")
    print("   python cv/build.py       build it, then read the PDF in cv/out/")
    print("   jobs/README.md           before scraping: the scoring constants in")
    print("                            jobs/score.py are one person's priorities")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
