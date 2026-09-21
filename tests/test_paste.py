"""Tests for pasting a job description in by hand (jobs.paste) and the
brief/keyword plumbing it shares with the daily scrape (jobs.tailor).

The CV build itself needs Word, so nothing here renders a document. What is pinned is
everything that decides *what* gets rendered and *what BRIEFS.md ends up saying*.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from jobs import tailor as tl  # noqa: E402
from jobs.brief import parse  # noqa: E402
from jobs.paste import company_from_url, guess_title, variants  # noqa: E402
from jobs.score import _families, pick_variant  # noqa: E402

JD = """Apply now
Share

Senior QA Automation Engineer

Remote (worldwide)

About the role
We are a game studio. You will own the test strategy: designing test plans, building
automated test suites in Python, and driving regression testing across releases. You
will run API testing against our REST services and own defect management in Jira.
"""


# --- reading the pasted text ------------------------------------------------


def test_guess_title_skips_board_furniture():
    assert guess_title(JD) == "Senior QA Automation Engineer"


@pytest.mark.parametrize(
    "line",
    ["Apply now", "Share", "Remote", "Full-time", "About the role", "Job description"],
)
def test_noise_lines_are_never_taken_as_the_title(line):
    assert guess_title(f"{line}\nQA Engineer\n") == "QA Engineer"


def test_guess_title_gives_up_rather_than_inventing_one():
    assert guess_title("Apply now\nShare\n") == ""


def test_prose_is_not_mistaken_for_a_title():
    """A sentence ends in a full stop; a job title does not."""
    text = "We are hiring right now.\nQA Engineer\n"
    assert guess_title(text) == "QA Engineer"


# --- company inference ------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://boards.greenhouse.io/bungie/jobs/123",
        "https://jobs.lever.co/voodoo/abc",
        "https://jobs.ashbyhq.com/openai/xyz",
        "https://www.linkedin.com/jobs/view/123",
        "https://myworkdayjobs.com/en-US/acme/job/123",
    ],
)
def test_ats_hosts_never_become_the_company_name(url):
    """Guessing from the host would print "Greenhouse" where the employer belongs."""
    assert company_from_url(url) == ""


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://careers.spotify.com/job/123", "Spotify"),
        ("https://www.bungie.net/careers/123", "Bungie"),
        ("https://jobs.cd-projekt.com/x", "Cd Projekt"),
    ],
)
def test_company_inferred_from_an_employer_domain(url, expected):
    assert company_from_url(url) == expected


def test_no_url_means_no_guess():
    assert company_from_url("") == ""
    assert company_from_url("not a url") == ""


# --- variant routing on pasted text -----------------------------------------


def test_pasted_jd_routes_to_a_real_variant():
    title = guess_title(JD)
    variant = pick_variant(_families(title.lower()), title.lower(), JD.lower(), "Bungie")
    assert variant in variants()
    assert variant == "qa_gaming"


def _personal_profile() -> Path:
    """cv/profile.json is personal and gitignored, so a toolchain-only branch has none.

    These checks compare the code against the real profile; profile.example.json carries
    fewer variants and skills, so testing against it would fail for the wrong reason.
    """
    path = Path(__file__).resolve().parent.parent / "cv" / "profile.json"
    if not path.exists():
        pytest.skip("no personal cv/profile.json on this checkout")
    return path


def test_every_variant_settings_can_choose_exists_in_the_profile():
    """A cv name in settings.json with no matching profile.json variant fails only at
    build time, on the first posting routed to it - check the real pair up front."""
    import json

    from jobs import settings

    profile = json.loads(_personal_profile().read_text(encoding="utf-8"))
    assert settings.current().missing_variants(profile) == []


# --- jd persistence ----------------------------------------------------------


def test_write_jd_saves_the_full_text_next_to_the_cv(tmp_path):
    dest = tl.write_jd("Bungie", "QA Gaming Engineer", JD, "bungie-qa-gaming-engineer", tmp_path)
    assert dest == tmp_path / "bungie-qa-gaming-engineer" / "jd.md"
    written = dest.read_text(encoding="utf-8")
    assert written.startswith("# Bungie — QA Gaming Engineer")
    assert JD.strip() in written


def test_write_jd_skips_when_there_is_no_text(tmp_path):
    slug = "bungie-qa-gaming-engineer"
    assert tl.write_jd("Bungie", "QA Gaming Engineer", "  ", slug, tmp_path) is None
    assert not (tmp_path / slug).exists()


# --- skills vs gaps ---------------------------------------------------------


def test_playwright_is_a_skill_and_cypress_still_a_gap():
    matched, gaps = tl.analyse("Strong hands-on experience with Playwright or Cypress.")
    assert "Playwright" in matched
    assert gaps == ["Cypress"]


def test_workflow_qa_wording_matches_the_defect_manager_experience():
    """A ticket-flow QA posting uses none of the test-engineering vocabulary; before these
    entries it matched too little to headline a keyword block at all."""
    matched, _ = tl.analyse(
        "Manage ticket flow in Jira, monitor SLA adherence, coordinate with the team, "
        "and feed errors into process improvements. Quality-assurance experience required."
    )
    for skill in (
        "Jira",
        "Quality assurance",
        "SLA tracking",
        "Process improvement",
        "Cross-functional coordination",
    ):
        assert skill in matched


def test_generic_keywords_trail_the_specific_ones():
    """fit() trims from the end. 'Quality assurance' once sat mid-list and pushed
    'Root-cause analysis' off an AI-eval CV whose JD was about root causes."""
    matched, _ = tl.analyse(
        "QA role: root cause analysis on AUTOSAR stacks, cross-functional, SLA-driven."
    )
    specific = max(matched.index(s) for s in ("Root-cause analysis", "AUTOSAR"))
    generic = min(
        matched.index(s)
        for s in ("Quality assurance", "Cross-functional coordination", "SLA tracking")
    )
    assert specific < generic


def test_soft_skills_alone_never_headline_the_block():
    from types import SimpleNamespace

    posting = SimpleNamespace(
        company="Acme",
        title="Assistant",
        url="",
        description="Excellent written communication and attention to detail, in English.",
    )
    assert tl.tailor_for(posting, "project_manager")["matched"] == []


def test_no_gap_names_a_tool_the_profile_already_lists():
    """LACK is hand-kept, so it drifts when the profile gains a skill. Playwright sat in
    it for weeks after the CV claimed it, flagging a false gap on every brief."""
    import json
    import re

    profile = json.loads(_personal_profile().read_text(encoding="utf-8"))
    skills = " ".join(
        item for group in profile["skill_groups"].values() for item in group["items"]
    ).lower()
    for name in tl.lack():
        for term in re.sub(r"\(.*?\)", "", name).split("/"):
            term = term.strip().lower()
            assert not re.search(rf"\b{re.escape(term)}\b", skills), f"{term!r} is in LACK"


# --- keyword trimming -------------------------------------------------------


def test_shrink_reaches_empty_so_the_fit_loop_terminates():
    matched = [f"skill{i}" for i in range(14)]
    seen = [len(matched)]
    while matched:
        matched = tl.shrink(matched)
        seen.append(len(matched))
        assert len(seen) < 20, "shrink() failed to converge"
    assert seen[-1] == 0


def test_shrink_never_leaves_a_block_too_short_to_deserve_a_header():
    for n in range(1, 15):
        out = tl.shrink([f"s{i}" for i in range(n)])
        assert not out or len(out) >= tl.MIN_KEYWORDS, (n, out)


def test_shrink_drops_from_the_weak_end():
    matched = ["Python", "CI/CD", "Jira", "Linux", "Arabic"]
    assert tl.shrink(matched) == ["Python", "CI/CD"] or tl.shrink(matched) == []
    # 5 - 3 = 2, which is below MIN_KEYWORDS, so it collapses to nothing
    assert tl.shrink(matched) == []


# --- appending to BRIEFS.md -------------------------------------------------


def _tailor(company="Bungie", title="Senior QA Engineer", slug="bungie-senior-qa-engineer"):
    return {
        "company": company,
        "job_title": title,
        "variant": "qa_gaming",
        "matched": ["Python", "CI/CD", "Jira"],
        "gaps": ["SQL"],
        "slug": slug,
        "url": "https://example.com/job",
    }


def test_append_keeps_existing_briefs(tmp_path):
    """A pasted posting arrives mid-day; overwriting would lose the applied marks."""
    out = tmp_path / "BRIEFS.md"
    tl.write_briefs([_tailor(company="Epic Games", slug="epic")], out)
    marked = out.read_text(encoding="utf-8").replace(
        "**Status:** pending", "**Status:** applied", 1
    )
    out.write_text(marked, encoding="utf-8")

    tl.append_briefs([_tailor()], out)
    briefs = parse(out.read_text(encoding="utf-8"))
    assert [b.company for b in briefs] == ["Epic Games", "Bungie"]
    assert briefs[0].state == "applied", "the earlier mark was overwritten"
    assert briefs[1].state == "pending"


def test_append_to_a_missing_file_still_produces_a_readable_sheet(tmp_path):
    out = tmp_path / "nested" / "BRIEFS.md"
    tl.append_briefs([_tailor()], out)
    briefs = parse(out.read_text(encoding="utf-8"))
    assert len(briefs) == 1
    assert briefs[0].variant == "qa_gaming"
    assert briefs[0].slug == "bungie-senior-qa-engineer"


def test_appended_brief_round_trips_through_the_brief_parser(tmp_path):
    """jobs.brief has to be able to read what jobs.paste writes, or close breaks."""
    out = tmp_path / "BRIEFS.md"
    t = _tailor(title="Senior QA Automation Engineer — Player Platform")
    tl.append_briefs([t], out)
    b = parse(out.read_text(encoding="utf-8"))[0]
    assert b.company == "Bungie"
    # An em dash inside the title must not be mistaken for the company/role separator.
    assert b.title == "Senior QA Automation Engineer — Player Platform"
    assert b.url == t["url"]
    assert b.gaps == "SQL"


def test_write_briefs_and_append_briefs_render_the_same_block(tmp_path):
    a, b = tmp_path / "a.md", tmp_path / "b.md"
    tl.write_briefs([_tailor()], a)
    tl.append_briefs([_tailor()], b)
    assert parse(a.read_text(encoding="utf-8"))[0] == parse(b.read_text(encoding="utf-8"))[0]


def test_display_path_is_repo_relative_inside_the_repo():
    assert tl.display_path(tl.ROOT / "cv" / "x.pdf").replace("\\", "/") == "cv/x.pdf"


def test_display_path_falls_back_to_absolute_outside_the_repo(tmp_path):
    assert tl.display_path(tmp_path / "x.pdf") == str(tmp_path / "x.pdf")


def test_write_jd_follows_a_patched_tailored_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(tl, "TAILORED_DIR", tmp_path)
    path = tl.write_jd("Acme", "QA", "the text", "acme-qa")
    assert path == tmp_path / "acme-qa" / "jd.md"
