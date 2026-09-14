"""Command line entry points.

    python -m app.cli seed      load the Honda reference scenario
    python -m app.cli reset     drop and recreate the database
    python -m app.cli refresh   re-run signals, states, contradictions, notifications
    python -m app.cli report    print the dashboard to the terminal
"""

from __future__ import annotations

import argparse
import sys

from app.config import get_settings
from app.db import engine, session_scope
from app.models import Base
from app.services import behavior, contradictions, dashboard, notifications, state_engine
from app.services.context import build_contexts
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
    from app.models import BehaviorSignal

    with session_scope() as db:
        existing = {s.dedupe_key for s in db.query(BehaviorSignal).all() if s.dedupe_key}
        added = 0
        for ctx in build_contexts(db).values():
            for payload in behavior.derive_signals(ctx):
                if payload["dedupe_key"] in existing:
                    continue
                existing.add(payload["dedupe_key"])
                db.add(BehaviorSignal(**payload))
                added += 1
        db.flush()
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

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
