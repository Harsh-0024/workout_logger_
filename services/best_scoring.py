from __future__ import annotations

from typing import Dict, List, Tuple

from services.workout_quality import WorkoutQualityScorer
from services.helpers import timed_set_score


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


def _lexicographic_compare_desc(left: List[float], right: List[float], *, eps: float = 1e-9) -> Tuple[int, int | None]:
    """
    Compare two descending vectors lexicographically.

    Returns:
    - 1 if left wins
    - -1 if right wins
    - 0 if tied

    Second return value is the first differing index, or None when tied.
    """
    limit = max(len(left), len(right))
    for idx in range(limit):
        lv = left[idx] if idx < len(left) else None
        rv = right[idx] if idx < len(right) else None
        if lv is None and rv is None:
            return 0, None
        if lv is None:
            return -1, idx
        if rv is None:
            return 1, idx
        if abs(float(lv) - float(rv)) <= eps:
            continue
        return (1, idx) if float(lv) > float(rv) else (-1, idx)
    return 0, None


def _strength_rank_vectors(
    sets_json: Dict,
    *,
    top_n: int = 3,
) -> Dict[str, List[float]]:
    weights = sets_json.get("weights") or []
    reps = sets_json.get("reps") or []
    w, r = coerce_equal_len_sets(weights, reps)
    if not w or not r:
        return {"scores": [], "weights": []}

    one_rms = [float(WorkoutQualityScorer.estimate_1rm(wi, ri) or 0.0) for wi, ri in zip(w, r)]
    pairs = [(score, float(weight)) for score, weight in zip(one_rms, w) if score > 0 and weight > 0]
    if not pairs:
        return {"scores": [], "weights": []}

    pairs.sort(key=lambda item: (item[0], item[1]), reverse=True)
    n = max(1, int(top_n) if isinstance(top_n, int) and top_n > 0 else 3)
    top = pairs[: min(n, len(pairs))]
    return {
        "scores": [score for score, _weight in top],
        "weights": sorted((weight for _score, weight in top), reverse=True),
    }


def _timed_rank_vectors(
    sets_json: Dict,
    *,
    top_n: int = 3,
) -> Dict[str, List[float]]:
    weights = sets_json.get("weights") or []
    reps = sets_json.get("reps") or []
    w, r = coerce_equal_len_sets(weights, reps)
    if not w or not r:
        return {"scores": [], "weights": []}

    timed_scores = [float(timed_set_score(wi, ri) or 0.0) for wi, ri in zip(w, r)]
    pairs = [(score, float(weight)) for score, weight in zip(timed_scores, w) if score > 0 and weight > 0]
    if not pairs:
        return {"scores": [], "weights": []}

    pairs.sort(key=lambda item: (item[0], item[1]), reverse=True)
    n = max(1, int(top_n) if isinstance(top_n, int) and top_n > 0 else 3)
    top = pairs[: min(n, len(pairs))]
    return {
        "scores": [score for score, _weight in top],
        "weights": sorted((weight for _score, weight in top), reverse=True),
    }


def _compare_rank_vectors(
    previous: Dict[str, List[float]],
    current: Dict[str, List[float]],
) -> Dict[str, float | int | str | None]:
    perf_cmp, perf_idx = _lexicographic_compare_desc(current.get("scores") or [], previous.get("scores") or [])
    if perf_cmp != 0:
        return {
            "cmp": perf_cmp,
            "reason": "peak" if perf_idx == 0 else "consistency",
            "diff_index": perf_idx,
            "diff": (
                (current["scores"][perf_idx] - previous["scores"][perf_idx])
                if perf_idx is not None
                and perf_idx < len(current.get("scores") or [])
                and perf_idx < len(previous.get("scores") or [])
                else None
            ),
        }

    weight_cmp, weight_idx = _lexicographic_compare_desc(current.get("weights") or [], previous.get("weights") or [])
    if weight_cmp != 0:
        return {
            "cmp": weight_cmp,
            "reason": "consistency",
            "diff_index": weight_idx,
            "diff": (
                (current["weights"][weight_idx] - previous["weights"][weight_idx])
                if weight_idx is not None
                and weight_idx < len(current.get("weights") or [])
                and weight_idx < len(previous.get("weights") or [])
                else None
            ),
        }

    return {"cmp": 0, "reason": "same", "diff_index": None, "diff": 0.0}


def compare_strength_workouts(
    previous_sets_json: Dict,
    current_sets_json: Dict,
    *,
    top_n: int = 3,
) -> Dict[str, float | int | str | None]:
    """
    Compare two strength workouts lexicographically:
    1. best set
    2. second-best set
    3. third-best set
    4. continue until a difference is found
    5. if all tied, compare weights lexicographically
    """
    previous = _strength_rank_vectors(previous_sets_json or {}, top_n=top_n)
    current = _strength_rank_vectors(current_sets_json or {}, top_n=top_n)
    result = _compare_rank_vectors(previous, current)
    result["previous"] = previous
    result["current"] = current
    return result


def best_workout_timed_score(
    sets_json: Dict,
    *,
    top_n: int = 3,
) -> Dict[str, float]:
    """
    Timed-exercise equivalent of best_workout_strength_score.
    Uses timed_set_score = weight * sqrt(seconds) instead of 1RM.
    Same bonus structure: peak + 25% of additional top-N set surplus.
    """
    if not sets_json or not isinstance(sets_json, dict):
        return {"score": 0.0, "peak_timed": 0.0, "top_sum": 0.0, "set_count": 0.0}

    weights = sets_json.get("weights") or []
    reps = sets_json.get("reps") or []
    w, r = coerce_equal_len_sets(weights, reps)
    if not w or not r:
        return {"score": 0.0, "peak_timed": 0.0, "top_sum": 0.0, "set_count": 0.0}

    scores = [timed_set_score(wi, ri) for wi, ri in zip(w, r)]
    scores = [float(x) for x in scores if x > 0]
    if not scores:
        return {"score": 0.0, "peak_timed": 0.0, "top_sum": 0.0, "set_count": float(len(r) or 0)}

    scores.sort(reverse=True)
    n = max(1, int(top_n) if isinstance(top_n, int) and top_n > 0 else 3)
    top = scores[: min(n, len(scores))]

    peak = float(top[0])
    top_sum = float(sum(top))
    bonus = 0.25 * float(max(0.0, top_sum - peak))
    score = peak + bonus

    return {
        "score": float(score),
        "peak_timed": peak,
        "top_sum": top_sum,
        "set_count": float(len(r)),
    }


def compare_timed_workouts(
    previous_sets_json: Dict,
    current_sets_json: Dict,
    *,
    top_n: int = 3,
) -> Dict[str, float | int | str | None]:
    previous = _timed_rank_vectors(previous_sets_json or {}, top_n=top_n)
    current = _timed_rank_vectors(current_sets_json or {}, top_n=top_n)
    result = _compare_rank_vectors(previous, current)
    result["previous"] = previous
    result["current"] = current
    return result
