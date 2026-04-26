#!/usr/bin/env python3
"""
One-time migration helper:

- Finds strength workout logs that appear to have stored peak 1RM from the
  old damped-high-rep estimator.
- Recomputes those rows using classic Epley from sets_json:
    1RM = weight * (1 + reps/30)

Timed exercises are intentionally skipped because their stored metric is
timed_set_score (kg * sqrt(seconds)), not 1RM.
"""

from __future__ import annotations

import argparse
import re
from typing import Iterable, Optional

from models import Session, WorkoutLog


def _is_timed_log(log: WorkoutLog) -> bool:
    exercise_string = str(getattr(log, "exercise_string", "") or "")
    exercise_name = str(getattr(log, "exercise", "") or "").lower()

    bracket_parts = re.findall(r"\[([^\]]*)\]", exercise_string)
    for part in bracket_parts:
        token = str(part or "").strip().lower()
        if not token:
            continue
        if re.search(r"(?:\b(?:s|sec|secs|second|seconds)\b|\d+\s*s(?:ec(?:onds?)?)?\b)", token):
            return True

    timed_name_patterns = [
        r"\bdead\s*hang\b",
        r"\bplank\b",
        r"\bforearm\s*roller\b",
        r"\b(?:trap\s*bar\s*)?farmer(?:[’']\s*s)?\s*walk\b",
        r"\b(?:trap\s*bar\s*)?farmers?\s*walk\b",
    ]
    return bool(re.search("|".join(timed_name_patterns), exercise_name, flags=re.IGNORECASE))


def _coerce_sets(sets_json: dict) -> list[tuple[float, int]]:
    if not isinstance(sets_json, dict):
        return []

    weights = list(sets_json.get("weights") or [])
    reps = list(sets_json.get("reps") or [])
    if not weights or not reps:
        return []

    if len(weights) != len(reps):
        if len(weights) < len(reps) and weights:
            weights = weights + [weights[-1]] * (len(reps) - len(weights))
        elif len(reps) < len(weights) and reps:
            reps = reps + [reps[-1]] * (len(weights) - len(reps))

    out: list[tuple[float, int]] = []
    for w, r in zip(weights, reps):
        try:
            wf = float(w)
            ri = int(r)
        except (TypeError, ValueError):
            continue
        if wf <= 0 or ri <= 0:
            continue
        out.append((wf, ri))
    return out


def _peak_epley(pairs: list[tuple[float, int]]) -> Optional[float]:
    if not pairs:
        return None
    return max(w * (1.0 + (r / 30.0)) for w, r in pairs)


def _peak_damped(pairs: list[tuple[float, int]]) -> Optional[float]:
    if not pairs:
        return None
    out = 0.0
    for w, r in pairs:
        eff_reps = r if r <= 10 else 10.0 + (r - 10) * 0.75
        out = max(out, w * (1.0 + (eff_reps / 30.0)))
    return out if out > 0 else None


def _iter_candidate_logs(session) -> Iterable[WorkoutLog]:
    return (
        session.query(WorkoutLog)
        .filter(WorkoutLog.sets_json.isnot(None), WorkoutLog.estimated_1rm.isnot(None))
        .yield_per(500)
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill likely-damped strength WorkoutLog.estimated_1rm to classic Epley."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write updates to DB. Without this flag the script runs as dry-run.",
    )
    parser.add_argument(
        "--eps",
        type=float,
        default=1e-6,
        help="Floating-point tolerance for formula matching.",
    )
    args = parser.parse_args()

    session = Session()
    scanned = 0
    skipped_timed = 0
    skipped_no_sets = 0
    skipped_no_high_rep = 0
    high_rep_strength_rows = 0
    likely_damped_rows = 0
    updated = 0
    unchanged = 0

    try:
        for log in _iter_candidate_logs(session):
            scanned += 1
            if _is_timed_log(log):
                skipped_timed += 1
                continue

            pairs = _coerce_sets(log.sets_json if isinstance(log.sets_json, dict) else {})
            if not pairs:
                skipped_no_sets += 1
                continue

            if not any(r > 10 for _, r in pairs):
                skipped_no_high_rep += 1
                continue

            high_rep_strength_rows += 1
            epley = _peak_epley(pairs)
            damped = _peak_damped(pairs)
            if epley is None or damped is None:
                skipped_no_sets += 1
                continue

            old_value = float(log.estimated_1rm or 0.0)
            is_likely_damped = abs(old_value - damped) <= args.eps and abs(old_value - epley) > args.eps
            if not is_likely_damped:
                unchanged += 1
                continue

            likely_damped_rows += 1
            if args.apply:
                log.estimated_1rm = float(epley)
            updated += 1

            if args.apply and updated % 1000 == 0:
                session.commit()

        if args.apply:
            session.commit()
        else:
            session.rollback()

        print(
            "Classic-Epley damped backfill summary:",
            f"scanned={scanned}",
            f"skipped_timed={skipped_timed}",
            f"skipped_no_sets={skipped_no_sets}",
            f"skipped_no_high_rep={skipped_no_high_rep}",
            f"high_rep_strength_rows={high_rep_strength_rows}",
            f"likely_damped_rows={likely_damped_rows}",
            f"updated={updated if args.apply else 0}",
            f"would_update={updated if not args.apply else 0}",
            f"unchanged={unchanged}",
            f"apply={args.apply}",
        )
        return 0
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
