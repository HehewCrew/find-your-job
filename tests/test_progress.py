from __future__ import annotations

from jobs import errors, progress


def test_emit_with_no_reporter_does_nothing():
    progress.emit(None, "fetch", "ignored")  # must not raise


def test_emit_builds_the_event():
    got: list[progress.Progress] = []
    progress.emit(got.append, "fetch", "3/9 remotive", done=3, total=9, detail=True)
    assert got == [progress.Progress("fetch", "3/9 remotive", 3, 9, True)]


def test_printer_prints_only_non_detail_events(capsys):
    report = progress.printer()
    report(progress.Progress("fetch", "Fetching sources…"))
    report(progress.Progress("fetch", "1/9 remotive", 1, 9, detail=True))
    assert capsys.readouterr().out == "Fetching sources…\n"


def test_skipped_is_a_jobs_error_carrying_the_assessment():
    exc = errors.Skipped(assessment="A")
    assert isinstance(exc, errors.JobsError)
    assert exc.assessment == "A"
