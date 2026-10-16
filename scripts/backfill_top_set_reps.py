#!/usr/bin/env python3
"""
One-time fix for stored top sets:

When two sets shared the heaviest weight (60x12, 60x16), the saved top set took
whichever came first. The top set is now the heaviest weight with the most reps
done at it, so this recomputes top_reps for logs saved before that change.

Dry run by default; pass --apply to write.
"""

from __future__ import annotations

import argparse
import os
import sys

# Let this script import project modules when run as `python scripts/<name>.py`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import Session, WorkoutLog
from services.bodyweight import effective_sets_for_log
from services.helpers import top_set


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the changes")
    args = parser.parse_args()

    session = Session()
    changed = 0
    try:
        logs = session.query(WorkoutLog).filter(WorkoutLog.top_weight.isnot(None)).all()
        for log in logs:
            sets = effective_sets_for_log(session, log) or {}
            weight, reps = top_set(sets.get("weights"), sets.get("reps"))
            if weight is None or reps is None:
                continue
            # Only the reps were wrong; leave logs whose top weight disagrees alone.
            if abs(float(log.top_weight) - weight) > 0.01 or log.top_reps == reps:
                continue
            print(f"{log.id}  {log.date:%Y-%m-%d}  {log.exercise}: "
                  f"{log.top_weight:g} x {log.top_reps} -> {weight:g} x {reps}")
            log.top_reps = reps
            changed += 1
        if args.apply:
            session.commit()
            print(f"Updated {changed} log(s).")
        else:
            session.rollback()
            print(f"{changed} log(s) would change. Run with --apply to write.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
