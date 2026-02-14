from __future__ import annotations

from typing import Dict, List, Tuple

from services.workout_quality import WorkoutQualityScorer


def coerce_equal_len_sets(weights: List, reps: List) -> Tuple[List[float], List[int]]:
    """
    Align weights/reps lengths without inventing extra sets.

    Notes:
    - Workout logs are usually already aligned by the parser.
    - If lengths mismatch, we only pad the shorter side using the last known value,
      mirroring existing behavior in other parts of the codebase.
    """
    weights = list(weights or [])
    reps = list(reps or [])

    if not weights and not reps:
        return [], []

    if len(weights) != len(reps):
        if len(weights) < len(reps) and weights:
            weights = weights + [weights[-1]] * (len(reps) - len(weights))
        elif len(reps) < len(weights) and reps:
            reps = reps + [reps[-1]] * (len(weights) - len(reps))

    out_w: List[float] = []
    out_r: List[int] = []
    for w, r in zip(weights, reps):
        try:
            wf = float(w)
            ri = int(r)
        except Exception:
            continue
        if wf <= 0 or ri <= 0:
            continue
        out_w.append(wf)
        out_r.append(ri)

    return out_w, out_r


def best_workout_strength_score(
    sets_json: Dict,
    *,
    top_n: int = 3,
) -> Dict[str, float]:
    """
    Strength-first score for selecting a "best" workout log for retrieval / lift best.

    Why this exists:
    - A "quality %" score can cause light workouts to outrank heavy workouts.
    - A pure "peak 1RM" rule can let a 1-set day replace a better multi-set day.

    Rule:
    - Base = peak 1RM.
    - Bonus = 25% of additional strength work across the top-N sets.
      (i.e., more high-quality sets matter, but peak still leads.)
    - No rep-range penalty: ranges are guidance, not strict pass/fail.
    """
    if not sets_json or not isinstance(sets_json, dict):
        return {"score": 0.0, "peak_1rm": 0.0, "top_sum_1rm": 0.0, "set_count": 0.0}

    weights = sets_json.get("weights") or []
    reps = sets_json.get("reps") or []
    w, r = coerce_equal_len_sets(weights, reps)
    if not w or not r:
        return {"score": 0.0, "peak_1rm": 0.0, "top_sum_1rm": 0.0, "set_count": 0.0}

    one_rms = [WorkoutQualityScorer.estimate_1rm(wi, ri) for wi, ri in zip(w, r)]
    one_rms = [float(x) for x in one_rms if x and x > 0]
    if not one_rms:
        return {"score": 0.0, "peak_1rm": 0.0, "top_sum_1rm": 0.0, "set_count": float(len(r) or 0)}

    one_rms.sort(reverse=True)
    n = max(1, int(top_n) if isinstance(top_n, int) and top_n > 0 else 3)
    top = one_rms[: min(n, len(one_rms))]

    peak = float(top[0])
    top_sum = float(sum(top))
    bonus = 0.25 * float(max(0.0, top_sum - peak))
    score = peak + bonus

    return {
        "score": float(score),
        "peak_1rm": peak,
        "top_sum_1rm": top_sum,
        "set_count": float(len(r)),
    }
