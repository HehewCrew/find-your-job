"""Core data model for a tracked job application."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any

# Ordered roughly by how far along the process is.
STATUSES: tuple[str, ...] = (
    "wishlist",
    "applied",
    "screening",
    "interviewing",
    "offer",
    "rejected",
    "withdrawn",
)

OPEN_STATUSES: frozenset[str] = frozenset(
    {"wishlist", "applied", "screening", "interviewing", "offer"}
)

# An open application nobody has touched for this long has probably gone quiet: most companies
# never send the rejection, so the status would otherwise read "applied" forever.
QUIET_DAYS = 14

# The fields an edit may change. id and the timestamps are the store's, notes have note().
EDITABLE: tuple[str, ...] = (
    "company",
    "role",
    "status",
    "applied_on",
    "url",
    "location",
    "salary",
    "contact",
)


class ValidationError(ValueError):
    """Raised when user input does not describe a valid application."""


def today() -> str:
    return date.today().isoformat()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class Application:
    """A single job application."""

    id: int
    company: str
    role: str
    status: str = "applied"
    applied_on: str = field(default_factory=today)
    url: str = ""
    location: str = ""
    salary: str = ""
    contact: str = ""
    notes: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        self.company = self.company.strip()
        self.role = self.role.strip()
        if not self.company:
            raise ValidationError("company must not be empty")
        if not self.role:
            raise ValidationError("role must not be empty")
        self.status = normalize_status(self.status)
        validate_date(self.applied_on)

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES

    def days_since_applied(self, reference: date | None = None) -> int:
        ref = reference or date.today()
        return (ref - date.fromisoformat(self.applied_on)).days

    def is_quiet(self, reference: date | None = None) -> bool:
        """Open, and not updated in QUIET_DAYS or more."""
        if not self.is_open:
            return False
        ref = reference or date.today()
        return (ref - date.fromisoformat(self.updated_at[:10])).days >= QUIET_DAYS

    def update(self, **fields: Any) -> list[str]:
        """Change fields, validated as __post_init__ would. Returns "field=value" for each
        real change; touches only if there was one. Nothing changes if anything is invalid."""
        unknown = sorted(set(fields) - set(EDITABLE))
        if unknown:
            raise ValidationError(f"cannot edit {', '.join(unknown)}")
        clean: dict[str, str] = {}
        for name, raw in fields.items():
            value = str(raw).strip() if name in ("company", "role") else str(raw)
            if name in ("company", "role") and not value:
                raise ValidationError(f"{name} must not be empty")
            if name == "status":
                value = normalize_status(value)
            if name == "applied_on":
                value = validate_date(value)
            clean[name] = value
        changed = []
        for name, value in clean.items():
            if getattr(self, name) != value:
                setattr(self, name, value)
                changed.append(f"{name}={value}")
        if changed:
            self.touch()
        return changed

    def touch(self) -> None:
        self.updated_at = _now()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Application:
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


def normalize_status(value: str) -> str:
    """Accept case-insensitive input and unambiguous prefixes."""
    candidate = value.strip().lower()
    if not candidate:
        raise ValidationError("status must not be empty")
    if candidate in STATUSES:
        return candidate
    matches = [s for s in STATUSES if s.startswith(candidate)]
    if len(matches) == 1:
        return matches[0]
    raise ValidationError(f"unknown status {value!r}; expected one of: {', '.join(STATUSES)}")


def validate_date(value: str) -> str:
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(f"invalid date {value!r}; expected YYYY-MM-DD") from exc
    return value
