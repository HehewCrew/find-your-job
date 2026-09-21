from __future__ import annotations

from datetime import date

import pytest

from jobtrack.models import Application, ValidationError, normalize_status, validate_date


def test_defaults_are_sensible():
    app = Application(id=1, company="Acme", role="Engineer")
    assert app.status == "applied"
    assert app.applied_on == date.today().isoformat()
    assert app.is_open


def test_company_and_role_are_stripped():
    app = Application(id=1, company="  Acme  ", role="  Engineer ")
    assert app.company == "Acme"
    assert app.role == "Engineer"


@pytest.mark.parametrize("company,role", [("", "Engineer"), ("Acme", "   ")])
def test_blank_fields_rejected(company, role):
    with pytest.raises(ValidationError):
        Application(id=1, company=company, role=role)


@pytest.mark.parametrize(
    "raw,expected",
    [("APPLIED", "applied"), ("interview", "interviewing"), ("off", "offer"), ("w", None)],
)
def test_normalize_status(raw, expected):
    if expected is None:
        with pytest.raises(ValidationError):  # "w" is ambiguous: wishlist / withdrawn
            normalize_status(raw)
    else:
        assert normalize_status(raw) == expected


def test_unknown_status_rejected():
    with pytest.raises(ValidationError):
        normalize_status("ghosted")


def test_validate_date():
    assert validate_date("2026-01-15") == "2026-01-15"
    with pytest.raises(ValidationError):
        validate_date("15/01/2026")


def test_days_since_applied():
    app = Application(id=1, company="Acme", role="Engineer", applied_on="2026-01-01")
    assert app.days_since_applied(reference=date(2026, 1, 11)) == 10


def test_closed_statuses_are_not_open():
    for status in ("rejected", "withdrawn"):
        app = Application(id=1, company="Acme", role="Engineer", status=status)
        assert not app.is_open


def test_round_trip_dict():
    app = Application(id=7, company="Acme", role="Engineer", notes=["phone screen booked"])
    assert Application.from_dict(app.to_dict()) == app


def test_from_dict_ignores_unknown_keys():
    app = Application.from_dict(
        {"id": 1, "company": "Acme", "role": "Engineer", "legacy_field": "junk"}
    )
    assert app.company == "Acme"
