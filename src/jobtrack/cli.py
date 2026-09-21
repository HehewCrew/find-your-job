"""Command-line interface for jobtrack."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__, report
from .models import (
    STATUSES,
    Application,
    ValidationError,
    normalize_status,
    today,
    validate_date,
)
from .storage import Store


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jobtrack",
        description="Track job applications from the command line.",
    )
    parser.add_argument("--version", action="version", version=f"jobtrack {__version__}")
    parser.add_argument(
        "--file",
        type=Path,
        default=None,
        metavar="PATH",
        help="data file to use (default: ./applications.json or $JOBTRACK_FILE)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="add a new application")
    p_add.add_argument("company")
    p_add.add_argument("role")
    p_add.add_argument("--status", default="applied", help=f"one of: {', '.join(STATUSES)}")
    p_add.add_argument("--date", dest="applied_on", default=None, metavar="YYYY-MM-DD")
    p_add.add_argument("--url", default="")
    p_add.add_argument("--location", default="")
    p_add.add_argument("--salary", default="")
    p_add.add_argument("--contact", default="")
    p_add.add_argument("--note", action="append", default=[], dest="notes")
    p_add.set_defaults(func=cmd_add)

    p_list = sub.add_parser("list", help="list applications")
    p_list.add_argument("--status", action="append", default=[], help="filter by status")
    p_list.add_argument("--open", action="store_true", help="only still-active applications")
    p_list.add_argument(
        "--sort",
        choices=("id", "company", "applied", "status"),
        default="id",
    )
    p_list.set_defaults(func=cmd_list)

    p_show = sub.add_parser("show", help="show one application in full")
    p_show.add_argument("id", type=int)
    p_show.set_defaults(func=cmd_show)

    p_update = sub.add_parser("update", help="change fields on an application")
    p_update.add_argument("id", type=int)
    p_update.add_argument("--status")
    p_update.add_argument("--company")
    p_update.add_argument("--role")
    p_update.add_argument("--date", dest="applied_on", metavar="YYYY-MM-DD")
    p_update.add_argument("--url")
    p_update.add_argument("--location")
    p_update.add_argument("--salary")
    p_update.add_argument("--contact")
    p_update.set_defaults(func=cmd_update)

    p_note = sub.add_parser("note", help="append a note to an application")
    p_note.add_argument("id", type=int)
    p_note.add_argument("text")
    p_note.set_defaults(func=cmd_note)

    p_rm = sub.add_parser("rm", help="delete an application")
    p_rm.add_argument("id", type=int)
    p_rm.set_defaults(func=cmd_rm)

    p_search = sub.add_parser("search", help="free-text search across fields")
    p_search.add_argument("term")
    p_search.set_defaults(func=cmd_search)

    p_stats = sub.add_parser("stats", help="summary of the pipeline")
    p_stats.set_defaults(func=cmd_stats)

    p_export = sub.add_parser("export", help="write all applications as CSV")
    p_export.add_argument("-o", "--output", type=Path, default=None, help="default: stdout")
    p_export.set_defaults(func=cmd_export)

    return parser


# -- commands -----------------------------------------------------------


def cmd_add(args: argparse.Namespace, store: Store) -> int:
    app = Application(
        id=store.next_id(),
        company=args.company,
        role=args.role,
        status=args.status,
        applied_on=args.applied_on or today(),
        url=args.url,
        location=args.location,
        salary=args.salary,
        contact=args.contact,
        notes=list(args.notes),
    )
    store.add(app)
    store.save()
    print(f"Added #{app.id}: {app.company} — {app.role} [{app.status}]")
    return 0


def cmd_list(args: argparse.Namespace, store: Store) -> int:
    statuses = [normalize_status(s) for s in args.status] if args.status else None
    apps = store.filter(statuses=statuses, open_only=args.open)
    keys = {
        "id": lambda a: a.id,
        "company": lambda a: a.company.lower(),
        "applied": lambda a: a.applied_on,
        "status": lambda a: STATUSES.index(a.status),
    }
    apps.sort(key=keys[args.sort])
    print(report.render_table(apps))
    return 0


def cmd_show(args: argparse.Namespace, store: Store) -> int:
    app = store.get(args.id)
    if app is None:
        return _not_found(args.id)
    print(report.render_detail(app))
    return 0


def cmd_update(args: argparse.Namespace, store: Store) -> int:
    app = store.get(args.id)
    if app is None:
        return _not_found(args.id)

    changed: list[str] = []
    if args.status is not None:
        app.status = normalize_status(args.status)
        changed.append(f"status={app.status}")
    if args.applied_on is not None:
        app.applied_on = validate_date(args.applied_on)
        changed.append(f"date={app.applied_on}")
    for attr in ("company", "role", "url", "location", "salary", "contact"):
        value = getattr(args, attr)
        if value is not None:
            setattr(app, attr, value)
            changed.append(f"{attr}={value}")

    if not changed:
        print("Nothing to update. Pass at least one field, e.g. --status interviewing")
        return 1

    app.touch()
    store.save()
    print(f"Updated #{app.id}: {', '.join(changed)}")
    return 0


def cmd_note(args: argparse.Namespace, store: Store) -> int:
    app = store.get(args.id)
    if app is None:
        return _not_found(args.id)
    app.notes.append(args.text)
    app.touch()
    store.save()
    print(f"Note added to #{app.id} ({len(app.notes)} total)")
    return 0


def cmd_rm(args: argparse.Namespace, store: Store) -> int:
    app = store.remove(args.id)
    if app is None:
        return _not_found(args.id)
    store.save()
    print(f"Removed #{app.id}: {app.company} — {app.role}")
    return 0


def cmd_search(args: argparse.Namespace, store: Store) -> int:
    matches = store.search(args.term)
    if not matches:
        print(f"No matches for {args.term!r}")
        return 1
    print(report.render_table(matches))
    return 0


def cmd_stats(args: argparse.Namespace, store: Store) -> int:
    print(report.render_stats(store.applications))
    return 0


def cmd_export(args: argparse.Namespace, store: Store) -> int:
    csv_text = report.to_csv(sorted(store.applications, key=lambda a: a.id))
    if args.output:
        args.output.write_text(csv_text, encoding="utf-8")
        print(f"Wrote {len(store)} applications to {args.output}")
    else:
        sys.stdout.write(csv_text)
    return 0


def _not_found(app_id: int) -> int:
    print(f"No application with id {app_id}", file=sys.stderr)
    return 1


# -- entry point --------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    store = Store(args.file)
    try:
        return args.func(args, store)
    except ValidationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
