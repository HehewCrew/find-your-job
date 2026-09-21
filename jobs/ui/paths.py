"""Every file the UI reads or writes, in one place, so tests can point it at tmp_path."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


@dataclass(frozen=True)
class Paths:
    root: Path
    sheet: Path  # TODAY_SCRAPING.md
    data: Path  # TODAY_SCRAPING.json
    briefs: Path  # cv/out/tailored/BRIEFS.md
    store: Path  # applications.json (the jobtrack store)
    tailored: Path  # cv/out/tailored/
    applications: Path  # applications/ (one folder per application sent)
    # The four personal files and the tracked examples they start from.
    profile: Path
    profile_example: Path
    settings: Path
    settings_example: Path
    priorities: Path
    priorities_example: Path
    rules: Path
    rules_example: Path
    preview: Path  # cv/out/preview/ - Setup's "Preview CV" builds

    @classmethod
    def under(cls, root: Path) -> Paths:
        """The repo's layout, rooted anywhere - the real repo or a test's tmp_path."""
        tailored = root / "cv" / "out" / "tailored"
        return cls(
            root=root,
            sheet=root / "TODAY_SCRAPING.md",
            data=root / "TODAY_SCRAPING.json",
            briefs=tailored / "BRIEFS.md",
            store=root / "applications.json",
            tailored=tailored,
            applications=root / "applications",
            profile=root / "cv" / "profile.json",
            profile_example=root / "cv" / "profile.example.json",
            settings=root / "jobs" / "settings.json",
            settings_example=root / "jobs" / "settings.example.json",
            priorities=root / "jobs" / "priorities.md",
            priorities_example=root / "jobs" / "priorities.example.md",
            rules=root / "cv" / "rules.md",
            rules_example=root / "cv" / "rules.example.md",
            preview=root / "cv" / "out" / "preview",
        )

    @classmethod
    def default(cls) -> Paths:
        """This repo - honouring $JOBTRACK_FILE for the store, as the CLI does."""
        from jobtrack.storage import default_path

        return cls.under(ROOT).with_store(default_path())

    def with_store(self, store: Path) -> Paths:
        return replace(self, store=store)
