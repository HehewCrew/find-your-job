from __future__ import annotations

import pytest

from jobtrack.cli import main
from jobtrack.storage import Store


def run(args, path):
    return main(["--file", str(path), *args])


def test_add_then_list(store_path, capsys):
    assert run(["add", "Acme", "Backend Engineer"], store_path) == 0
    assert "Added #1" in capsys.readouterr().out

    assert run(["list"], store_path) == 0
    out = capsys.readouterr().out
    assert "Acme" in out and "Backend Engineer" in out


def test_add_accepts_status_prefix(store_path, capsys):
    run(["add", "Acme", "Engineer", "--status", "interview"], store_path)
    capsys.readouterr()
    run(["show", "1"], store_path)
    assert "interviewing" in capsys.readouterr().out


def test_add_rejects_bad_status(store_path, capsys):
    assert run(["add", "Acme", "Engineer", "--status", "ghosted"], store_path) == 2
    assert "unknown status" in capsys.readouterr().err


def test_add_rejects_bad_date(store_path, capsys):
    assert run(["add", "Acme", "Engineer", "--date", "01-01-2026"], store_path) == 2
    assert "invalid date" in capsys.readouterr().err


def test_list_empty(store_path, capsys):
    assert run(["list"], store_path) == 0
    assert "No applications yet" in capsys.readouterr().out


def test_update_persists(store_path, capsys):
    run(["add", "Acme", "Engineer"], store_path)
    assert run(["update", "1", "--status", "offer"], store_path) == 0
    capsys.readouterr()
    assert Store(store_path).load().get(1).status == "offer"


def test_update_with_no_fields_is_an_error(store_path, capsys):
    run(["add", "Acme", "Engineer"], store_path)
    assert run(["update", "1"], store_path) == 1
    assert "Nothing to update" in capsys.readouterr().out


def test_commands_on_missing_id_return_1(store_path, capsys):
    for args in (["show", "42"], ["update", "42", "--status", "offer"], ["rm", "42"]):
        assert run(args, store_path) == 1
        assert "No application with id 42" in capsys.readouterr().err


def test_note_appends(store_path, capsys):
    run(["add", "Acme", "Engineer"], store_path)
    run(["note", "1", "phone screen Tuesday"], store_path)
    capsys.readouterr()
    assert Store(store_path).load().get(1).notes == ["phone screen Tuesday"]


def test_rm_deletes(store_path, capsys):
    run(["add", "Acme", "Engineer"], store_path)
    assert run(["rm", "1"], store_path) == 0
    capsys.readouterr()
    assert len(Store(store_path).load()) == 0


def test_search_no_match_returns_1(store_path, capsys):
    run(["add", "Acme", "Engineer"], store_path)
    assert run(["search", "zzz"], store_path) == 1


def test_stats_reports_totals(store_path, capsys):
    run(["add", "Acme", "Engineer"], store_path)
    run(["add", "Globex", "Analyst", "--status", "rejected"], store_path)
    capsys.readouterr()
    run(["stats"], store_path)
    assert "Total: 2" in capsys.readouterr().out


def test_export_to_stdout_and_file(store_path, tmp_path, capsys):
    run(["add", "Acme", "Engineer"], store_path)
    capsys.readouterr()

    run(["export"], store_path)
    assert "id,company,role" in capsys.readouterr().out

    target = tmp_path / "out.csv"
    run(["export", "-o", str(target)], store_path)
    capsys.readouterr()
    assert "Acme" in target.read_text(encoding="utf-8")


def test_env_var_is_used_when_no_flag(tmp_path, monkeypatch, capsys):
    target = tmp_path / "env.json"
    monkeypatch.setenv("JOBTRACK_FILE", str(target))
    assert main(["add", "Acme", "Engineer"]) == 0
    capsys.readouterr()
    assert target.exists()


def test_no_command_exits(capsys):
    with pytest.raises(SystemExit):
        main([])
