"""Errors a `run`-style function raises instead of printing and exiting.

`main` turns each into the exit code the CLI always used: `JobsError` means there was
nothing to do (exit 1). Bad input stays `ValidationError` / `SettingsError` (exit 2).
"""

from __future__ import annotations


class JobsError(Exception):
    """Nothing to do: no sheet, nothing ticked, briefs still pending."""


class Skipped(JobsError):
    """paste's verdict was skip and the build was not forced."""

    def __init__(self, assessment: object) -> None:
        super().__init__("Skipped - nothing built. Rerun with --force to tailor it anyway.")
        self.assessment = assessment
