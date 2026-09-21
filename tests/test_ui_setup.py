"""Setup: creating and editing the four personal files from the UI, without a text editor."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from jobs.ui import setup
from jobs.ui.paths import Paths

from jobtrack.models import ValidationError

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def paths(tmp_path):
    """tmp_path laid out like the repo, holding only the tracked example files."""
    p = Paths.under(tmp_path)
    for example in (p.settings_example, p.profile_example, p.priorities_example, p.rules_example):
        example.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / example.relative_to(tmp_path), example)
    return p


def settings_data(paths) -> dict:
    return json.loads(paths.settings_example.read_text(encoding="utf-8"))


def profile_data(paths) -> dict:
    return json.loads(paths.profile_example.read_text(encoding="utf-8"))


# --- status ------------------------------------------------------------------------


def test_a_fresh_clone_is_missing_all_four(paths):
    s = setup.status(paths)
    assert s["missing"] == ["profile", "settings", "priorities", "rules"]
    assert all(not f["exists"] for f in s["files"].values())


def test_status_reports_an_invalid_file_with_the_loaders_message(paths):
    bad = settings_data(paths)
    bad["roles"][0]["max_level"] = "overlord"
    paths.settings.write_text(json.dumps(bad), encoding="utf-8")
    f = setup.status(paths)["files"]["settings"]
    assert f["exists"] and not f["valid"] and "max_level" in f["error"]


def test_a_role_naming_a_missing_cv_variant_is_a_warning(paths):
    profile = profile_data(paths)
    del profile["variants"]["sdet"]
    paths.profile.write_text(json.dumps(profile), encoding="utf-8")
    paths.settings.write_text(paths.settings_example.read_text(encoding="utf-8"), encoding="utf-8")
    warnings = setup.status(paths)["warnings"]
    assert any("sdet" in w for w in warnings)


# --- JSON files --------------------------------------------------------------------


def test_load_starts_from_the_example_when_the_file_is_missing(paths):
    got = setup.load(paths, "settings")
    assert got["from_example"] is True
    assert got["data"]["you"]["home_country"] == "Portugal"
    assert "schema" in got


def test_save_keeps_keys_the_form_never_showed(paths):
    data = settings_data(paths)
    data["you"]["home_country"] = "Tunisia"
    data["_my_own_note"] = "kept"
    setup.save(paths, "settings", {"data": data})
    saved = json.loads(paths.settings.read_text(encoding="utf-8"))
    assert saved["you"]["home_country"] == "Tunisia"
    assert saved["_my_own_note"] == "kept"
    assert saved["you"]["_note"] == data["you"]["_note"]
    assert setup.load(paths, "settings")["from_example"] is False


def test_an_invalid_save_is_refused_and_writes_nothing(paths):
    data = settings_data(paths)
    data["roles"] = []
    with pytest.raises(ValidationError, match="roles"):
        setup.save(paths, "settings", {"data": data})
    assert not paths.settings.exists()


def test_saving_again_keeps_the_previous_version_as_bak(paths):
    data = settings_data(paths)
    setup.save(paths, "settings", {"data": data})
    data["you"]["home_country"] = "Tunisia"
    setup.save(paths, "settings", {"data": data})
    bak = json.loads(paths.settings.with_name("settings.json.bak").read_text(encoding="utf-8"))
    assert bak["you"]["home_country"] == "Portugal"


def test_a_profile_that_cannot_build_is_refused(paths, cv_sandbox):
    profile = profile_data(paths)
    profile["variants"]["sdet"]["roles"] = ["no_such_role"]
    with pytest.raises(ValidationError, match="sdet"):
        setup.save(paths, "profile", {"data": profile})
    assert not paths.profile.exists()


def test_a_valid_profile_saves_with_its_build_warnings(paths, cv_sandbox):
    result = setup.save(paths, "profile", {"data": profile_data(paths)})
    assert paths.profile.exists()
    assert isinstance(result["warnings"], list)


# --- Markdown files ----------------------------------------------------------------


def test_markdown_loads_as_sections_with_the_examples_guidance(paths):
    got = setup.load(paths, "priorities")
    headings = [s["heading"] for s in got["sections"]]
    assert "1. The non-negotiable" in headings
    first = got["sections"][headings.index("1. The non-negotiable")]
    assert first["help"] and first["body"] == first["help"]


def test_markdown_round_trips(paths):
    got = setup.load(paths, "rules")
    got["sections"][0]["body"] = "- Never claim a metric I cannot defend."
    setup.save(paths, "rules", {"preamble": got["preamble"], "sections": got["sections"]})
    again = setup.load(paths, "rules")
    assert again["from_example"] is False
    assert again["sections"][0]["body"] == "- Never claim a metric I cannot defend."
    assert [s["heading"] for s in again["sections"]] == [s["heading"] for s in got["sections"]]
    assert again["preamble"].strip() == got["preamble"].strip()


def test_an_unknown_file_name_is_refused(paths):
    with pytest.raises(ValidationError):
        setup.load(paths, "passwords")


# --- the guided start --------------------------------------------------------------


def answers(**over) -> dict:
    a = setup.guided_template()
    a.update(
        {
            "contact": {
                "name": "Sam Rivera",
                "phone": "+351 900 000 000",
                "email": "sam@example.com",
                "location": "Lisbon, Portugal",
            },
            "headline": "QA engineer | test automation",
            "summary": "QA engineer with {years} of test automation.",
            "skill_groups": {"qa": {"title": "QA", "items": ["Test design", "Python"]}},
            "roles": [
                {
                    "id": "acme_qa",
                    "title": "QA Engineer",
                    "org": "Acme",
                    "location": "Lisbon",
                    "start": "01/2022",
                    "end": "Present",
                    "bullets": ["Owned the regression suite"],
                }
            ],
            "education": [
                {"degree": "BSc Computing", "school": "Uni", "start": "09/2016", "end": "06/2019"}
            ],
            "variant_key": "qa",
        }
    )
    a.update(over)
    return a


def test_the_guided_start_writes_a_profile_that_builds(paths, cv_sandbox):
    setup.start_profile(paths, answers())
    profile = json.loads(paths.profile.read_text(encoding="utf-8"))
    assert list(profile["variants"]) == ["qa"]
    assert profile["variants"]["qa"]["headline"] == "QA ENGINEER | TEST AUTOMATION"
    assert setup.check_profile(profile) == []


def test_the_guided_start_refuses_to_overwrite_a_profile(paths, cv_sandbox):
    from jobs.errors import JobsError

    setup.start_profile(paths, answers())
    with pytest.raises(JobsError, match="already"):
        setup.start_profile(paths, answers())


@pytest.mark.parametrize(
    ("over", "field"),
    [
        (
            {"contact": {"name": "Sam", "phone": "", "email": "not-an-email", "location": ""}},
            "email",
        ),
        (
            {
                "roles": [
                    {
                        "id": "acme_qa",
                        "title": "QA",
                        "org": "Acme",
                        "start": "2022",
                        "end": "Present",
                        "bullets": ["x"],
                    }
                ]
            },
            "MM/YYYY",
        ),
        ({"roles": []}, "role"),
        ({"variant_key": "My CV"}, "letters"),
    ],
)
def test_the_guided_start_says_what_is_wrong(paths, cv_sandbox, over, field):
    with pytest.raises(ValidationError, match=field):
        setup.start_profile(paths, answers(**over))
    assert not paths.profile.exists()


# --- preview -----------------------------------------------------------------------


def test_preview_builds_one_cv_into_the_preview_folder(paths, cv_sandbox):
    setup.start_profile(paths, answers())
    events = []
    result = setup.preview(paths, "qa", events.append)
    docx = paths.root / result["docx"]
    assert docx.exists() and docx.is_relative_to(paths.preview)
    assert result["pdf"] is None and result["pages"] is None
    assert events and events[0].stage == "tailor"


def test_preview_needs_a_saved_profile(paths):
    from jobs.errors import JobsError

    with pytest.raises(JobsError, match="Save"):
        setup.preview(paths, "qa", None)
