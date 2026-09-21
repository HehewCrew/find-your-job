"""Golden-output tests: the CLI prints exactly what it printed before the core refactor.

Captured on the pre-refactor code with UPDATE_GOLDEN=1. A diff here means terminal output
changed - intended only for the behaviour changes the spec names.
"""

from __future__ import annotations

from datetime import date

import pytest
from jobs import brief, paste, pick, review, scrape
from jobs.score import Scored
from jobs.sources import Posting

JD = """Senior QA Automation Engineer

Remote (worldwide)

We are a game studio. You will own the test strategy: designing test plans, building
automated test suites in Python, and driving regression testing across releases. You
will run API testing against our REST services and own defect management in Jira.
"""


def lead(company: str, title: str, url: str) -> Scored:
    p = Posting(
        source="remotive",
        company=company,
        title=title,
        url=url,
        location="Remote",
        description=JD,
        posted_at="2026-09-20",
    )
    s = Scored(p, 120, "qa_gaming", ["remote-anywhere", "gaming"])
    s.tailor = {
        "company": company,
        "job_title": title,
        "variant": "qa_gaming",
        "matched": ["Python", "Test automation", "Regression testing", "API testing"],
        "gaps": [],
        "slug": f"{company.lower()}-qa",
        "url": url,
    }
    return s


@pytest.fixture
def sheet(tmp_path):
    md, js = tmp_path / "TODAY_SCRAPING.md", tmp_path / "TODAY_SCRAPING.json"
    leads = [lead("Roblox", "QA Analyst", "https://x/1"), lead("Riot", "QA Lead", "https://x/2")]
    review.write_sheet(leads, "2026-09-21", sheet=md, data=js)
    text = md.read_text(encoding="utf-8")
    text = text.replace("## [ ] 1.", "## [x] 1.").replace("## [ ] 2.", "## [-] 2.")
    md.write_text(text, encoding="utf-8")
    return md, js


def run_pick(argv, sheet, tmp_path):
    md, js = sheet
    return pick.main(
        [
            *argv,
            "--sheet",
            str(md),
            "--data",
            str(js),
            "--briefs",
            str(tmp_path / "BRIEFS.md"),
            "--file",
            str(tmp_path / "applications.json"),
        ]
    )


def test_pick_builds_the_ticked_lead(sheet, tmp_path, cv_sandbox, capsys, golden):
    code = run_pick([], sheet, tmp_path)
    golden("pick_ticked", code, *capsys.readouterr())


def test_pick_dry_run(sheet, tmp_path, cv_sandbox, capsys, golden):
    code = run_pick(["--dry-run"], sheet, tmp_path)
    golden("pick_dry_run", code, *capsys.readouterr())


def test_pick_with_no_sheet(tmp_path, cv_sandbox, capsys, golden):
    missing = (tmp_path / "none.md", tmp_path / "none.json")
    code = run_pick([], missing, tmp_path)
    golden("pick_no_sheet", code, *capsys.readouterr())


def run_paste(argv, tmp_path):
    jd = tmp_path / "jd.txt"
    jd.write_text(JD, encoding="utf-8")
    return paste.main(
        [
            "--file",
            str(jd),
            "--company",
            "Acme",
            "--variant",
            "qa_gaming",
            "--briefs",
            str(tmp_path / "BRIEFS.md"),
            *argv,
        ]
    )


def test_paste_builds(tmp_path, cv_sandbox, capsys, golden):
    code = run_paste(["--force"], tmp_path)
    golden("paste_force", code, *capsys.readouterr())


def test_paste_dry_run(tmp_path, cv_sandbox, capsys, golden):
    code = run_paste(["--dry-run"], tmp_path)
    golden("paste_dry_run", code, *capsys.readouterr())


def test_paste_too_short(tmp_path, cv_sandbox, capsys, golden):
    short = tmp_path / "short.txt"
    short.write_text("QA Engineer\n", encoding="utf-8")
    code = paste.main(["--file", str(short), "--company", "Acme"])
    golden("paste_too_short", code, *capsys.readouterr())


BRIEFS = """# Tailored applications

## Roblox — QA Analyst
- **Status:** pending
- **CV:** `qa_gaming` → `cv/out/tailored/roblox-qa/*.pdf`
- **Link:** https://x/1
- **Matched skills:** Python
- **Gaps to expect:** none flagged

## Riot — QA Lead
- **Status:** pending
- **CV:** `qa_gaming` → `cv/out/tailored/riot-qa/*.pdf`
- **Link:** https://x/2
- **Matched skills:** Python
- **Gaps to expect:** none flagged
"""


def run_brief(argv, tmp_path):
    return brief.main(["--briefs", str(tmp_path / "BRIEFS.md"), "--root", str(tmp_path), *argv])


def test_brief_mark_list_close(tmp_path, cv_sandbox, capsys, golden):
    (tmp_path / "BRIEFS.md").write_text(BRIEFS, encoding="utf-8")
    cv = cv_sandbox / "tailored" / "roblox-qa" / "Your_Name_CV.pdf"
    cv.parent.mkdir(parents=True)
    cv.write_bytes(b"%PDF-1.4")
    store = str(tmp_path / "applications.json")

    codes = [
        run_brief(["applied", "1"], tmp_path),
        run_brief(["close", "--file", store], tmp_path),  # refused: #2 still pending
        run_brief(["aborted", "rest", "--reason", "US-only"], tmp_path),
        run_brief(["list"], tmp_path),
        run_brief(["close", "--file", store], tmp_path),
    ]
    out, err = capsys.readouterr()
    golden("brief_day", sum(c << i for i, c in enumerate(codes)), out, err)


def test_scrape_writes_the_sheet(tmp_path, cv_sandbox, monkeypatch, capsys, golden):
    posting = Posting(
        source="remotive",
        company="Roblox",
        title="QA Automation Engineer",
        url="https://x/9",
        location="Remote (worldwide)",
        description="Fully remote worldwide. " + JD,
        posted_at=date.today().isoformat(),
    )
    monkeypatch.setattr(scrape, "collect", lambda *a, **k: ([posting], ["lever:gone: HTTPError"]))
    monkeypatch.setattr(scrape.discover, "load_state", lambda *a, **k: {"boards": {}})
    monkeypatch.setattr(scrape, "_refresh_boards", lambda *a, **k: None)
    real = review.write_sheet
    monkeypatch.setattr(
        scrape.review,
        "write_sheet",
        lambda leads, when: real(leads, when, sheet=tmp_path / "S.md", data=tmp_path / "S.json"),
    )
    code = scrape.main(["--file", str(tmp_path / "applications.json")])
    out, err = capsys.readouterr()
    assert "lead(s) written" in out, "the posting must qualify; widen its description if not"
    golden("scrape_sheet", code, out, err)
