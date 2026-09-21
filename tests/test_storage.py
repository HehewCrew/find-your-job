from __future__ import annotations

import json

from jobtrack.models import Application
from jobtrack.storage import Store


def test_missing_file_loads_empty(store_path):
    assert len(Store(store_path).load()) == 0


def test_save_and_reload(store_path, store):
    store.add(Application(id=1, company="Acme", role="Engineer"))
    store.save()

    reloaded = Store(store_path).load()
    assert len(reloaded) == 1
    assert reloaded.get(1).company == "Acme"


def test_saved_file_has_schema_version(store_path, sample_apps):
    data = json.loads(store_path.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert len(data["applications"]) == 3


def test_next_id_increments(sample_apps):
    assert sample_apps.next_id() == 4


def test_next_id_after_removal_does_not_reuse(sample_apps):
    sample_apps.remove(3)
    assert sample_apps.next_id() == 3  # highest remaining is 2


def test_remove_returns_none_for_missing(sample_apps):
    assert sample_apps.remove(999) is None
    assert len(sample_apps) == 3


def test_search_matches_company_role_and_location(sample_apps):
    assert [a.id for a in sample_apps.search("acme")] == [1]
    assert [a.id for a in sample_apps.search("analyst")] == [2]
    assert [a.id for a in sample_apps.search("remote")] == [2]
    assert sample_apps.search("nothing") == []


def test_search_matches_notes(store):
    store.add(Application(id=1, company="Acme", role="Engineer", notes=["referred by Dana"]))
    assert [a.id for a in store.search("dana")] == [1]


def test_filter_by_status_and_open(sample_apps):
    assert [a.id for a in sample_apps.filter(statuses=["applied"])] == [1]
    assert [a.id for a in sample_apps.filter(open_only=True)] == [1, 2]


def test_env_override(monkeypatch, tmp_path):
    target = tmp_path / "custom.json"
    monkeypatch.setenv("JOBTRACK_FILE", str(target))
    assert Store().path == target


def test_save_is_atomic_leaves_no_temp_files(store_path, sample_apps):
    leftovers = list(store_path.parent.glob("*.tmp"))
    assert leftovers == []
