"""Ashby board discovery and the pruning rules the author set on 2026-09-17."""

from __future__ import annotations

import sys
from collections import Counter
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobs import discover  # noqa: E402
from jobs.sources import Posting  # noqa: E402

TODAY = date(2026, 9, 17)


def _post(posted_at: str) -> Posting:
    return Posting(source="ashby:acme", company="Acme", title="QA", url="u", posted_at=posted_at)


@pytest.mark.parametrize(
    ("url", "token"),
    [
        ("https://jobs.ashbyhq.com/va4u/32ee0437-5184", "va4u"),
        ("https://jobs.ashbyhq.com/0g?utm_source=x", "0g"),
        ("https://jobs.ashbyhq.com/Kraken%20Digital/abc", "Kraken Digital"),
        ("https://jobs.ashbyhq.com/api/whatever", None),
        ("https://jobs.ashbyhq.com/%3Cscript%3E", None),
        ("https://boards.greenhouse.io/acme", None),
    ],
)
def test_token_from_url(url, token):
    assert discover.token_from_url(url) == token


def test_dedupe_is_case_insensitive_and_keeps_curated_spelling():
    assert discover.dedupe_tokens(["OpenAI"], ["openai", "ramp"]) == ["OpenAI", "ramp"]


def test_record_keeps_the_newest_job_date():
    state = {"boards": {}}
    discover.record(state, "acme", [_post("2026-09-01"), _post("2026-09-10")], TODAY)
    discover.record(state, "acme", [_post("2026-08-01")], TODAY)
    entry = state["boards"]["acme"]
    assert entry["last_new_job"] == "2026-09-10"
    assert entry["postings"] == 1


def test_aged_out_rejections_count():
    """The author, 2026-09-17: the aged-out rejections were real ones, Voodoo among them."""
    apps = [
        {"company": "Voodoo", "status": "rejected", "notes": []},
        {
            "company": "Voodoo",
            "status": "rejected",
            "notes": ["2026-09-09: aged out to rejected - applied 2026-08-04, 36 days"],
        },
        {"company": "Ramp", "status": "applied", "notes": []},
    ]
    counts = discover.rejections(apps)
    assert counts["voodoo"] == 2
    assert counts["ramp"] == 0


@pytest.mark.parametrize(
    ("last", "today", "due"),
    [
        ("", date(2026, 9, 21), True),  # never run
        ("2026-09-17", date(2026, 9, 20), False),  # Thursday's run covers that week's Sunday
        ("2026-09-17", date(2026, 9, 21), True),  # the next Monday
        ("2026-09-21", date(2026, 9, 21), False),  # already ran this Monday
        ("2026-09-17", date(2026, 9, 23), True),  # no Monday session: catches up Wednesday
    ],
)
def test_weekly_sweep_is_due_from_monday(last, today, due):
    assert discover.is_due({"last_discovery": last}, today) is due


def test_merge_keeps_a_scrape_save_that_landed_mid_sweep():
    on_disk = {"boards": {"ramp": {"last_new_job": "2026-09-20"}, "only_disk": {}}}
    ours = {"boards": {"found": {"postings": 3}}, "last_discovery": "2026-09-21"}
    merged = discover.merge_boards(on_disk, ours)
    assert set(merged["boards"]) == {"ramp", "only_disk", "found"}
    assert merged["last_discovery"] == "2026-09-21"


def test_lock_blocks_a_second_sweep_but_not_a_stale_one(tmp_path):
    lock = tmp_path / ".discover.lock"
    assert discover._take_lock(lock)
    assert not discover._take_lock(lock)
    from datetime import timedelta

    assert discover._take_lock(lock, stale_after=timedelta(seconds=-1))


@pytest.mark.parametrize(
    ("entry", "rejections", "reason"),
    [
        (None, Counter(), ""),
        ({"last_new_job": "2026-09-10"}, Counter(), ""),
        ({"last_new_job": "2026-08-01"}, Counter(), "no new job since 2026-08-01"),
        ({"postings": 0}, Counter(), "no postings"),
        ({"gone": True}, Counter(), "board no longer exists"),
        ({"last_new_job": "2026-09-10"}, Counter({"voodoo": 1}), ""),
        ({"last_new_job": "2026-09-10"}, Counter({"voodoo": 2}), "rejected 2 times"),
        (None, Counter({"voodoo": 2}), "rejected 2 times"),
    ],
)
def test_prune_reason(entry, rejections, reason):
    assert discover.prune_reason("voodoo", entry, rejections, TODAY) == reason


def test_plan_merges_curated_and_discovered_and_reports_pruned():
    state = {
        "boards": {
            "Ramp": {"last_new_job": "2026-09-15"},
            "dusty": {"last_new_job": "2026-05-01"},
            "fresh": {"last_new_job": "2026-09-16"},
        }
    }
    poll, pruned = discover.ashby_plan(["ramp", "newboard"], state, Counter(), TODAY)
    assert poll == ["ramp", "newboard", "fresh"]
    assert pruned == {"dusty": "no new job since 2026-05-01"}


def test_discovered_boards_only_contribute_this_weeks_postings():
    from jobs.scrape import drop_stale_discovered

    def post(source, posted_at):
        return Posting(source=source, company="c", title=source, url="u", posted_at=posted_at)

    postings = [
        post("ashby:found", "2026-09-15"),
        post("ashby:found-old", "2026-08-01"),
        post("ashby:Ramp", "2026-08-01"),  # curated, case differs: kept whatever its age
        post("himalayas", "2026-08-01"),
    ]
    kept = drop_stale_discovered(postings, ["ramp"], TODAY)
    assert [p.source for p in kept] == ["ashby:found", "ashby:Ramp", "himalayas"]


def test_state_round_trips(tmp_path):
    path = tmp_path / "boards.json"
    state = discover.load_state(path)
    discover.record(state, "acme", [_post("2026-09-10")], TODAY)
    discover.save_state(state, path)
    assert discover.load_state(path)["boards"]["acme"]["last_new_job"] == "2026-09-10"
