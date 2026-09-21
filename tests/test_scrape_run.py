"""scrape.run(): fetch, rank, write the sheet - and say what it is doing as it goes."""

from __future__ import annotations

from datetime import date

from jobs import review, scrape
from jobs.sources import Posting

from jobtrack.storage import Store


def posting(n: int) -> Posting:
    return Posting(
        source="remotive",
        company=f"Studio{n}",
        title="QA Automation Engineer",
        url=f"https://x/{n}",
        location="Remote (worldwide)",
        description="Fully remote worldwide. Python test automation, regression testing, "
        "API testing, Jira. We are a game studio.",
        posted_at=date.today().isoformat(),
    )


def sandbox(monkeypatch, tmp_path):
    monkeypatch.setattr(scrape.discover, "load_state", lambda *a, **k: {"boards": {}})
    monkeypatch.setattr(scrape, "_refresh_boards", lambda *a, **k: None)
    real = review.write_sheet
    monkeypatch.setattr(
        scrape.review,
        "write_sheet",
        lambda leads, when: real(leads, when, sheet=tmp_path / "S.md", data=tmp_path / "S.json"),
    )


def test_collect_ticks_once_per_source_from_the_calling_thread(monkeypatch):
    import threading

    monkeypatch.setattr(scrape.sources, "remotive", lambda: [posting(1)])

    def broken():
        raise OSError("down")

    monkeypatch.setattr(scrape.sources, "arbeitnow", broken)
    events, threads = [], set()

    def report(e):
        events.append(e)
        threads.add(threading.get_ident())

    postings, problems = scrape.collect(["remotive", "arbeitnow"], [], report=report)
    assert len(postings) == 1 and problems == ["arbeitnow: OSError"]
    ticks = [e for e in events if e.detail]
    assert sorted(e.done for e in ticks) == [1, 2] and {e.total for e in ticks} == {2}
    assert threads == {threading.get_ident()}


def test_run_ranks_writes_the_sheet_and_reports(tmp_path, monkeypatch, cv_sandbox):
    sandbox(monkeypatch, tmp_path)
    monkeypatch.setattr(scrape, "collect", lambda *a, **k: ([posting(1), posting(2)], []))
    events = []
    result = scrape.run(
        sources=None,
        min_score=None,
        store=Store(tmp_path / "a.json").load(),
        report=events.append,
    )
    assert result.raw == 2 and len(result.leads) == 2
    assert result.sheet == tmp_path / "S.md" and result.sheet.exists()
    assert all(s.tailor for s in result.leads)
    stages = [e.stage for e in events if not e.detail]
    assert stages[0] == "fetch" and "rank" in stages


def test_run_with_nothing_qualifying_writes_no_sheet(tmp_path, monkeypatch, cv_sandbox):
    sandbox(monkeypatch, tmp_path)
    monkeypatch.setattr(scrape, "collect", lambda *a, **k: ([], ["remotive: HTTPError"]))
    result = scrape.run(sources=None, min_score=None, store=Store(tmp_path / "a.json").load())
    assert (result.leads, result.sheet, result.problems) == ([], None, ["remotive: HTTPError"])
