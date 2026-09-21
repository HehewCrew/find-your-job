"""The Applications tab: everything the jobtrack CLI does, through the UI's API."""

from __future__ import annotations

import threading

import pytest
from jobs.errors import JobsError
from jobs.ui import api
from jobs.ui.paths import Paths
from jobs.ui.tasks import TaskRunner

from jobtrack.models import Application, ValidationError
from jobtrack.storage import Store


@pytest.fixture
def paths(tmp_path):
    p = Paths.under(tmp_path)
    store = Store(p.store).load()
    store.add(
        Application(
            id=1,
            company="Acme",
            role="QA Engineer",
            status="applied",
            applied_on="2026-08-01",
            updated_at="2026-08-01T09:00:00",
        )
    )
    store.add(
        Application(
            id=2,
            company="Globex",
            role="SDET",
            status="interviewing",
            location="Remote",
            notes=["phone screen went well"],
        )
    )
    store.add(
        Application(
            id=3,
            company="Initech",
            role="Tester",
            status="rejected",
            updated_at="2026-07-01T09:00:00",
        )
    )
    store.add(Application(id=4, company="Hooli", role="QA Lead", status="offer"))
    store.save()
    return p


def ids(result) -> list[int]:
    return [row["id"] for row in result["apps"]]


def test_list_is_newest_update_first_with_stats(paths):
    got = api.apps_list(paths)
    assert ids(got)[-1] == 3  # oldest update last
    s = got["stats"]
    assert (s["total"], s["open"], s["closed"]) == (4, 3, 1)
    assert s["by_status"]["interviewing"] == 1
    assert s["offer_rate"] == 50  # 1 offer of 2 decided
    assert s["quiet"] == 1  # Acme: open, untouched since August


def test_filters(paths):
    assert sorted(ids(api.apps_list(paths, statuses=["interview"]))) == [2]
    assert sorted(ids(api.apps_list(paths, open_only=True))) == [1, 2, 4]
    assert ids(api.apps_list(paths, quiet=True)) == [1]
    assert ids(api.apps_list(paths, q="phone screen")) == [2]
    with pytest.raises(ValidationError):
        api.apps_list(paths, statuses=["w"])


def test_get_and_missing(paths):
    assert api.app_get(paths, 2)["notes"] == ["phone screen went well"]
    with pytest.raises(api.Missing):
        api.app_get(paths, 99)


def test_add(paths):
    runner = TaskRunner()
    app = api.app_add(paths, runner, {"company": "Umbrella", "role": "QA", "note": "referral"})
    assert app["id"] == 5 and app["status"] == "applied" and app["notes"] == ["referral"]
    with pytest.raises(ValidationError):
        api.app_add(paths, runner, {"company": "", "role": "QA"})
    with pytest.raises(ValidationError):
        api.app_add(paths, runner, {"company": "X", "role": "QA", "id": 1})


def test_update_validates_like_the_model(paths):
    runner = TaskRunner()
    got = api.app_update(paths, runner, 1, {"status": "screen", "salary": "40k"})
    assert got["changed"] == ["status=screening", "salary=40k"]
    assert Store(paths.store).load().get(1).status == "screening"
    with pytest.raises(ValidationError):
        api.app_update(paths, runner, 1, {"company": "  "})


def test_note_and_delete(paths):
    runner = TaskRunner()
    assert api.app_note(paths, runner, 1, "sent a follow-up")["notes"] == ["sent a follow-up"]
    with pytest.raises(ValidationError):
        api.app_note(paths, runner, 1, "   ")
    assert api.app_delete(paths, runner, 3) == {"deleted": 3}
    assert Store(paths.store).load().get(3) is None
    with pytest.raises(api.Missing):
        api.app_delete(paths, runner, 3)


def test_quick_actions_close_or_advance_a_quiet_application(paths):
    runner = TaskRunner()
    got = api.app_quick(paths, runner, 1, "no_reply")
    assert got["status"] == "rejected" and "no reply" in got["notes"][-1]
    got = api.app_quick(paths, runner, 2, "heard_back")
    assert got["status"] == "screening" and "heard back" in got["notes"][-1]
    with pytest.raises(ValidationError):
        api.app_quick(paths, runner, 2, "ghosted")


def test_writes_wait_for_a_build_that_is_saving_the_store(paths):
    runner = TaskRunner()
    gate = threading.Event()
    runner.start("build", lambda report: gate.wait(5) and {})
    try:
        with pytest.raises(JobsError, match="build"):
            api.app_note(paths, runner, 1, "hello")
        assert api.apps_list(paths)["stats"]["total"] == 4  # reading is fine
    finally:
        gate.set()


def test_csv_follows_the_filters(paths):
    text = api.apps_csv(paths, open_only=True)
    lines = text.strip().splitlines()
    assert lines[0].startswith("id,company,role,status")
    assert len(lines) == 4 and "Initech" not in text
