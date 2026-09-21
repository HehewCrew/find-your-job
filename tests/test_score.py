"""Regression tests for job filtering and ranking.

Every case here corresponds to a bug that actually shipped into the wishlist once.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from jobs import settings  # noqa: E402
from jobs.score import (  # noqa: E402
    DEFAULT_MIN_SCORE,
    MAX_PER_COMPANY,
    _families,
    dedupe_key,
    excluded_by,
    pick_variant,
    rank,
    score,
    us_located,
)
from jobs.sources import Posting  # noqa: E402


def make(title: str, *, company="Acme", location="Remote", desc="", visa=False) -> Posting:
    return Posting(
        source="test",
        company=company,
        title=title,
        url="http://x",
        location=location,
        description=desc,
        visa_sponsorship=visa,
    )


# --- hard filter: priority 1 is non-negotiable ------------------------------


def test_onsite_tunisia_is_rejected():
    s = score(make("QA Engineer", location="Tunis, Tunisia", desc="on site"))
    assert not s.ok
    assert "Tunisia" in s.rejected


def test_remote_role_from_tunisia_is_allowed():
    s = score(make("QA Engineer", location="Tunis, Tunisia", desc="fully remote worldwide"))
    assert s.ok


# --- relevance gate ---------------------------------------------------------


@pytest.mark.parametrize(
    "title",
    [
        "Senior Sales Engineer",
        "Technical Account Manager",
        "Senior Graphic Designer",
        "Machine Learning Engineer",
        "DevOps Engineer with Splunk",
        "Engineering Manager, Platform",
        # These three reached the wishlist before the exclusion regex was repaired.
        "Research Engineer / Scientist, Frontier Red Team",
        "Staff+ Software Engineer, Safeguards Evals",
        "Staff AI Product Analyst",
    ],
)
def test_titles_outside_the_target_profile_are_excluded(title):
    # excluded_by(), not TITLE_EXCLUDE: the gate is two regexes since business
    # functions moved to DOMAIN_EXCLUDE, and "Senior Sales Engineer" is now caught
    # by the head check rather than the whole-title one.
    assert excluded_by(title), f"{title!r} should be excluded"
    assert not score(make(title)).ok


@pytest.mark.parametrize(
    "title",
    [
        "QA Engineer II",
        "Software Engineer in Test",
        "Game Designer, Balance",
        "Data Analyst - Supply",
        "AI Trainer",
        "Red Team Engineer, Safeguards",
    ],
)
def test_target_roles_survive_the_gate(title):
    assert not excluded_by(title), f"{title!r} should be kept"
    assert score(make(title)).ok


def test_unrelated_title_without_a_role_family_is_dropped():
    s = score(make("Warehouse Operative"))
    assert not s.ok
    assert "role family" in s.rejected


# --- studios say "Designer", not "Design" -----------------------------------
#
# Every one of these was dropped at the role gate. The pattern spelled out
# "level design", "gameplay design", "systems design" and closed each with \b -
# but "designer" puts a letter after "design", so the boundary never matched.
# Only "game designer" had been patched, which masked the bug for the rest.


@pytest.mark.parametrize(
    "title",
    [
        "Game Designer",
        "Gameplay Designer",
        "Lead Gameplay Designer",
        "Level Designer",
        "Narrative Designer",
        "Senior Systems Designer, Progression",
        "Combat Designer",
        "Encounter Designer",
        "Economy Designer",
        # the "<X> Design" spellings must keep working too
        "Game Design Intern",
        "Senior Game Designer, Balance - Wild Rift",
    ],
)
def test_designer_titles_reach_the_game_design_family(title):
    assert "game_design" in _families(title.lower()), f"{title!r} dropped at the role gate"


def test_designer_titles_route_to_the_game_designer_cv():
    s = score(make("Gameplay Designer", company="Epicgames", desc="remote worldwide"))
    assert s.ok
    assert s.variant == "game_designer"


def test_non_gaming_design_titles_are_still_not_game_design():
    for title in ("Senior Graphic Designer", "Interior Designer", "ASIC Design Engineer"):
        assert "game_design" not in _families(title.lower()), title


# --- stack words and seniority bands are not professions --------------------


def test_qa_devops_hybrid_is_kept_as_a_qa_role():
    """ "QA/DevOps Engineer" is a QA job; it was excluded as a different profession."""
    s = score(make("QA/DevOps Engineer", company="Discord", desc="remote worldwide"))
    assert s.ok, s.rejected
    assert "qa" in _families("qa/devops engineer")


def test_plain_devops_title_is_still_excluded():
    """The override only applies when a core role family also matches."""
    assert not score(make("DevOps Engineer with Splunk")).ok


def test_above_level_core_roles_are_penalised_not_dropped():
    """QA seniority is a penalty, because ~5 years makes it arguable.

    Game design is not - see the entry-level tests below.
    """
    s = score(make("Principal QA Engineer", company="Riotgames", desc="fully remote, anywhere"))
    assert s.ok, s.rejected
    assert "above-level" in s.reasons


def test_above_level_costs_enough_to_sink_a_badly_located_role():
    remote = score(
        make("Principal QA Engineer", company="Riotgames", desc="fully remote, work from anywhere")
    )
    onsite = score(
        make(
            "Principal QA Engineer",
            company="Riotgames",
            location="Los Angeles",
            desc="on-site in LA",
        )
    )
    assert remote.score >= DEFAULT_MIN_SCORE, remote.score
    assert onsite.score < DEFAULT_MIN_SCORE, onsite.score


def test_above_level_role_ranks_below_an_at_level_one():
    senior = score(make("Principal QA Engineer", desc=ANYWHERE))
    normal = score(make("QA Engineer", desc=ANYWHERE))
    assert normal.score > senior.score


# --- game design is entry level only ----------------------------------------
#
# The game-design CV is built from personal projects, not paid design work. The author has
# said he is only a credible applicant at entry level or internship. These titles were
# scoring 99 - near the top of the sheet - and crowding out roles he can actually get.
# QA is deliberately NOT symmetrical: ~5 years makes "Senior QA Engineer" reasonable.


@pytest.mark.parametrize(
    "title",
    [
        "Senior Game Designer - Mob Control",
        "Senior Level Designer - Hole.io",
        "Lead Game Designer - Monopoly Go!",
        "Principal Encounter Designer",
        "Sr. Narrative Designer",
        "Staff Systems Designer",
        "Game Designer II",
        "Game Design Manager",
    ],
)
def test_game_design_above_entry_level_is_rejected(title):
    s = score(make(title, company="Riotgames", desc="fully remote, work from anywhere"))
    assert not s.ok, f"{title!r} should not reach the sheet (scored {s.score})"
    assert "entry level" in s.rejected


def test_head_of_game_design_is_dropped_as_a_profession():
    """Already caught by TITLE_EXCLUDE's "head of" - a different gate, same outcome."""
    assert not score(make("Head of Game Design", company="Riotgames", desc=ANYWHERE)).ok


@pytest.mark.parametrize(
    "title",
    [
        "Game Designer",
        "Junior Game Designer",
        "Game Design Intern",
        "Level Designer",
        "Associate Gameplay Designer",
        "Graduate Narrative Designer",
        "Entry-Level Systems Designer",
    ],
)
def test_entry_level_game_design_still_qualifies(title):
    s = score(make(title, company="Riotgames", desc="fully remote, work from anywhere"))
    assert s.ok, f"{title!r} was rejected: {s.rejected}"
    assert s.variant == "game_designer"


def test_entry_level_game_design_outranks_a_plain_one_of_the_same_location():
    junior = score(make("Junior Game Designer", company="Riotgames", desc=ANYWHERE))
    plain = score(make("Game Designer", company="Riotgames", desc=ANYWHERE))
    assert junior.score > plain.score
    assert "entry-level game design" in junior.reasons


def test_seniority_is_judged_per_family_not_globally():
    """The same word that kills a design role is only a penalty on a QA one."""
    design = score(make("Senior Game Designer", company="Riotgames", desc=ANYWHERE))
    qa = score(make("Senior QA Engineer", company="Riotgames", desc=ANYWHERE))
    assert not design.ok
    assert qa.ok and qa.score >= DEFAULT_MIN_SCORE


def test_senior_game_data_and_qa_roles_are_untouched():
    """Only design is project-only; the data and QA experience behind those CVs is real."""
    for title in ("Senior Game Data Analyst - Paper.io 2", "Senior QA Tester - Paper.io 2"):
        s = score(make(title, company="Voodoo", desc=ANYWHERE))
        assert s.ok, f"{title!r} was rejected: {s.rejected}"


# --- the escape bug: a literal backspace silently disabled every exclusion ---


def test_exclusion_pattern_ends_with_a_real_word_boundary():
    s = settings.current()
    for pattern in (s.exclude_titles, s.exclude_departments):
        assert pattern.pattern.endswith(r")\b)")
        assert chr(8) not in pattern.pattern, "literal backspace disables the whole group"


def test_a_json_backspace_is_refused_rather_than_disabling_the_pattern():
    # A backslash-b typed into a JSON string arrives as chr(8), not a word boundary.
    with pytest.raises(settings.SettingsError, match="backspace"):
        settings.phrase("re:" + chr(8) + "(qa|tester)" + chr(8))


# --- a domain is not a profession -------------------------------------------
#
# All three reached the "different profession" reject on the 2026-08-21 scrape while
# matching a real role family. TITLE_EXCLUDE scanned the whole title, so the team or
# domain a role sits in disqualified it: an analytics job "in Finance", a data job on
# "Legal Compliance", a QA job for "Sales and Marketing".


@pytest.mark.parametrize(
    ("title", "family"),
    [
        ("Senior Analytics Engineer, Finance", "data"),
        ("Data Analyst, Operations (IP & Legal Compliance)", "data"),
        ("Senior Software Engineer, Quality Assurance, Sales and Marketing", "qa"),
        ("QA Engineer - Payroll Platform", "qa"),
    ],
)
def test_a_domain_in_the_tail_does_not_disqualify(title, family):
    assert not excluded_by(title), f"{title!r} dropped over its domain, not its profession"
    assert family in _families(title.lower())
    assert score(make(title, desc=ANYWHERE)).ok


@pytest.mark.parametrize(
    "title",
    [
        "Marketing Lead, Evals",
        "Senior Sales Engineer",
        "Technical Account Manager",
        "Strategic Finance, Business Partnership & Workflow Automation",
        "Legal Counsel, Data Privacy",
        "Technical Recruiter, Quality Assurance",
    ],
)
def test_a_domain_heading_the_title_still_disqualifies(title):
    """The head names the profession. Forgiving the tail must not forgive the head."""
    assert excluded_by(title), f"{title!r} is a {title.split(',')[0]} role, not a target"
    assert not score(make(title, desc=ANYWHERE)).ok


def test_a_profession_in_the_tail_still_disqualifies():
    """Only business functions moved to the head-only check. A profession named after
    the comma is still that profession, however the title is punctuated."""
    for title in ("QA Engineer, Engineering Manager", "Test Analyst - Data Scientist"):
        assert excluded_by(title), title
        assert not score(make(title, desc=ANYWHERE)).ok


# --- dedupe: one normalisation, used everywhere -----------------------------


def test_dedupe_ignores_location_suffix_and_parentheticals():
    a = dedupe_key("Riot Games", "QA Engineer II - VALORANT Foundations, Engine")
    b = dedupe_key("Riotgames", "QA Engineer II (Remote)")
    assert a == b


def test_rank_collapses_the_same_role_from_two_boards():
    posts = [
        make("QA Engineer - Remote", company="Acme", desc="python remote worldwide"),
        make("QA Engineer (EU)", company="Acme", desc="python remote worldwide"),
    ]
    assert len(rank(posts, min_score=0)) == 1


# --- variant routing --------------------------------------------------------


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Game Designer, Activities", "game_designer"),
        ("QA Engineer - VALORANT", "qa_gaming"),
        ("Software Engineer in Test", "sdet"),
        ("Data Analyst", "data_analyst"),
        ("AI Trainer", "ai_qa"),
    ],
)
def test_variant_routing(title, expected):
    s = score(make(title, desc="python"))
    assert s.variant == expected


def test_description_alone_cannot_decide_the_variant():
    """A backend posting mentioning games in passing must not become a gaming CV."""
    assert pick_variant({"qa"}, "qa engineer", "") == "automation_qa"


def test_opportunity_does_not_match_unity():
    """ "unity" is a GAME_TERM, and "opportunity" contains it as a raw substring - EEO
    boilerplate ("equal opportunity employer") appears in nearly every posting, so a
    substring check mis-routed an ALTEN automotive Test Engineer posting to qa_gaming."""
    desc = "great opportunity to grow your career in automotive testing"
    # Since 2026-09-09 an automotive description routes to the automotive QA variant. The
    # point of this case is unchanged: whatever it picks, it must not be a gaming variant.
    assert pick_variant({"qa"}, "test engineer", desc) == "automation_qa_automotive"
    assert pick_variant({"qa"}, "test engineer", "great opportunity to grow") == "automation_qa"


@pytest.mark.parametrize(
    "title,company,expected",
    [
        # Studio names and franchises carry no literal "game", so these were all
        # mis-routed to the generic automation CV before is_gaming() existed.
        ("QA Engineer II - VALORANT Foundations", "Riotgames", "qa_gaming"),
        ("Senior QA Engineer, Compliance & Release", "Riotgames", "qa_gaming"),
        ("Game Designer, Activities", "Epicgames", "game_designer"),
        ("Senior Product Engineer, QA Engineer", "Colonist", "qa_gaming"),
        ("QA Engineer", "Acme", "automation_qa"),
    ],
)
def test_gaming_detected_from_studio_or_franchise(title, company, expected):
    assert score(make(title, company=company, desc="python")).variant == expected


# --- seniority / internships ------------------------------------------------


def test_non_gaming_internship_is_rejected():
    assert not score(make("QA Intern", desc="remote")).ok


def test_gaming_internship_is_allowed():
    s = score(make("Game Design Intern", desc="remote worldwide game studio"))
    assert s.ok


# --- remote quality ---------------------------------------------------------


def test_remote_anywhere_beats_region_locked_remote():
    anywhere = score(make("QA Engineer", desc="remote, work from anywhere"))
    locked = score(make("QA Engineer", desc="remote but US only, must reside in the US"))
    assert anywhere.score > locked.score


def _with_countries(countries: list[str], desc: str = "fully remote") -> Posting:
    p = make("QA Engineer", location="Remote - " + ", ".join(countries), desc=desc)
    p.countries = countries
    return p


@pytest.mark.parametrize(
    "countries", [["South Africa"], ["Cambodia"], ["Vietnam"], ["India", "Thailand"]]
)
def test_a_southeast_asia_or_south_africa_lock_stays_but_is_penalised(countries):
    """Himalayas' first trial put 154 country-restricted leads on the sheet, led by
    "Remote - United States" at 162, because "fully remote" in the text won the tier.
    Southeast Asia and South Africa are the locks the author keeps (2026-09-17)."""
    locked = score(_with_countries(countries))
    open_ = score(make("QA Engineer", desc="fully remote"))
    assert "remote-anywhere" not in locked.reasons
    assert any("hires only in" in r for r in locked.reasons)
    assert locked.score < open_.score


@pytest.mark.parametrize(
    "countries",
    [
        ["Netherlands"],
        ["Finland"],
        ["Denmark"],
        ["Germany"],
        ["France"],
        ["Spain"],
        ["Saudi Arabia"],
        ["United Arab Emirates"],
        ["Poland", "Germany"],
    ],
)
def test_relocation_target_country_locks_stay_on_the_sheet(countries):
    """The author, 2026-09-17: "all my relocation targets should stay"."""
    s = score(_with_countries(countries))
    assert s.ok, s.rejected
    assert any("hires only in" in r for r in s.reasons)


@pytest.mark.parametrize(
    "countries",
    [
        ["Canada"],
        ["India"],
        ["Pakistan"],
        ["Argentina", "Brazil"],
        ["United Kingdom"],
        ["Philippines"],
    ],
)
def test_any_other_hiring_country_lock_is_dropped(countries):
    """2026-09-17: "hires only in" is a drop unless it is Southeast Asia or South Africa."""
    s = score(_with_countries(countries))
    assert not s.ok
    assert s.rejected.startswith("hires only in")


@pytest.mark.parametrize("countries", [["Anywhere"], ["Worldwide"], ["EMEA"], ["Tunisia"]])
def test_hiring_countries_that_include_tunisia_are_not_locked(countries):
    assert not any("hires only in" in r for r in score(_with_countries(countries)).reasons)


@pytest.mark.parametrize(
    ("location", "desc", "countries"),
    [
        ("Remote - United States", "fully remote", ["United States"]),
        ("Remote - USA", "fully remote", ["USA"]),
        ("Remote (US)", "remote team", []),
        ("USA (Remote)", "remote team", []),
        ("Remote", "Fully remote. US only.", []),
        ("Remote", "Remote role. Must be based in the United States.", []),
    ],
)
def test_us_only_remote_is_rejected(location, desc, countries):
    """The author, 2026-09-17: US-only remote is a wall, not a penalty. It took a quarter of
    the first sheet after Himalayas was added, many still scoring above 100."""
    p = make("QA Engineer", location=location, desc=desc)
    p.countries = countries
    s = score(p)
    assert not s.ok
    assert s.rejected == "US only"


@pytest.mark.parametrize(
    ("location", "desc", "countries"),
    [
        ("Remote - US or Europe", "remote team", []),
        ("Remote", "remote team", ["United States", "Germany"]),
        ("Remote", "Remote. Contact us only by email.", []),
        ("Remote - Americas", "remote team", []),
    ],
)
def test_remote_open_beyond_the_us_is_not_rejected_as_us_only(location, desc, countries):
    p = make("QA Engineer", location=location, desc=desc)
    p.countries = countries
    assert score(p).rejected != "US only"


@pytest.mark.parametrize(
    "location",
    [
        "San Francisco",
        "New York City",
        "Remote - Atlanta - Hybrid",
        "Weirton, WV",
        "USA - Provo UT",
        "Mountain View, CA",
        "San Carlos, California",
        "Torrance (On-Site)",
    ],
)
def test_us_based_on_site_roles_are_rejected_too(location):
    """ "They should not have appeared in the first place" - US-based, not just remote US."""
    assert score(make("QA Engineer", location=location, desc="join our office")).rejected == (
        "US only"
    )


@pytest.mark.parametrize(
    "location",
    [
        "Tunis, TN",  # TN is Tunisia before it is Tennessee
        "Pune, IN",
        "Toronto, CA",
        "Munich DE",
        "Remote - Georgia",
        "Austin, TX; London",
        "Remote - Australia, Canada, Germany, United Kingdom, United States",
        "Birmingham",
    ],
)
def test_places_that_only_look_american_are_not_us_only(location):
    assert not us_located(location)


def test_visa_sponsorship_is_rewarded():
    with_visa = score(make("QA Engineer", desc="remote", visa=True))
    without = score(make("QA Engineer", desc="remote"))
    assert with_visa.score > without.score


# --- location tiers vs the gaming bonus -------------------------------------
#
# The author's stated order is location first: fully remote > remote-with-overlap >
# Netherlands/Berlin/Hamburg. Gaming is a strong preference but must only sort jobs
# *within* a tier - a gaming role in Amsterdam must never outrank a fully-remote one.

ANYWHERE = "fully remote, work from anywhere"
OVERLAP = "remote, overlap with eastern time"
DUTCH = "on-site in our Amsterdam office"
BERLIN = "on-site in our Berlin office"
HAMBURG = "on-site in our Hamburg office"
FINLAND = "on-site in our Helsinki office"
DENMARK = "on-site in our Aarhus office"
GULF = "on-site in our Riyadh office"


def test_location_tiers_rank_in_the_stated_order():
    tiers = [
        score(make("QA Engineer", location="Remote - Anywhere", desc=ANYWHERE)).score,
        score(make("QA Engineer", location="Remote", desc=OVERLAP)).score,
        score(make("QA Engineer", location="Amsterdam, Netherlands", desc=DUTCH)).score,
        score(make("QA Engineer", location="Berlin, Germany", desc=BERLIN)).score,
    ]
    assert tiers == sorted(tiers, reverse=True), tiers


def test_berlin_and_hamburg_rank_the_same_as_netherlands():
    """Berlin and Hamburg were added as named relocation targets alongside the
    Netherlands - all three on-site tiers must score equally, not one above another."""
    nl = score(make("QA Engineer", location="Amsterdam, Netherlands", desc=DUTCH))
    berlin = score(make("QA Engineer", location="Berlin, Germany", desc=BERLIN))
    hamburg = score(make("QA Engineer", location="Hamburg, Germany", desc=HAMBURG))
    assert nl.score == berlin.score == hamburg.score, (nl.score, berlin.score, hamburg.score)


def test_finland_ranks_the_same_as_the_other_relocation_targets():
    """Finland was added as a named relocation target on 2026-08-13 (Veikkaus, Helsinki) -
    it must score the same as the Netherlands/Berlin/Hamburg tier, not below it."""
    nl = score(make("QA Engineer", location="Amsterdam, Netherlands", desc=DUTCH))
    finland = score(make("QA Engineer", location="Helsinki, Finland", desc=FINLAND))
    assert nl.score == finland.score, (nl.score, finland.score)


@pytest.mark.parametrize(
    "location", ["Aarhus, Denmark", "Copenhagen, Denmark", "København, Danmark"]
)
def test_denmark_ranks_the_same_as_the_other_relocation_targets(location):
    """Denmark was added as a named relocation target on 2026-09-11 (Netcompany, Aarhus) -
    the whole country, so any Danish city scores the same as the Netherlands tier."""
    nl = score(make("QA Engineer", location="Amsterdam, Netherlands", desc=DUTCH))
    denmark = score(make("QA Engineer", location=location, desc=DENMARK))
    assert nl.score == denmark.score, (nl.score, denmark.score)


def test_a_danish_language_requirement_is_not_a_relocation_target():
    """ "danish" is left out of the pattern on purpose - in a description it is usually a
    language requirement, and a Stockholm role asking for Danish is not in Denmark."""
    s = score(make("QA Engineer", location="Stockholm, Sweden", desc="fluent Danish required"))
    assert s.score < DEFAULT_MIN_SCORE, s.score


@pytest.mark.parametrize(
    "location",
    ["Riyadh, Saudi Arabia", "Dubai, United Arab Emirates", "Doha, Qatar", "Muscat, Oman"],
)
def test_the_gulf_ranks_the_same_as_the_other_relocation_targets(location):
    """The Gulf was added as a named relocation target on 2026-09-14 (NHC Innovation,
    Riyadh) - the whole region, so any Gulf city scores the same as the Netherlands tier."""
    nl = score(make("QA Engineer", location="Amsterdam, Netherlands", desc=DUTCH))
    gulf = score(make("QA Engineer", location=location, desc=GULF))
    assert nl.score == gulf.score, (nl.score, gulf.score)


def test_an_american_gulf_is_not_a_relocation_target():
    """No bare "gulf" in the pattern - the Gulf Coast is in the US."""
    s = score(make("QA Engineer", location="Houston, TX", desc="on-site, Gulf Coast region"))
    assert s.score < DEFAULT_MIN_SCORE, s.score


def test_a_nationals_only_gulf_role_is_an_eligibility_lock():
    """Saudization and Emiratization reserve roles for nationals - a wall, like US-only."""
    s = score(make("QA Engineer", location="Riyadh, Saudi Arabia", desc="Saudi nationals only"))
    assert any(r.startswith("eligibility(") for r in s.reasons), s.reasons
    assert s.score < DEFAULT_MIN_SCORE, s.score


@pytest.mark.parametrize(
    ("better_loc", "better_desc", "worse_loc", "worse_desc"),
    [
        ("Remote - Anywhere", ANYWHERE, "Remote", OVERLAP),
        ("Remote", OVERLAP, "Amsterdam, Netherlands", DUTCH),
        ("Remote - Anywhere", ANYWHERE, "Amsterdam, Netherlands", DUTCH),
    ],
)
def test_gaming_never_outranks_a_better_located_role(
    better_loc, better_desc, worse_loc, worse_desc
):
    plain = score(make("QA Engineer", location=better_loc, desc=better_desc))
    gaming = score(make("QA Engineer", company="Riot Games", location=worse_loc, desc=worse_desc))
    assert plain.score > gaming.score, (plain.score, gaming.score)


def test_gaming_still_sorts_within_a_tier():
    plain = score(make("QA Engineer", location="Remote - Anywhere", desc=ANYWHERE))
    gaming = score(
        make("QA Engineer", company="Riot Games", location="Remote - Anywhere", desc=ANYWHERE)
    )
    assert gaming.score > plain.score


def test_qa_outranks_data_analysis_at_the_same_location():
    qa = score(make("QA Engineer", location="Remote - Anywhere", desc=ANYWHERE))
    da = score(make("Data Analyst", location="Remote - Anywhere", desc=ANYWHERE))
    assert qa.score > da.score


def test_game_data_is_exempt_from_the_data_penalty():
    game = score(make("Game Data Analyst", company="Riot Games", location="Remote", desc=ANYWHERE))
    plain = score(make("Data Analyst", location="Remote", desc=ANYWHERE))
    assert "data-role(least preferred)" not in game.reasons
    assert "data-role(least preferred)" in plain.reasons
    assert game.score > plain.score


def test_work_authorization_is_a_blocker_not_a_timezone_preference():
    """ "Remote (US)" usually means authorized to work in the US, which the author is not."""
    overlap = score(make("QA Engineer", location="Remote", desc=OVERLAP))
    auth = score(
        make("QA Engineer", location="Remote (US)", desc="must be authorized to work in the US")
    )
    assert overlap.score > auth.score


# --- the default threshold --------------------------------------------------
#
# DEFAULT_MIN_SCORE is what the daily scrape filters on. It has to sit between
# "the least the author wants" and "what he never asked for", or a whole preference
# tier silently disappears from the shortlist - which is exactly what happened
# when a Netherlands QA role scored 48 against a threshold of 60.


def test_a_plain_netherlands_qa_role_clears_the_default_threshold():
    s = score(make("QA Engineer", location="Amsterdam, Netherlands", desc=DUTCH))
    assert s.score >= DEFAULT_MIN_SCORE, s.score


def test_a_plain_berlin_qa_role_clears_the_default_threshold():
    s = score(make("QA Engineer", location="Berlin, Germany", desc=BERLIN))
    assert s.score >= DEFAULT_MIN_SCORE, s.score


def test_a_plain_hamburg_qa_role_clears_the_default_threshold():
    s = score(make("QA Engineer", location="Hamburg, Germany", desc=HAMBURG))
    assert s.score >= DEFAULT_MIN_SCORE, s.score


def test_a_plain_finland_qa_role_clears_the_default_threshold():
    s = score(make("QA Engineer", location="Helsinki, Finland", desc=FINLAND))
    assert s.score >= DEFAULT_MIN_SCORE, s.score


def test_a_plain_denmark_qa_role_clears_the_default_threshold():
    s = score(make("QA Engineer", location="Aarhus, Denmark", desc=DENMARK))
    assert s.score >= DEFAULT_MIN_SCORE, s.score


def test_a_plain_gulf_qa_role_clears_the_default_threshold():
    s = score(make("QA Engineer", location="Riyadh, Saudi Arabia", desc=GULF))
    assert s.score >= DEFAULT_MIN_SCORE, s.score


def test_a_netherlands_gaming_role_clears_the_default_threshold():
    s = score(
        make(
            "Game Designer",
            company="Riot Games",
            location="Amsterdam, Netherlands",
            desc=DUTCH,
        )
    )
    assert s.score >= DEFAULT_MIN_SCORE, s.score


def test_an_onsite_role_somewhere_unrequested_stays_below_the_threshold():
    s = score(make("QA Engineer", location="Munich, Germany", desc="on-site in Munich"))
    assert s.score < DEFAULT_MIN_SCORE, s.score


# --- region locks -----------------------------------------------------------
#
# On 2026-08-06 one employer's nine "Freelance | 8-20 hrs/week | Remote (EU/UK)"
# listings took slots 1,3,4,5,7,8,9,12,13 of an 18-lead sheet, scoring up to 155.
# "fully remote" in the body set remote_any, and remote_any suppressed the
# eligibility penalty entirely. A region the author cannot work from is a wall, not a
# tier - so it now cancels remote-anywhere instead of being cancelled by it.

EU_UK_GIG = (
    "Freelance | 8-20 hrs/week | Remote (EU/UK). "
    "Fully remote work for specialists based in the EU or UK."
)


def test_eu_uk_only_is_not_remote_anywhere():
    s = score(make("Quality Assurance Manager", desc=EU_UK_GIG))
    assert "remote-anywhere" not in s.reasons, s.reasons
    assert any(r.startswith("eligibility") for r in s.reasons), s.reasons


def test_eu_uk_only_scores_below_a_genuinely_open_role():
    locked = score(make("Quality Assurance Manager", desc=EU_UK_GIG))
    open_role = score(make("Quality Assurance Manager", desc="Fully remote, work from anywhere."))
    assert locked.score < open_role.score, (locked.score, open_role.score)


@pytest.mark.parametrize(
    "desc",
    [
        "Remote (APAC) - this role will be based remotely in the APAC region.",
        "Remote - North & South America only.",
        "Candidates who operate in UTC-3 -- UTC-8.",
        "You must be located in the EU to apply.",
    ],
)
def test_regions_that_exclude_tunisia_are_locked(desc):
    s = score(make("QA Engineer", desc="Fully remote. " + desc))
    assert "remote-anywhere" not in s.reasons, (desc, s.reasons)


@pytest.mark.parametrize(
    "location,desc",
    [
        ("Home based - EMEA", "Home-based in the Europe, Middle East, or Africa regions."),
        ("Remote", "Fully remote, work from anywhere in the world."),
        ("Remote", "We prioritize your talent, not your location."),
    ],
)
def test_regions_that_include_tunisia_are_not_locked(location, desc):
    """EMEA and worldwide both contain Tunisia. Locking these would drop the best
    employer found so far - Canonical hires home-based EMEA."""
    s = score(make("QA Engineer", location=location, desc=desc))
    assert not any(r.startswith("eligibility") for r in s.reasons), s.reasons


# --- per-company cap --------------------------------------------------------


def test_one_company_cannot_flood_the_sheet():
    flood = [
        make(f"QA Engineer {i}", company="10Xteam", desc="Fully remote, work from anywhere")
        for i in range(9)
    ]
    assert len(rank(flood)) == MAX_PER_COMPANY


def test_the_cap_keeps_each_companys_best():
    postings = [
        make("QA Engineer", company="Flood", desc="Fully remote, work from anywhere"),
        make("Data Analyst", company="Flood", desc="on-site in Berlin"),
    ]
    kept = rank(postings, per_company=1)
    assert len(kept) == 1
    assert kept[0].posting.title == "QA Engineer"


def test_the_cap_is_per_company_not_global():
    postings = [
        make("QA Engineer", company=name, desc="Fully remote, work from anywhere")
        for name in ("Alpha", "Beta", "Gamma", "Delta")
    ]
    assert len(rank(postings)) == 4


def test_the_cap_can_be_disabled():
    flood = [
        make(f"QA Engineer {i}", company="10Xteam", desc="Fully remote, work from anywhere")
        for i in range(9)
    ]
    assert len(rank(flood, per_company=0)) == 9


# --- the cap is a daily quota, not a lifetime one ---------------------------
#
# jobs.scrape used to rank() first and drop already-tracked leads from the result. The
# cap therefore ran against every qualifying posting, applied ones included, so a
# company the author had already applied to three times went silent. On the 2026-08-21
# scrape six employers were in exactly that state - Testlio, Voodoo, Riot, Canonical,
# Scopely and OpenAI - hiding a 99-score Voodoo game-design lead behind three roles
# already in jobtrack.


def test_already_tracked_roles_do_not_spend_the_daily_quota():
    postings = [
        make(f"QA Engineer {i}", company="Testlio", desc="Fully remote, work from anywhere")
        for i in range(3)
    ] + [make("Game Designer", company="Testlio", desc="Fully remote, work from anywhere")]
    tracked = {dedupe_key("Testlio", f"QA Engineer {i}") for i in range(3)}

    assert len(rank(postings)) == MAX_PER_COMPANY  # all four compete, cap trims to 3
    kept = rank(postings, exclude=tracked)
    assert [s.posting.title for s in kept] == ["Game Designer"]


def test_exclude_uses_the_same_normalisation_as_the_deduper():
    """The exclude set seeds the in-run `seen` set, so a title that dedupe_key()
    collapses must be excluded even when the stored spelling differs."""
    posts = [make("QA Engineer II (Remote)", company="Riotgames", desc=ANYWHERE)]
    tracked = {dedupe_key("Riot Games", "QA Engineer II - VALORANT Foundations, Engine")}
    assert rank(posts, exclude=tracked) == []


def test_exclude_defaults_to_nothing():
    posts = [make("QA Engineer", company="Acme", desc=ANYWHERE)]
    assert len(rank(posts)) == 1
    assert len(rank(posts, exclude=set())) == 1


def test_home_based_apac_is_locked():
    """Canonical writes an APAC-only remote role as `Home Based - APAC`, which reached the
    review sheet tagged `remote+overlap` because the prose saying so sat past the lock
    zone's 1200-char window."""
    s = score(
        make(
            "Ubuntu Linux Kernel Test Engineer",
            location="Home Based - APAC; Office Based - Beijing, China",
            desc="Ensuring the quality of kernels demands a systematic approach to testing.",
        )
    )
    assert any(r.startswith("eligibility") for r in s.reasons), s.reasons


def test_an_incidental_apac_mention_does_not_lock():
    s = score(
        make(
            "QA Engineer",
            desc="Fully remote, work from anywhere. We have teammates across APAC and EMEA.",
        )
    )
    assert not any(r.startswith("eligibility") for r in s.reasons), s.reasons


# ------------------------------------------- AI QA classification (2026-09-09)


@pytest.mark.parametrize(
    "title",
    [
        "Senior Fullstack Engineer, AI Observability & Evals Platform",
        "Senior Backend Software Engineer, AI Observability & Evals Platform",
        "Senior Software Engineer, AI Evals",
        "Member of Technical Staff (full-stack + AI evals)",
    ],
)
def test_an_engineering_role_on_an_evals_product_is_not_ai_qa(title):
    """These build the evals product; they do not do the evaluating. The bare word "Evals"
    in the ai_qa family matched all of them at +50, and on 2026-09-09 four such LangChain
    and Sentry roles took four of the nine slots on the review sheet. The author's CV is QA, not
    software engineering, so they are unwinnable and cost a slot each."""
    assert "ai_qa" not in _families(title.lower())


def test_software_engineer_in_test_is_still_ai_qa():
    """The guard keys off the engineering title alone, so it must not swallow the QA-side
    titles that legitimately contain "engineer"."""
    assert "ai_qa" in _families("software engineer in test, ai evals")


@pytest.mark.parametrize(
    "title",
    ["LLM Evaluation Specialist", "AI Evaluation Engineer", "Model Evaluation Lead"],
)
def test_evaluation_titles_are_found(title):
    """`llm eval` and `ai evaluat` used to be followed by a closing \b, which cannot match
    mid-word: "Evaluation" continues past "eval", so every one of these was dropped as
    "not a target role family" - the exact titles the search is aimed at."""
    assert "ai_qa" in _families(title.lower())


def test_ai_automation_counts_as_an_ai_role():
    """The only application of 112 that ever reached a screening was DB Dialog's AI
    Automation Engineer. It matched `qa` alone and drew no AI credit whatsoever."""
    assert "ai_qa" in _families("ai automation engineer")


def test_ai_automation_routes_to_its_own_variant():
    """profile.json carries an ai_automation variant that pick_variant had no path to:
    nothing it could return was ever that string, so the CV could never be built."""
    assert pick_variant({"ai_qa", "qa"}, "ai automation engineer", "") == "ai_automation"


@pytest.mark.parametrize("title", ["AI Specialist", "Generative AI Specialist", "GenAI Specialist"])
def test_ai_specialist_is_an_ai_automation_role(title):
    """VA4U's "AI Specialist" (2026-09-17) was n8n workflows and agents, and the scrape
    dropped it as "not a target role family"."""
    assert "ai_qa" in _families(title.lower())
    assert pick_variant(_families(title.lower()), title.lower(), "") == "ai_automation"


def test_plain_ai_qa_still_routes_to_ai_qa():
    assert pick_variant({"ai_qa"}, "llm evaluation specialist", "") == "ai_qa"


def test_plain_automation_is_untouched_by_the_ai_routing():
    assert pick_variant({"qa"}, "senior automation engineer", "") == "automation_qa"


# ------------------------------------ remote-anywhere is a hiring fact (2026-09-09)


def test_marketing_worldwide_does_not_make_a_role_remote():
    """GIANTS Software's "a long-standing simulation title celebrated worldwide" is about
    the game's popularity. Bare "worldwide" was in REMOTE_ANY and matched against the whole
    description, so a Brno full-time central-office role took the top location tier and
    scored 145 - above a genuinely remote one. 27 postings in one scrape reached
    remote-anywhere on nothing but copy like this."""
    desc = (
        "GIANTS Software is best known for its Farming Simulator series, a "
        "long-standing real-time simulation title celebrated worldwide. Game Tester "
        "for our QA team in Brno, Czech Republic. Central office, permanent employment."
    )
    s = score(make("Game Tester", company="GIANTS Software", location="Brno, Czechia", desc=desc))
    assert "remote-anywhere" not in s.reasons


def test_a_global_player_base_does_not_make_a_role_remote():
    """The same shape from the other common phrasing."""
    desc = "We connect millions of players worldwide. This role is based in our Lisboa studio."
    s = score(make("QA Engineer", company="Voodoo", location="Lisboa", desc=desc))
    assert "remote-anywhere" not in s.reasons


@pytest.mark.parametrize("loc", ["Worldwide", "Anywhere", "Global", "Remote - Global"])
def test_an_anywhere_location_field_still_earns_the_top_tier(loc):
    """The same words ARE a hiring fact when they are the whole location field, which is
    how We Work Remotely and Testlio express it."""
    assert "remote-anywhere" in score(make("QA Engineer", location=loc)).reasons


def test_anywhere_in_the_world_still_earns_the_top_tier():
    assert "remote-anywhere" in score(make("QA Engineer", location="Anywhere in the World")).reasons


def test_home_based_counts_as_remote():
    """Canonical writes every one of its ~300 remote openings as "Home based - EMEA", and
    that phrasing is why the board is in targets.json. REMOTE carried "home office" but not
    "home based", so those roles only ever reached the sheet because a stray "worldwide" in
    the description rescued them; removing that dropped them to "on-site abroad"."""
    s = score(
        make("Kernel Build Automation Engineer", company="Canonical", location="Home based - EMEA")
    )
    assert "remote" in s.reasons
    assert "on-site abroad" not in s.reasons
