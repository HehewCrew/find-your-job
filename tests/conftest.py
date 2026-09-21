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


ROOT = FIXTURES.parent.parent
GOLDEN = FIXTURES.parent / "golden"


@pytest.fixture
def cv_sandbox(tmp_path, monkeypatch):
    """Build tailored CVs into tmp_path from the example profile, with no PDF engine.

    Word and LibreOffice are both reported missing, so the result is the same on every
    machine: a .docx and no PDF. Returns the sandbox's `cv/out`.
    """
    from jobs import brief
    from jobs import tailor as tl

    out = tmp_path / "cvout"
    monkeypatch.setattr(tl.cvbuild, "OUT_DIR", out)
    monkeypatch.setattr(tl, "TAILORED_DIR", out / "tailored")
    monkeypatch.setattr(brief, "TAILORED_DIR", out / "tailored")
    monkeypatch.setattr(tl, "PROFILE_PATH", ROOT / "cv" / "profile.example.json")
    monkeypatch.setattr(tl.cvbuild, "_word_to_pdf", lambda paths, **kw: [])
    monkeypatch.setattr(tl.cvbuild, "_find_soffice", lambda: None)
    return out


@pytest.fixture
def golden(tmp_path):
    """Compare a CLI run to tests/golden/<name>.txt; UPDATE_GOLDEN=1 rewrites it.

    tmp_path is replaced by <tmp> and backslashes by slashes, so the file is the same on
    every machine and OS.
    """

    def check(name: str, code: int, out: str, err: str) -> None:
        text = f"exit={code}\n--- stdout\n{out}--- stderr\n{err}"
        text = text.replace(str(tmp_path), "<tmp>").replace("\\", "/")
        path = GOLDEN / f"{name}.txt"
        if os.environ.get("UPDATE_GOLDEN"):
            path.parent.mkdir(exist_ok=True)
            path.write_text(text, encoding="utf-8")
            return
        if not path.exists():
            pytest.fail(f"no golden file {path.name}; run once with UPDATE_GOLDEN=1")
        assert text == path.read_text(encoding="utf-8")

    return check
