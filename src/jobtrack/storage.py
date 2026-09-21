"""JSON-file persistence for tracked applications.

The store is deliberately plain JSON so the data stays greppable and
diffable in git if the user wants to version it.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable, Iterator
from pathlib import Path

from .models import Application

DEFAULT_FILENAME = "applications.json"
SCHEMA_VERSION = 1


def default_path() -> Path:
    """Location of the data file, overridable via JOBTRACK_FILE."""
    override = os.environ.get("JOBTRACK_FILE")
    if override:
        return Path(override).expanduser()
    return Path.cwd() / DEFAULT_FILENAME


class Store:
    """Load/save a collection of applications backed by a JSON file."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else default_path()
        self._apps: list[Application] = []
        self._loaded = False

    # -- persistence ---------------------------------------------------

    def load(self) -> Store:
        if not self.path.exists():
            self._apps = []
            self._loaded = True
            return self
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        records = raw.get("applications", []) if isinstance(raw, dict) else raw
        self._apps = [Application.from_dict(r) for r in records]
        self._loaded = True
        return self

    def save(self) -> None:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "applications": [a.to_dict() for a in self._apps],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write so an interrupted run cannot truncate the file.
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=self.path.parent, delete=False, suffix=".tmp"
        ) as tmp:
            json.dump(payload, tmp, indent=2, ensure_ascii=False)
            tmp.write("\n")
            tmp_path = Path(tmp.name)
        tmp_path.replace(self.path)

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    # -- collection access ---------------------------------------------

    def __iter__(self) -> Iterator[Application]:
        self._ensure_loaded()
        return iter(self._apps)

    def __len__(self) -> int:
        self._ensure_loaded()
        return len(self._apps)

    @property
    def applications(self) -> list[Application]:
        self._ensure_loaded()
        return list(self._apps)

    def next_id(self) -> int:
        self._ensure_loaded()
        return max((a.id for a in self._apps), default=0) + 1

    def add(self, app: Application) -> Application:
        self._ensure_loaded()
        self._apps.append(app)
        return app

    def get(self, app_id: int) -> Application | None:
        self._ensure_loaded()
        return next((a for a in self._apps if a.id == app_id), None)

    def remove(self, app_id: int) -> Application | None:
        self._ensure_loaded()
        app = self.get(app_id)
        if app is not None:
            self._apps.remove(app)
        return app

    def search(self, term: str) -> list[Application]:
        needle = term.strip().lower()
        return [
            a
            for a in self.applications
            if needle in a.company.lower()
            or needle in a.role.lower()
            or needle in a.location.lower()
            or any(needle in n.lower() for n in a.notes)
        ]

    def filter(
        self, statuses: Iterable[str] | None = None, open_only: bool = False
    ) -> list[Application]:
        apps = self.applications
        if statuses:
            wanted = set(statuses)
            apps = [a for a in apps if a.status in wanted]
        if open_only:
            apps = [a for a in apps if a.is_open]
        return apps
