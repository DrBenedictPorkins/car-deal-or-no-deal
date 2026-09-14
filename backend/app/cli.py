"""Command line entry points.

    python -m app.cli seed          load the Honda reference scenario
    python -m app.cli reset         drop and recreate the database
    python -m app.cli refresh       re-run signals, states, contradictions, notifications
    python -m app.cli report        print the dashboard to the terminal

    python -m app.cli golden-build  regenerate the sanitized golden .eml corpus
    python -m app.cli replay        replay a corpus chronologically and print the timeline
    python -m app.cli sanitize      turn real .eml correspondence into safe fixtures

    python -m app.cli gmail-auth    authorize a Gmail account (OAuth, no password)
    python -m app.cli gmail-seed    stage fixture messages in a dedicated test mailbox
    python -m app.cli gmail-preview dry run: what a historical import would pull in
    python -m app.cli gmail-sync    import from Gmail (incremental unless --historical)
"""

from __future__ import annotations

import argparse
import sys

from app.config import get_settings
from app.db import engine, session_scope
from app.models import Base
from app.services import contradictions, dashboard, notifications, signals, state_engine
from app.services.money import fmt
from app.services.states import ensure_states


def cmd_reset(args: argparse.Namespace) -> int:
    if not args.yes:
        settings = get_settings()
        print(f"This deletes every table in {settings.sqlalchemy_url}. Pass --yes to confirm.")
        return 1
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with session_scope() as db:
        ensure_states(db)
    print("Database reset.")
    return 0


def cmd_seed(args: argparse.Namespace) -> int:
    from app.fixtures.honda_scenario import load

    Base.metadata.create_all(bind=engine)
    with session_scope() as db:
        ids = load(db, accepted=not args.live)
    cmd_refresh(args)
    print(f"Loaded {len(ids)} dealers: " + ", ".join(sorted(ids)))
    return 0


def cmd_refresh(args: argparse.Namespace) -> int:  # noqa: ARG001
    with session_scope() as db:
        added = signals.refresh_signals(db)
        transitions = state_engine.refresh_all(db)
        found = contradictions.detect_all(db)
        notes = notifications.refresh(db)
    print(
        f"signals +{added}, transitions {len(transitions)}, "
        f"contradictions {len(found)}, notifications +{len(notes)}"
    )
    return 0


def cmd_report(args: argparse.Namespace) -> int:  # noqa: ARG001
    with session_scope() as db:
        view = dashboard.build(db)

    s = view.summary
    print()
    print(f"  {s.dealers_total} dealers · {s.dealers_responded} responded · "
          f"{s.dealers_silent} silent · {s.dealers_with_offer} with a priced offer")
    print(f"  Best OTD: {fmt(s.best_otd_cents)} ({s.best_otd_dealer})")
    print(f"  Best dealer-controlled: {fmt(s.best_dealer_controlled_cents)} "
          f"({s.best_dealer_controlled_dealer})")
    print(f"  You owe {s.you_owe_count} · dealers owe {s.dealer_owes_count} · "
          f"{s.open_contradictions} contradiction(s)")
    print()
    header = f"  {'DEALER':<22}{'OTD':>12}{'DEALER COST':>14}  {'STATE':<16}NEXT ACTION"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for row in view.rows:
        print(
            f"  {row.dealer_name[:21]:<22}"
            f"{fmt(row.otd_cents):>12}"
            f"{fmt(row.dealer_controlled_cents):>14}  "
            f"{row.state_label[:15]:<16}"
            f"{row.next_action[:46]}"
        )
    print()
    return 0


# --------------------------------------------------------------------- corpus


def cmd_golden_build(args: argparse.Namespace) -> int:  # noqa: ARG001
    from pathlib import Path

    from app.fixtures import golden_corpus

    target = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "golden"
    written = golden_corpus.write(target)
    print(f"Wrote {len(written)} sanitized messages to {target}")
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    from pathlib import Path

    from app.fixtures import golden_corpus
    from app.ingestion import eml
    from app.ingestion.replay import replay

    if args.golden:
        directory = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "golden"
    else:
        directory = Path(args.directory or (get_settings().data_dir / "replay"))

    if not directory.is_dir():
        print(f"No such directory: {directory}")
        return 1

    if args.reset:
        Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    with session_scope() as db:
        ensure_states(db)
        if args.golden and args.reset:
            golden_corpus.seed_profile(db)
            golden_corpus.seed_dealers(db)
            db.flush()

        messages = eml.load_directory(directory, source_system="replay")
        if not messages:
            print(f"No .eml files in {directory}")
            return 1

        run = replay(db, messages)
        print()
        for line in run.timeline():
            print(line)
        print()
        final = run.final
        print(f"  Best out-the-door: {fmt(final.best_otd_cents)} ({final.best_otd_dealer})")
        for name, snapshot in sorted(final.dealers.items()):
            friction = ", ".join(snapshot.friction_codes)
            print(
                f"  {name[:24]:<26}{snapshot.state[:16]:<18}"
                f"{fmt(snapshot.otd_cents):>12}  "
                + (f"friction: {friction}" if friction else "")
            )
        print()
    return 0


def cmd_sanitize(args: argparse.Namespace) -> int:
    from pathlib import Path

    from app.ingestion.sanitize import SanitizerConfig, sanitize_directory

    source, destination = Path(args.source), Path(args.destination)
    if not source.is_dir():
        print(f"No such directory: {source}")
        return 1

    config = SanitizerConfig(
        buyer_addresses=tuple(args.buyer_email or ()),
        buyer_names=tuple(args.buyer_name or ()),
        keep_vins=args.keep_vins,
        extra_literals=tuple(args.redact or ()),
    )
    report = sanitize_directory(
        source, destination, config=config, write_mapping=args.write_mapping
    )
    print(f"Wrote {len(report.files_written)} file(s) to {destination}")
    print(f"Replaced: {report.summary()}")
    if args.write_mapping:
        print(
            "\n  WARNING: mapping.private.json re-identifies everything in this "
            "directory.\n  Keep it out of version control."
        )
    if report.residue:
        print("\n  Review these before committing:")
        for item in report.residue[:40]:
            print(f"    - {item}")
    else:
        print("  No residue detected. Read a couple of the files anyway.")
    return 0


# ----------------------------------------------------------------------- gmail


def cmd_gmail_auth(args: argparse.Namespace) -> int:  # noqa: ARG001
    from app.ingestion.gmail.auth import GmailUnavailable, get_credentials, required_scopes

    settings = get_settings()
    try:
        get_credentials(settings)
    except GmailUnavailable as exc:
        print(exc)
        return 1
    print("Authorized. Scopes granted: " + ", ".join(required_scopes(settings)))
    print(f"Token stored (encrypted) at {settings.token_path}")
    return 0


def cmd_gmail_preview(args: argparse.Namespace) -> int:
    from app.ingestion.gmail.auth import GmailUnavailable
    from app.ingestion.gmail.source import GmailSource

    settings = get_settings()
    try:
        rows = GmailSource(settings).preview(limit=args.limit)
    except GmailUnavailable as exc:
        print(exc)
        return 1
    print(f"Query: {settings.gmail_import_query or '(everything)'}")
    for row in rows:
        print(f"  {row['sent_at'][:16]}  {str(row['from'])[:36]:<38}{str(row['subject'])[:60]}")
    print(f"\n  {len(rows)} shown. Nothing has been imported.")
    return 0


def cmd_gmail_seed(args: argparse.Namespace) -> int:
    """Stage fixture messages in a dedicated test mailbox. Never a personal one."""
    from pathlib import Path

    from app.fixtures import golden_corpus
    from app.ingestion import eml
    from app.ingestion.gmail import client as gmail_client
    from app.ingestion.gmail import seeder
    from app.ingestion.gmail.auth import GmailUnavailable, get_credentials

    settings = get_settings()
    if args.directory:
        messages = eml.load_directory(Path(args.directory), source_system="fixture")
    else:
        messages = golden_corpus.build()

    try:
        service = gmail_client.build_service(get_credentials(settings))
        if args.purge:
            removed = seeder.purge(service, args.purge)
            print(f"Deleted {removed} message(s) matching {args.purge!r}")
            return 0
        ids = seeder.insert(
            service, messages, settings=settings, account=settings.gmail_account
        )
    except (GmailUnavailable, seeder.InsertRefused) as exc:
        print(exc)
        return 1
    print(f"Inserted {len(ids)} message(s) into {settings.gmail_account}")
    print("Nothing was sent — insert writes directly to the mailbox.")
    return 0


def cmd_gmail_sync(args: argparse.Namespace) -> int:
    from app.ingestion.base import get_source
    from app.ingestion.gmail.auth import GmailUnavailable
    from app.services import sync as sync_service

    settings = get_settings()
    Base.metadata.create_all(bind=engine)
    try:
        with session_scope() as db:
            ensure_states(db)
            report = sync_service.run(
                db,
                get_source(settings),
                mode="historical" if args.historical else "incremental",
                account=settings.gmail_account,
                limit=args.limit,
            )
    except GmailUnavailable as exc:
        print(exc)
        return 1
    print(
        f"{report.mode}: fetched {report.fetched}, new {report.created}, "
        f"duplicates {report.duplicates}, unresolved {report.unresolved}, "
        f"offers {report.offers}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dealbench")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("reset", help="drop and recreate all tables")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_reset)

    p = sub.add_parser("seed", help="load the Honda reference scenario")
    p.add_argument(
        "--live",
        action="store_true",
        help="leave the negotiation in progress instead of marking it accepted",
    )
    p.set_defaults(func=cmd_seed)

    p = sub.add_parser("refresh", help="re-run every deterministic pass")
    p.set_defaults(func=cmd_refresh)

    p = sub.add_parser("report", help="print the dashboard")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("golden-build", help="regenerate the sanitized golden corpus")
    p.set_defaults(func=cmd_golden_build)

    p = sub.add_parser("replay", help="replay a corpus chronologically")
    p.add_argument("--directory", help="directory of .eml files")
    p.add_argument("--golden", action="store_true", help="use the committed golden corpus")
    p.add_argument("--reset", action="store_true", help="drop the database first")
    p.set_defaults(func=cmd_replay)

    p = sub.add_parser("sanitize", help="turn real .eml correspondence into fixtures")
    p.add_argument("source", help="directory of real .eml files")
    p.add_argument("destination", help="where to write the sanitized copies")
    p.add_argument("--buyer-email", action="append", help="your address (repeatable)")
    p.add_argument("--buyer-name", action="append", help="your name (repeatable)")
    p.add_argument("--redact", action="append", help="any other literal to remove")
    p.add_argument("--keep-vins", action="store_true", help="leave VINs intact")
    p.add_argument(
        "--write-mapping",
        action="store_true",
        help="also write the re-identification key (never commit it)",
    )
    p.set_defaults(func=cmd_sanitize)

    p = sub.add_parser("gmail-auth", help="authorize a Gmail account via OAuth")
    p.set_defaults(func=cmd_gmail_auth)

    p = sub.add_parser("gmail-preview", help="dry run of a historical import")
    p.add_argument("--limit", type=int, default=25)
    p.set_defaults(func=cmd_gmail_preview)

    p = sub.add_parser(
        "gmail-seed", help="stage fixture messages in a dedicated test mailbox"
    )
    p.add_argument("--directory", help="directory of .eml files (default: golden corpus)")
    p.add_argument("--purge", help="instead, delete everything matching this Gmail query")
    p.set_defaults(func=cmd_gmail_seed)

    p = sub.add_parser("gmail-sync", help="import from Gmail")
    p.add_argument("--historical", action="store_true", help="full import, not incremental")
    p.add_argument("--limit", type=int, default=None)
    p.set_defaults(func=cmd_gmail_sync)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
