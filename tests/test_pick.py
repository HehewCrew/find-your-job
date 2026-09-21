"""pick.run(): jobtrack entries, one CV per taken lead, briefs - and one bad CV sinks nothing."""

from __future__ import annotations

import json

import pytest
from jobs import pick, review
from jobs import tailor as tl
from jobs.errors import JobsError

from jobtrack.storage import Store


def make_lead(i: int, company: str, mark: str) -> review.Lead:
    t = {
        "company": company,
        "job_title": "QA Analyst",
        "variant": "qa_gaming",
        "matched": [],
        "gaps": [],
        "slug": f"{company.lower()}-qa-analyst",
        "url": f"https://x/{i}",
    }
    data = {"tailor": t, "location": "Remote", "score": 100, "reasons": [], "description": "jd"}
    return review.Lead(index=i, label=f"{company} — QA Analyst", url=t["url"], mark=mark, data=data)


def test_choose_takes_ticked_and_drops_skipped():
    leads = [
        make_lead(1, "A", review.TAKE),
        make_lead(2, "B", review.SKIP),
        make_lead(3, "C", review.PENDING),
    ]
    taken, dropped = pick.choose(leads, [], "S.md")
    assert [x.company for x in taken] == ["A"] and [x.company for x in dropped] == ["B"]


def test_choose_with_nothing_marked_is_nothing_to_do():
    with pytest.raises(JobsError):
        pick.choose([make_lead(1, "A", review.PENDING)], [], "S.md")


def test_run_builds_records_and_reports(tmp_path, cv_sandbox):
    store = Store(tmp_path / "applications.json").load()
    events = []
    result = pick.run(
        [make_lead(1, "A", review.TAKE)],
        [make_lead(2, "B", review.SKIP)],
        briefs=tmp_path / "B.md",
        store=store,
        report=events.append,
    )
    assert [cv.error for cv in result.cvs] == [""]
    assert result.cvs[0].docx.exists()
    statuses = sorted(a.status for a in Store(tmp_path / "applications.json").load())
    assert statuses == ["wishlist", "withdrawn"]
    non_detail = [e.message for e in events if not e.detail]
    assert non_detail[0] == "\nTailoring 1 CV(s)…"
    assert non_detail[1].startswith("  · A")


def test_one_failing_cv_does_not_lose_the_others_briefs(tmp_path, cv_sandbox, monkeypatch):
    real_fit = tl.fit

    def flaky(t, report=None):
        if t["company"] == "B":
            raise RuntimeError("Word crashed")
        return real_fit(t, report=report)

    monkeypatch.setattr(pick.tl, "fit", flaky)
    store = Store(tmp_path / "applications.json").load()
    result = pick.run(
        [
            make_lead(1, "A", review.TAKE),
            make_lead(2, "B", review.TAKE),
            make_lead(3, "C", review.TAKE),
        ],
        [],
        briefs=tmp_path / "B.md",
        store=store,
    )
    assert [cv.error for cv in result.cvs] == ["", "Word crashed", ""]
    text = (tmp_path / "B.md").read_text(encoding="utf-8")
    assert "## A —" in text and "## C —" in text and "## B —" not in text


def test_main_reports_a_failed_cv_and_exits_1(tmp_path, cv_sandbox, monkeypatch, capsys):
    md, js = tmp_path / "S.md", tmp_path / "S.json"
    lead = make_lead(1, "A", review.TAKE)
    js.write_text(json.dumps([{**lead.data, "url": lead.url}]), encoding="utf-8")
    md.write_text(f"## [x] 1. {lead.label}\n- **Link:** {lead.url}\n", encoding="utf-8")

    def broken(t, report=None):
        raise RuntimeError("Word crashed")

    monkeypatch.setattr(pick.tl, "fit", broken)
    code = pick.main(["--sheet", str(md), "--data", str(js), "--briefs", str(tmp_path / "B.md")])
    out = capsys.readouterr().out
    assert code == 1
    assert "!! failed: Word crashed" in out
    assert "brief(s) appended" not in out
