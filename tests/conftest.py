from __future__ import annotations

import os
from pathlib import Path

import pytest

# Every test scores against a fixed persona, never against whichever jobs/settings.json the
# person running the suite happens to have. Set before any test module imports jobs.score.
FIXTURES = Path(__file__).resolve().parent / "fixtures"
os.environ["JOBHUNT_SETTINGS"] = str(FIXTURES / "settings_tunisia.json")

from jobtrack.models import Application  # noqa: E402
from jobtrack.storage import Store  # noqa: E402


@pytest.fixture
def store_path(tmp_path):
    return tmp_path / "applications.json"


@pytest.fixture
def store(store_path):
    return Store(store_path).load()


@pytest.fixture
def sample_apps(store):
    store.add(Application(id=1, company="Acme", role="Backend Engineer", status="applied"))
    store.add(
        Application(
            id=2, company="Globex", role="Data Analyst", status="interviewing", location="Remote"
        )
    )
    store.add(Application(id=3, company="Initech", role="SRE", status="rejected"))
    store.save()
    return store
