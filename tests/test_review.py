"""Tests for the review sheet (jobs.review) that sits between scrape and pick.

The sheet is hand-edited, so what matters is that every plausible edit either round-trips
faithfully or fails loudly - never silently builds a CV against the wrong posting.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from jobs import review  # noqa: E402
from jobs.score import Scored  # noqa: E402
from jobs.sources import Posting  # noqa: E402

from jobtrack.models import ValidationError  # noqa: E402


def scored(company="Roblox", title="Quality Analyst", url="https://x/1", score=131) -> Scored:
    p = Posting(
        source="greenhouse:roblox",
        company=company,
        title=title,
        url=url,
        location="Remote",
        description="python test automation",
        posted_at="2026-08-04",
    )
    s = Scored(p, score, "qa_gaming", ["remote-anywhere", "gaming"])
    s.tailor = {
        "company": company,
        "job_title": title,
        "variant": "qa_gaming",
        "matched": ["Python", "Test automation"],
        "gaps": ["SQL"],
        "slug": "roblox-quality-analyst",
        "url": url,
    }
    return s


@pytest.fixture
def sheet(tmp_path):
    return tmp_path / "TODAY_SCRAPING.md", tmp_path / "TODAY_SCRAPING.json"


def write(sheet, leads):
    md, js = sheet
    review.write_sheet(leads, "2026-08-04", sheet=md, data=js)
    return md, js


def read(sheet):
    md, js = sheet
    return review.read_sheet(sheet=md, data=js)


# --- round trip -------------------------------------------------------------


def test_every_lead_starts_undecided(sheet):
    write(sheet, [scored(url="https://x/1"), scored(company="Voodoo", url="https://x/2")])
    leads = read(sheet)
    assert len(leads) == 2
    assert all(x.mark == review.PENDING for x in leads)


@pytest.mark.parametrize(
    "mark,expected",
    [("x", review.TAKE), ("X", review.TAKE), ("-", review.SKIP), (" ", review.PENDING)],
)
def test_marks_are_read_back(sheet, mark, expected):
    md, js = write(sheet, [scored()])
    md.write_text(
        md.read_text(encoding="utf-8").replace("## [ ] 1.", f"## [{mark}] 1."), encoding="utf-8"
    )
    assert read(sheet)[0].mark == expected


def test_the_json_sidecar_carries_the_description(sheet):
    """Markdown holds the decision; the CV is tailored from the sidecar."""
    write(sheet, [scored()])
    lead = read(sheet)[0]
    assert lead.data["description"] == "python test automation"
    assert lead.data["tailor"]["matched"] == ["Python", "Test automation"]
    assert lead.data["score"] == 131


def test_leads_are_joined_by_url_not_position(sheet):
    """Deleting a section by hand must not shift every later lead onto wrong data."""
    md, js = write(sheet, [scored(url="https://x/1"), scored(company="Voodoo", url="https://x/2")])
    kept = (
        md.read_text(encoding="utf-8").split("## [ ] 1.")[0]
        + "## [ ] 2."
        + (md.read_text(encoding="utf-8").split("## [ ] 2.")[1])
    )
    md.write_text(kept, encoding="utf-8")
    leads = read(sheet)
    assert len(leads) == 1
    assert leads[0].company == "Voodoo"
    assert leads[0].url == "https://x/2"


def test_a_lead_with_no_sidecar_record_still_parses(sheet):
    """An entry typed in by hand has no data, but must not crash pick."""
    md, js = write(sheet, [scored()])
    md.write_text(
        md.read_text(encoding="utf-8")
        + "\n## [x] 99. Handmade — Some Role\n- **Link:** https://nowhere/1\n",
        encoding="utf-8",
    )
    leads = read(sheet)
    assert leads[-1].mark == review.TAKE
    assert leads[-1].data == {}
    assert leads[-1].company == "Handmade"


def test_missing_sheet_is_empty_not_an_error(tmp_path):
    assert review.read_sheet(sheet=tmp_path / "nope.md", data=tmp_path / "nope.json") == []


def test_em_dash_in_the_title_survives_the_label(sheet):
    write(sheet, [scored(title="Senior QA — Player Platform")])
    assert read(sheet)[0].data["title"] == "Senior QA — Player Platform"


# --- selecting by number ----------------------------------------------------


def test_select_by_printed_number(sheet):
    write(sheet, [scored(url="https://x/1"), scored(company="Voodoo", url="https://x/2")])
    leads = read(sheet)
    assert [x.index for x in review.select(leads, ["2"])] == [2]


def test_select_by_name_fragment(sheet):
    write(sheet, [scored(url="https://x/1"), scored(company="Voodoo", url="https://x/2")])
    assert review.select(read(sheet), ["voodoo"])[0].company == "Voodoo"


def test_select_rejects_an_unknown_number(sheet):
    write(sheet, [scored()])
    with pytest.raises(ValidationError, match="no lead #9"):
        review.select(read(sheet), ["9"])


def test_select_refuses_an_ambiguous_fragment(sheet):
    write(sheet, [scored(url="https://x/1"), scored(title="Quality Analyst II", url="https://x/2")])
    with pytest.raises(ValidationError, match="ambiguous"):
        review.select(read(sheet), ["roblox"])


def test_select_deduplicates(sheet):
    write(sheet, [scored()])
    assert len(review.select(read(sheet), ["1", "1", "roblox"])) == 1


def test_select_all(sheet):
    write(sheet, [scored(url="https://x/1"), scored(company="Voodoo", url="https://x/2")])
    assert len(review.select(read(sheet), ["all"])) == 2


# --- the numbers on the sheet match the ones select() accepts ----------------


def test_printed_numbers_are_contiguous_from_one(sheet):
    write(sheet, [scored(url=f"https://x/{i}") for i in range(1, 6)])
    assert [x.index for x in read(sheet)] == [1, 2, 3, 4, 5]


# --- marking from the UI ----------------------------------------------------------


def test_set_mark_round_trips_through_read_sheet(sheet):
    write(sheet, [scored(url="https://x/1"), scored(company="Riot", url="https://x/2")])
    md, _ = sheet
    review.set_mark("https://x/2", review.TAKE, sheet=md)
    review.set_mark("https://x/1", review.SKIP, sheet=md)
    assert [lead.mark for lead in read(sheet)] == [review.SKIP, review.TAKE]
    review.set_mark("https://x/1", review.PENDING, sheet=md)
    assert read(sheet)[0].mark == review.PENDING


def test_set_mark_on_a_url_no_longer_on_the_sheet_refuses(sheet):
    from jobs.errors import JobsError

    write(sheet, [scored(url="https://x/1")])
    with pytest.raises(JobsError, match="changed"):
        review.set_mark("https://x/9", review.TAKE, sheet=sheet[0])


def test_set_mark_rejects_an_unknown_mark(sheet):
    write(sheet, [scored(url="https://x/1")])
    with pytest.raises(ValidationError):
        review.set_mark("https://x/1", "maybe", sheet=sheet[0])


def test_sheet_date_reads_the_header(sheet):
    write(sheet, [scored()])
    assert review.sheet_date(sheet[0]) == "2026-08-04"
    assert review.sheet_date(sheet[0].with_name("missing.md")) is None
