#!/usr/bin/env python3
"""
One-time backfill for WorkoutLog.estimated_1rm using Calculator A:
1RM = weight * (1 + reps/30)
"""

from __future__ import annotations

import argparse
from typing import Iterable, Optional

from models import Session, WorkoutLog


def _calc_peak_estimated_1rm(sets_json: dict) -> Optional[float]:
    if not isinstance(sets_json, dict):
        return None
    weights = list(sets_json.get("weights") or [])
    reps = list(sets_json.get("reps") or [])
    if not weights or not reps:
        return None

    if len(weights) != len(reps):
        if len(weights) < len(reps) and weights:
            weights = weights + [weights[-1]] * (len(reps) - len(weights))
        elif len(reps) < len(weights) and reps:
            reps = reps + [reps[-1]] * (len(weights) - len(reps))

    peak = 0.0
    for weight_raw, reps_raw in zip(weights, reps):
        try:
            weight = float(weight_raw)
            reps_value = int(reps_raw)
        except (TypeError, ValueError):
            continue
        if weight <= 0 or reps_value <= 0:
            continue
        est = weight * (1.0 + (reps_value / 30.0))
        if est > peak:
            peak = est
    return peak if peak > 0 else None


def _iter_logs_with_sets(session) -> Iterable[WorkoutLog]:
    return (
        session.query(WorkoutLog)
        .filter(WorkoutLog.sets_json.isnot(None))
        .yield_per(500)
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill WorkoutLog.estimated_1rm with Calculator A.")
    parser.add_argument("--dry-run", action="store_true", help="Compute changes without writing.")
    args = parser.parse_args()

    session = Session()
    scanned = 0
    updated = 0
    unchanged = 0
    skipped = 0

    try:
        for log in _iter_logs_with_sets(session):
            scanned += 1
            new_value = _calc_peak_estimated_1rm(log.sets_json)
            old_value = float(log.estimated_1rm) if log.estimated_1rm is not None else None

            if new_value is None:
                skipped += 1
                continue

            if old_value is not None and abs(old_value - new_value) < 1e-9:
                unchanged += 1
                continue

            updated += 1
            if not args.dry_run:
                log.estimated_1rm = new_value

            if not args.dry_run and updated % 1000 == 0:
                session.commit()

        if args.dry_run:
            session.rollback()
        else:
            session.commit()

        print(
            "Backfill complete.",
            f"scanned={scanned}",
            f"updated={updated}",
            f"unchanged={unchanged}",
            f"skipped={skipped}",
            f"dry_run={args.dry_run}",
        )
        return 0
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
