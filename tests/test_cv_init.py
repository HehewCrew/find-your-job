"""The interview in cv/init.py has one job: produce a profile.json that builds.

So the test that matters is not "does assemble() emit the right keys" but "does
build.py render the result without warning that a role vanished". Everything
else here is guarding the answers a new user is most likely to give -- no
certifications, no links, one job.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str, path: Path):
    """cv/ is not a package and build.py is imported by jobs/tailor.py under its bare name,
    so load both modules from their file paths rather than restructuring the tree."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


init = _load("cv_init", ROOT / "cv" / "init.py")
build = _load("build", ROOT / "cv" / "build.py")


def answers(**overrides) -> dict:
    base = {
        "contact": {
            "name": "Jane Marchetti",
            "phone": "(31) 612345678",
            "email": "jane@example.com",
            "location": "Utrecht, Netherlands",
        },
        "links": {"linkedin": {"label": "LinkedIn", "url": "https://example.com/in/jane"}},
        "headline": "TEST ENGINEER | QA",
        "years": "six years",
        "ai_tools": "Copilot",
        "summary": "Engineer with {years} of experience and daily use of {ai_tools}.",
        "skill_groups": {"qa": {"title": "QA", "items": ["Test design", "Regression"]}},
        "roles": [
            {
                "id": "acme_qa",
                "title": "QA Engineer",
                "org": "Acme",
                "location": "Remote",
                "start": "01/2022",
                "end": "Present",
                "bullets": ["Owned the regression suite", "Cut flake by rewriting the fixtures"],
                "keywords": {"default": ["pytest", "CI/CD"]},
            }
        ],
        "education": [
            {
                "degree": "BSc Computer Science",
                "school": "TU Delft",
                "start": "09/2015",
                "end": "06/2018",
            }
        ],
        "certifications": ["ISTQB Foundation"],
        "languages": "English (fluent), Dutch (native)",
        "variant_key": "qa_test",
        "variant": {"filename": "Marchetti_QA_CV", "folder": "QA"},
    }
    base.update(overrides)
    return base


# -- shape --------------------------------------------------------------


def test_assemble_has_every_key_build_reads():
    profile = init.assemble(answers())
    for key in (
        "meta",
        "contact",
        "links",
        "roles",
        "creative",
        "skill_groups",
        "education",
        "certifications",
        "languages",
        "variants",
    ):
        assert key in profile, key


def test_bullets_are_tagged_with_the_variant():
    """An untagged bullet is dropped by build.pick, taking the whole role with it."""
    profile = init.assemble(answers())
    bullets = profile["roles"][0]["bullets"]
    assert all(b["tags"] == ["qa_test"] for b in bullets)
    assert build.pick(bullets, "qa_test") == [
        "Owned the regression suite",
        "Cut flake by rewriting the fixtures",
    ]


def test_variant_lists_every_role_and_skill_group():
    variant = init.assemble(answers())["variants"]["qa_test"]
    assert variant["roles"] == ["acme_qa"]
    assert variant["skill_groups"] == ["qa"]
    assert variant["links"] == ["linkedin"]


def test_no_certifications_turns_the_section_off():
    variant = init.assemble(answers(certifications=[]))["variants"]["qa_test"]
    assert variant["show_certifications"] is False


def test_certifications_leave_the_section_on():
    variant = init.assemble(answers())["variants"]["qa_test"]
    assert "show_certifications" not in variant


def test_open_ended_role_prints_as_present():
    profile = init.assemble(answers(roles=[{**answers()["roles"][0], "end": ""}]))
    assert profile["roles"][0]["end"] == "Present"


# -- the point ----------------------------------------------------------


def test_the_generated_profile_actually_builds(tmp_path, monkeypatch, capsys):
    profile = init.assemble(answers())
    monkeypatch.setattr(build, "OUT_DIR", tmp_path / "out")

    path, headline = build.build_variant(profile, "qa_test")

    assert path.exists()
    assert headline == "TEST ENGINEER | QA"
    # build_variant warns on stderr when a role has no bullets for the variant.
    assert "MISSING from the timeline" not in capsys.readouterr().err


def test_it_builds_with_the_thinnest_plausible_answers(tmp_path, monkeypatch, capsys):
    """No links, no certifications, no keywords -- what a first pass usually gives."""
    thin = answers(
        links={},
        certifications=[],
        roles=[
            {
                "id": "acme_qa",
                "title": "QA Engineer",
                "org": "Acme",
                "start": "01/2022",
                "end": "Present",
                "bullets": ["Owned the regression suite"],
            }
        ],
    )
    profile = init.assemble(thin)
    monkeypatch.setattr(build, "OUT_DIR", tmp_path / "out")

    path, _ = build.build_variant(profile, "qa_test")

    assert path.exists()
    assert "MISSING from the timeline" not in capsys.readouterr().err


def test_written_profile_is_utf8_json(tmp_path):
    """Names carry accents; the file is read back with encoding='utf-8' by build.py."""
    profile = init.assemble(answers(contact={**answers()["contact"], "name": "Zoé Boräs"}))
    out = tmp_path / "profile.json"
    out.write_text(json.dumps(profile, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    assert json.loads(out.read_text(encoding="utf-8"))["contact"]["name"] == "Zoé Boräs"


# -- the interview itself -----------------------------------------------


SCRIPT = [
    # 1. contact
    "Jane Marchetti",
    "(31) 612345678",
    "jane@example.com",
    "Utrecht, Netherlands",
    # 2. links: add LinkedIn, then stop
    "y",
    "https://example.com/in/jane",
    "n",
    # 3. headline, years, ai tools, summary
    "test engineer | qa",
    "six years",
    "Copilot",
    "Engineer with {years} of experience.",
    # 4. one skill group, then stop (the prompt defaults to yes after the first)
    "qa",
    "QA & Test",
    "Test design",
    "Regression",
    "",
    "n",
    # 5. one current role, then stop
    "acme_qa",
    "QA Engineer",
    "Acme",
    "Remote",
    "01/2022",
    "y",
    "Owned the regression suite",
    "",
    "pytest, CI/CD",
    "n",
    # 6. one education entry, then stop
    "BSc Computer Science",
    "TU Delft",
    "09/2015",
    "06/2018",
    "n",
    # 7. no certifications, accept the default languages line
    "n",
    "English (fluent)",
    # 8. variant key, then accept both defaults
    "qa_test",
    "",
    "",
]


def test_interview_end_to_end(monkeypatch, capsys):
    answers_left = list(SCRIPT)
    monkeypatch.setattr(init, "_input", lambda prompt: answers_left.pop(0))

    profile = init.interview()

    assert not answers_left, f"{len(answers_left)} scripted answers unused - prompts changed"
    assert profile["contact"]["name"] == "Jane Marchetti"
    assert profile["meta"]["years_experience"] == "six years"
    variant = profile["variants"]["qa_test"]
    assert variant["headline"] == "TEST ENGINEER | QA"  # lowercased input is raised to caps
    assert variant["show_certifications"] is False
    assert profile["roles"][0]["end"] == "Present"
    assert profile["roles"][0]["keywords"]["default"] == ["pytest", "CI/CD"]


def test_interview_output_builds(tmp_path, monkeypatch, capsys):
    answers_left = list(SCRIPT)
    monkeypatch.setattr(init, "_input", lambda prompt: answers_left.pop(0))
    profile = init.interview()

    monkeypatch.setattr(build, "OUT_DIR", tmp_path / "out")
    path, _ = build.build_variant(profile, "qa_test")

    assert path.exists()
    assert "MISSING from the timeline" not in capsys.readouterr().err


def test_ctrl_c_writes_nothing(tmp_path, monkeypatch):
    def interrupt(prompt):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", interrupt)
    out = tmp_path / "profile.json"
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)

    assert init.main(["--out", str(out)]) == 1
    assert not out.exists()


def test_refuses_to_clobber_an_existing_profile(tmp_path, capsys):
    out = tmp_path / "profile.json"
    out.write_text('{"keep": "me"}', encoding="utf-8")

    assert init.main(["--out", str(out)]) == 2
    assert json.loads(out.read_text(encoding="utf-8")) == {"keep": "me"}


# -- validators ---------------------------------------------------------


@pytest.mark.parametrize("value", ["04/2026", "12/1999", "01/2020"])
def test_valid_dates_accepted(value):
    assert init.valid_date(value) == ""


@pytest.mark.parametrize("value", ["4/2026", "2026-04", "13/2026", "04/26", "april 2026"])
def test_invalid_dates_rejected(value):
    assert init.valid_date(value)


@pytest.mark.parametrize("value", ["a@b.co", "jane.doe@example.org"])
def test_valid_emails_accepted(value):
    assert init.valid_email(value) == ""


@pytest.mark.parametrize("value", ["jane", "jane@", "@example.com", "jane @ example.com"])
def test_invalid_emails_rejected(value):
    assert init.valid_email(value)


@pytest.mark.parametrize("value", ["qa_test", "automation_qa", "sdet2"])
def test_valid_slugs_accepted(value):
    assert init.valid_slug(value) == ""


@pytest.mark.parametrize("value", ["QA Test", "2fast", "qa-test", ""])
def test_invalid_slugs_rejected(value):
    assert init.valid_slug(value)


def test_urls_must_be_absolute():
    assert init.valid_url("https://example.com") == ""
    assert init.valid_url("example.com")


# --- a profile missing a variant must say so ---------------------------------


def test_missing_variant_reports_which_one():
    """cv/init.py writes a single variant, but jobs/score.py picks from a fixed set of
    eight. The first game or data posting therefore hit a bare KeyError from inside the
    renderer, which said nothing about how to fix it."""
    import pytest
    from cv.build import build_variant

    profile = {"variants": {"automation_qa": {}}, "meta": {}, "contact": {}, "links": {}}
    with pytest.raises(SystemExit) as excinfo:
        build_variant(profile, "game_designer")
    message = str(excinfo.value)
    assert "game_designer" in message
    assert "automation_qa" in message
    assert "profile.example.json" in message
