"""Every file the UI reads or writes, in one place, so tests can point it at tmp_path."""

from __future__ import annotations

from dataclasses import dataclass
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
        )

    @classmethod
    def default(cls) -> Paths:
        """This repo - honouring $JOBTRACK_FILE for the store, as the CLI does."""
        from jobtrack.storage import default_path

        return cls.under(ROOT).with_store(default_path())

    def with_store(self, store: Path) -> Paths:
        return Paths(
            self.root, self.sheet, self.data, self.briefs, store, self.tailored, self.applications
        )
