"""The settings layer, and the scorer run for people who are not the fixture persona.

tests/test_score.py pins one search - a QA engineer leaving Tunisia - because that is the
search the scorer was built around. These pin the point of moving it into settings: the
same code ranks correctly for someone who lives elsewhere and wants different things.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobs import settings  # noqa: E402
from jobs.score import rank, score  # noqa: E402
from jobs.sources import Posting  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# The smallest settings file that scores anything.
BASE = {
    "you": {"home_country": "Portugal", "regions": ["europe", "eu"]},
    "roles": [{"name": "qa", "titles": ["qa", "tester", "test engineer"], "cv": "automation_qa"}],
}


def cfg(**changes) -> settings.Settings:
    data = copy.deepcopy(BASE)
    for key, value in changes.items():
        data[key] = value
    return settings.from_dict(data)


def make(title="QA Engineer", *, location="Remote", desc="", countries=None) -> Posting:
    return Posting(
        source="test",
        company="Acme",
        title=title,
        url="http://x",
        location=location,
        description=desc,
        countries=countries or [],
    )


# --- phrase syntax -------------------------------------------------------------


def test_a_phrase_matches_whole_words_only():
    pat = settings.phrases(["qa"], key="t")
    assert pat.search("Senior QA Engineer")
    assert not pat.search("Remote - Qatar")


def test_a_star_is_any_letters_so_designer_matches_design():
    pat = settings.phrases(["game design*"], key="t")
    assert pat.search("Game Designer")
    assert pat.search("Game Design Intern")
    assert not settings.phrases(["game design"], key="t").search("Game Designer")


def test_re_prefix_is_a_raw_regex():
    assert settings.phrases([r"re:\bsdet\b|in test"], key="t").search("Software Engineer in Test")


@pytest.mark.parametrize(
    ("title", "level"),
    [
        ("QA Engineer", None),
        ("Senior QA Engineer", "senior"),
        ("QA Engineer II", "senior"),
        ("Lead Game Designer", "lead"),
        ("Principal QA Engineer", "principal"),
        ("Junior Data Analyst", "junior"),
        ("Senior Game Design Intern", "intern"),
    ],
)
def test_level_of_reads_the_lowest_band_a_title_states(title, level):
    assert settings.level_of(title) == level


def test_country_aliases_compare_equal():
    assert settings.canonical_country("USA") == settings.canonical_country("United States")
    assert settings.canonical_country(" The Netherlands ") == "netherlands"


def test_eu_expands_to_its_member_states():
    s = cfg(you={"home_country": "Portugal", "authorized_countries": ["EU"]})
    assert s.can_work_in("Germany") and s.can_work_in("Portugal")
    assert not s.can_work_in("United Kingdom")


# --- validation names the key --------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"you": {}}, "home_country"),
        ({"roles": []}, "roles"),
        ({"roles": [{"name": "qa", "titles": ["qa"]}]}, "cv"),
        (
            {"roles": [{"name": "qa", "titles": ["qa"], "cv": "x", "max_level": "boss"}]},
            "max_level",
        ),
        ({"roles": [{"name": "qa", "titles": ["qa"], "cv": "x", "interest": "nope"}]}, "nope"),
        ({"llm": {"provider": "skynet"}}, "llm.provider"),
        ({"location": {"on_site_at_home": "maybe"}}, "on_site_at_home"),
        ({"exclude_titles": "devops"}, "exclude_titles"),
    ],
)
def test_bad_settings_are_refused_with_the_key_named(changes, message):
    with pytest.raises(settings.SettingsError, match=message):
        cfg(**changes)


def test_a_broken_file_is_a_settings_error_not_a_traceback(tmp_path):
    bad = tmp_path / "settings.json"
    bad.write_text("{ not json", encoding="utf-8")
    with pytest.raises(settings.SettingsError, match="not valid JSON"):
        settings.load(bad)


# --- the shipped example -------------------------------------------------------


def test_the_example_settings_load():
    s = settings.load(settings.EXAMPLE_PATH)
    assert s.is_example
    assert s.roles and s.search_queries


def test_every_variant_the_example_can_choose_exists_in_the_example_profile():
    """A fresh clone runs the example settings against the example profile. A cv name in
    one with no variant in the other only fails at build time, on the first posting."""
    profile = json.loads((ROOT / "cv" / "profile.example.json").read_text(encoding="utf-8"))
    assert settings.load(settings.EXAMPLE_PATH).missing_variants(profile) == []


def test_the_example_contains_no_json_backspace():
    # A backslash-b in JSON is chr(8); the loader refuses it, so loading is the test -
    # this one says why if it ever fails.
    assert chr(8) not in json.dumps(json.loads(settings.EXAMPLE_PATH.read_text("utf-8")))


# --- the scorer, for people who are not the fixture ---------------------------


def american() -> settings.Settings:
    return cfg(
        you={"home_country": "United States", "regions": ["north america", "americas"]},
        location={"on_site_at_home": "ok"},
    )


def test_us_only_is_no_lock_for_someone_who_lives_there():
    s = score(
        make(location="Remote (US)", desc="US only. Must be authorized to work in the US."),
        american(),
    )
    assert s.ok, s.rejected
    assert not any(r.startswith("eligibility") for r in s.reasons)


def test_us_only_is_still_a_wall_for_everyone_else():
    s = score(make(location="Remote (US)", desc="US only."), cfg())
    assert not s.ok and s.rejected == "US only"


def test_an_office_job_at_home_is_its_own_tier_when_you_are_staying():
    s = score(make(location="Austin, TX", desc="on-site in our Austin office"), american())
    assert s.ok and "local" in s.reasons


def test_an_office_job_at_home_is_dropped_when_you_are_leaving():
    leaving = cfg(location={"on_site_at_home": "reject"})
    s = score(make(location="Lisbon, Portugal", desc="office based"), leaving)
    assert not s.ok and "Portugal" in s.rejected


def test_hybrid_where_you_live_is_not_an_eligibility_lock():
    s = score(make(location="Lisbon, Portugal", desc="hybrid, two days a week"), cfg())
    assert not any(r.startswith("eligibility") for r in s.reasons)


def test_eu_only_locks_out_someone_outside_the_eu_but_not_someone_in_it():
    posting = make(location="Remote", desc="Fully remote - EU only.")
    inside = score(posting, cfg())
    outside = score(posting, cfg(you={"home_country": "Brazil", "regions": ["latam"]}))
    assert not any(r.startswith("eligibility") for r in inside.reasons)
    assert any(r.startswith("eligibility") for r in outside.reasons)
    assert inside.score > outside.score


def test_a_hiring_list_naming_your_country_is_open_to_you():
    germany = cfg(you={"home_country": "Germany", "regions": ["europe"]})
    s = score(make(location="Remote - Germany", countries=["Germany"]), germany)
    assert s.ok and "remote" in s.reasons[0]
    assert not any(r.startswith("eligibility") for r in s.reasons)


def test_a_hiring_list_elsewhere_is_dropped_unless_you_would_go_there():
    posting = make(location="Remote - Canada", countries=["Canada"])
    assert not score(posting, cfg()).ok
    willing = cfg(location={"accept_country_locks": ["Canada"]})
    kept = score(posting, willing)
    assert kept.ok and any("hires only in" in r for r in kept.reasons)


@pytest.mark.parametrize(("desc", "locked"), [("Hours: UTC-8.", True), ("Hours: UTC+1.", False)])
def test_a_stated_offset_beyond_your_gap_is_a_lock(desc, locked):
    s = score(make(desc=desc), cfg(location={"max_timezone_gap_hours": 3}))
    assert any(r.startswith("eligibility") for r in s.reasons) is locked


def test_a_role_above_its_ceiling_can_be_rejected_or_penalised():
    strict = cfg(roles=[{**BASE["roles"][0], "max_level": "mid", "above_level": "reject"}])
    lenient = cfg(roles=[{**BASE["roles"][0], "max_level": "mid"}])
    assert not score(make("Senior QA Engineer"), strict).ok
    s = score(make("Senior QA Engineer"), lenient)
    assert s.ok and "above-level" in s.reasons


def test_the_first_matching_role_picks_the_cv_and_rules_can_redirect_it():
    s = cfg(
        roles=[
            {
                "name": "qa",
                "titles": ["qa"],
                "cv": "plain",
                "cv_rules": [{"text": ["fintech"], "cv": "finance"}],
            },
            {"name": "data", "titles": ["data analyst"], "cv": "data"},
        ]
    )
    assert score(make("QA Engineer"), s).variant == "plain"
    assert score(make("QA Engineer", desc="a fintech startup"), s).variant == "finance"
    assert score(make("QA Data Analyst"), s).variant == "plain"


def test_rank_uses_the_settings_threshold():
    picky = cfg(scoring={"min_score": 500})
    assert rank([make()], settings=picky) == []
    assert len(rank([make()], settings=cfg())) == 1
