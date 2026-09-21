"""Rendering helpers: tables, stats, CSV export."""

from __future__ import annotations

import csv
import io
from collections import Counter
from collections.abc import Sequence

from .models import STATUSES, Application

COLUMNS = ("ID", "COMPANY", "ROLE", "STATUS", "APPLIED", "AGE")


def render_table(apps: Sequence[Application]) -> str:
    """Fixed-width table sized to its contents."""
    if not apps:
        return "No applications yet. Add one with: jobtrack add <company> <role>"

    rows = [
        (
            str(a.id),
            a.company,
            a.role,
            a.status,
            a.applied_on,
            f"{a.days_since_applied()}d",
        )
        for a in apps
    ]
    widths = [max(len(header), *(len(row[i]) for row in rows)) for i, header in enumerate(COLUMNS)]

    def line(cells: Sequence[str]) -> str:
        return "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells)).rstrip()

    out = [line(COLUMNS), "  ".join("-" * w for w in widths)]
    out.extend(line(row) for row in rows)
    return "\n".join(out)


def render_detail(app: Application) -> str:
    fields = [
        ("Company", app.company),
        ("Role", app.role),
        ("Status", app.status),
        ("Applied", f"{app.applied_on} ({app.days_since_applied()} days ago)"),
        ("Location", app.location),
        ("Salary", app.salary),
        ("Contact", app.contact),
        ("URL", app.url),
        ("Updated", app.updated_at),
    ]
    width = max(len(label) for label, _ in fields)
    lines = [f"#{app.id}  {app.company} — {app.role}", ""]
    lines.extend(f"{label.ljust(width)}  {value}" for label, value in fields if value)
    if app.notes:
        lines.append("")
        lines.append("Notes:")
        lines.extend(f"  - {note}" for note in app.notes)
    return "\n".join(lines)


def render_stats(apps: Sequence[Application]) -> str:
    if not apps:
        return "No applications yet."
    counts = Counter(a.status for a in apps)
    total = len(apps)
    open_count = sum(1 for a in apps if a.is_open)
    width = max(len(s) for s in STATUSES)

    lines = [f"Total: {total}   Open: {open_count}   Closed: {total - open_count}", ""]
    for status in STATUSES:
        n = counts.get(status, 0)
        if not n:
            continue
        bar = "#" * n
        lines.append(f"{status.ljust(width)}  {str(n).rjust(3)}  {bar}")

    decided = counts.get("offer", 0) + counts.get("rejected", 0)
    if decided:
        rate = counts.get("offer", 0) / decided * 100
        lines.append("")
        lines.append(f"Offer rate (of {decided} decided): {rate:.0f}%")
    return "\n".join(lines)


def to_csv(apps: Sequence[Application]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(
        ["id", "company", "role", "status", "applied_on", "location", "salary", "url", "notes"]
    )
    for a in apps:
        writer.writerow(
            [
                a.id,
                a.company,
                a.role,
                a.status,
                a.applied_on,
                a.location,
                a.salary,
                a.url,
                " | ".join(a.notes),
            ]
        )
    return buf.getvalue()
