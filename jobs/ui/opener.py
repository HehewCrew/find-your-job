"""Open a CV in its default app, or show it selected in the file manager."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def open_path(path: Path, reveal: bool = False) -> None:
    """Open `path`, or with `reveal` show it in its folder - ready to attach to a form."""
    if sys.platform == "win32":
        if reveal:
            subprocess.Popen(["explorer", f"/select,{path}"])
        else:
            os.startfile(path)  # noqa: S606 - a local file the allowlist already vetted
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)] if reveal else ["open", str(path)])
    else:
        # xdg-open has no "select this file"; the folder is the closest it gets.
        subprocess.Popen(["xdg-open", str(path.parent if reveal else path)])
