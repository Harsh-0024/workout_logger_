"""
Workout logging service for processing and saving workout data.
"""
from typing import Any, List, Dict, Optional
from datetime import datetime, date, timedelta
import re

from models import Lift, RepRange, WorkoutLog
from parsers.workout import align_sets, extract_numbers
from services.best_scoring import (
    best_workout_strength_score,
    best_workout_timed_score,
    compare_strength_workouts,
    compare_timed_workouts,
)
from services.helpers import get_set_stats, get_timed_set_stats
from services.exercise_matching import (
    build_name_index,
    normalize_exercise_name,
    resolve_equivalent_names,
)
from utils.logger import logger


_PERFORMANCE_LABELS: Dict[str, Dict[str, str]] = {
    "gold_strength": {"label": "🥇 Gold Strength", "short": "Gold Strength"},
    "silver_strength": {"label": "🥈 Silver Strength", "short": "Silver Strength"},
    "bronze_strength": {"label": "🥉 Bronze Strength", "short": "Bronze Strength"},
    "gold_load": {"label": "🥇 Gold Load", "short": "Gold Load"},
    "silver_load": {"label": "🥈 Silver Load", "short": "Silver Load"},
    "bronze_load": {"label": "🥉 Bronze Load", "short": "Bronze Load"},
    "consistent": {"label": "→ Consistent", "short": "Consistent"},
    "slightly_off": {"label": "↓ Slightly Off", "short": "Slightly Off"},
    "moderately_off": {"label": "↓ Moderately Off", "short": "Moderately Off"},
    "significantly_off": {"label": "↓ Significantly Off", "short": "Significantly Off"},
    "first_log": {"label": "🆕 First Log", "short": "First Log"},
}


def _performance_payload(key: str, *, summary_mode: bool = False) -> Dict[str, str]:
    normalized = str(key or "consistent").strip().lower()
    if normalized not in _PERFORMANCE_LABELS:
        normalized = "consistent"

    # Session summary intentionally collapses load tie-break tiers into "Consistent".
    display_key = normalized
    if summary_mode and normalized in {"gold_load", "silver_load", "bronze_load"}:
        display_key = "consistent"

    meta = _PERFORMANCE_LABELS.get(display_key, _PERFORMANCE_LABELS["consistent"])
    return {
        "key": display_key,
        "label": meta["label"],
        "short": meta["short"],
    }


def _rank_vectors_for_sets(sets_json: Dict, *, top_n: int = 3, is_timed: bool = False) -> Dict[str, List[float]]:
    if is_timed:
        result = compare_timed_workouts(sets_json or {}, sets_json or {}, top_n=top_n)
    else:
        result = compare_strength_workouts(sets_json or {}, sets_json or {}, top_n=top_n)
    vectors = result.get("current") or {}
    return {
        "scores": [float(v) for v in (vectors.get("scores") or [])],
        "weights": [float(v) for v in (vectors.get("weights") or [])],
    }


def _lex_compare_desc(left: List[float], right: List[float], *, eps: float = 1e-6) -> int:
    limit = max(len(left), len(right))
    for idx in range(limit):
        lv = float(left[idx]) if idx < len(left) else None
        rv = float(right[idx]) if idx < len(right) else None
        if lv is None and rv is None:
            return 0
        if lv is None:
            return -1
        if rv is None:
            return 1
        if abs(lv - rv) <= eps:
            continue
        return 1 if lv > rv else -1
    return 0


def _first_drop_index(best_scores: List[float], current_scores: List[float], *, top_n: int = 3, eps: float = 1e-6) -> Optional[int]:
    for idx in range(max(1, int(top_n))):
        b = float(best_scores[idx]) if idx < len(best_scores) else 0.0
        c = float(current_scores[idx]) if idx < len(current_scores) else 0.0
        if c + eps < b:
            return idx
        if c > b + eps:
            return None
    return None


def _first_score_diff_index(left_scores: List[float], right_scores: List[float], *, top_n: int = 3, eps: float = 1e-6) -> tuple[Optional[int], int]:
    """
    Compare top-N score vectors and return (first_diff_index, cmp).

    cmp semantics:
    - 1: left wins
    - -1: right wins
    - 0: tied
    """
    n = max(1, int(top_n) if isinstance(top_n, int) and top_n > 0 else 3)
    for idx in range(n):
        lv = float(left_scores[idx]) if idx < len(left_scores) else 0.0
        rv = float(right_scores[idx]) if idx < len(right_scores) else 0.0
        if abs(lv - rv) <= eps:
            continue
        return (idx, 1) if lv > rv else (idx, -1)
    return None, 0


def classify_exercise_performance(
    db_session,
    user_id: int,
    exercise_name: str,
    current_sets: Optional[Dict],
    *,
    target_sets: int = 3,
    log_ex_index=None,
    is_timed: bool = False,
    current_log_id: Optional[int] = None,
    summary_mode: bool = False,
) -> Dict[str, Any]:
    """
    Classify today's exercise performance against all historical logs of the exercise.

    Ranking uses top-N performance vectors (e1RM for strength, timed score for timed logs)
    and tie-breaks by top-set loads.
    """
    normalized_current = _normalize_sets(current_sets)
    if not normalized_current:
        return _performance_payload("consistent", summary_mode=summary_mode)

    candidates = resolve_equivalent_names(exercise_name, log_ex_index) if log_ex_index else []
    if not candidates:
        candidates = _exercise_candidates(exercise_name)
    if not candidates:
        return _performance_payload("first_log", summary_mode=summary_mode)

    top_n = max(1, int(target_sets) if isinstance(target_sets, int) and target_sets > 0 else 3)
    logs = (
        db_session.query(WorkoutLog)
        .filter(WorkoutLog.user_id == user_id)
        .filter(WorkoutLog.exercise.in_(candidates))
        .order_by(WorkoutLog.date.asc(), WorkoutLog.id.asc())
        .all()
    )

    rows: List[Dict[str, Any]] = []
    for log in logs:
        log_sets = _normalize_sets(getattr(log, "sets_json", None))
        if not log_sets:
            continue
        vectors = _rank_vectors_for_sets(log_sets, top_n=top_n, is_timed=is_timed)
        if not vectors.get("scores"):
            continue
        rows.append(
            {
                "id": getattr(log, "id", None),
                "scores": vectors["scores"],
                "weights": vectors["weights"],
                "date": getattr(log, "date", datetime.min),
                "is_current": False,
            }
        )

    current_row: Optional[Dict[str, Any]] = None
    if current_log_id is not None:
        for row in rows:
            if row.get("id") == current_log_id:
                row["is_current"] = True
                current_row = row
                break

    if current_row is None:
        vectors = _rank_vectors_for_sets(normalized_current, top_n=top_n, is_timed=is_timed)
        if not vectors.get("scores"):
            return _performance_payload("consistent", summary_mode=summary_mode)
        current_row = {
            "id": current_log_id,
            "scores": vectors["scores"],
            "weights": vectors["weights"],
            "date": datetime.now(),
            "is_current": True,
        }
        rows.append(current_row)

    competitors = [row for row in rows if not row.get("is_current")]
    if not competitors:
        return _performance_payload("first_log", summary_mode=summary_mode)

    tie_group = [
        row
        for row in rows
        if _lex_compare_desc(row.get("scores") or [], current_row.get("scores") or []) == 0
    ]

    if len(tie_group) > 1:
        # If another workout is identical on load vector too, treat as consistent
        # instead of awarding a load medal for a non-unique rank.
        has_exact_load_tie = any(
            row is not current_row
            and _lex_compare_desc(row.get("weights") or [], current_row.get("weights") or []) == 0
            for row in tie_group
        )
        if has_exact_load_tie:
            return _performance_payload("consistent", summary_mode=summary_mode)

        weight_rank = 1 + sum(
            1
            for row in tie_group
            if row is not current_row
            and _lex_compare_desc(row.get("weights") or [], current_row.get("weights") or []) > 0
        )
        if weight_rank <= 3:
            load_key = {1: "gold_load", 2: "silver_load", 3: "bronze_load"}.get(weight_rank, "consistent")
            return _performance_payload(load_key, summary_mode=summary_mode)
        return _performance_payload("consistent", summary_mode=summary_mode)

    best_row = max(
        rows,
        key=lambda row: (
            tuple(row.get("scores") or []),
            tuple(row.get("weights") or []),
            row.get("date") or datetime.min,
        ),
    )

    # Compare against the strongest non-current reference workout.
    # If current is already best overall, compare against the next-best competitor.
    if best_row is current_row:
        reference_row = max(
            competitors,
            key=lambda row: (
                tuple(row.get("scores") or []),
                tuple(row.get("weights") or []),
                row.get("date") or datetime.min,
            ),
        )
    else:
        reference_row = best_row

    diff_idx, cmp_to_reference = _first_score_diff_index(
        current_row.get("scores") or [],
        reference_row.get("scores") or [],
        top_n=top_n,
    )

    if cmp_to_reference > 0:
        if diff_idx == 0:
            return _performance_payload("gold_strength", summary_mode=summary_mode)
        if diff_idx == 1:
            return _performance_payload("silver_strength", summary_mode=summary_mode)
        return _performance_payload("bronze_strength", summary_mode=summary_mode)

    if cmp_to_reference < 0:
        if diff_idx == 0:
            return _performance_payload("significantly_off", summary_mode=summary_mode)
        if diff_idx == 1:
            return _performance_payload("moderately_off", summary_mode=summary_mode)
        return _performance_payload("slightly_off", summary_mode=summary_mode)

    return _performance_payload("consistent", summary_mode=summary_mode)


def _format_sets_display(sets_json):
    """Format sets JSON into display string like '26 x 7, 22.5 x 15'."""
    if not sets_json or not isinstance(sets_json, dict):
        return ""
    
    weights = sets_json.get('weights') or []
    reps = sets_json.get('reps') or []
    
    if not weights and not reps:
        return ""
    
    pairs = []
    for w, r in zip(weights, reps):
        if w is not None and r is not None:
            w_str = f"{w:g}" if isinstance(w, (int, float)) else str(w)
            pairs.append(f"{w_str} x {int(r)}")
    
    return ", ".join(pairs) if pairs else ""


def _format_best_string(record):
    """Format best_string from a record to match the display format."""
    if not record:
        return '-'
    sets_json = getattr(record, 'sets_json', None)
    if sets_json:
        formatted = _format_sets_display(sets_json)
        if formatted:
            return formatted
    best_string = getattr(record, 'best_string', None)
    if best_string:
        return best_string
    exercise_string = getattr(record, 'exercise_string', None)
    if exercise_string:
        return exercise_string
    return '-'


def _exercise_candidates(exercise_name: str) -> List[str]:
    if not exercise_name:
        return []
    name = exercise_name.strip()
    candidates = [
        name,
        name.title(),
        name.replace("'", "’"),
        name.replace("'", "’").title(),
        name.replace("’", "'"),
        name.replace("’", "'").title(),
        name.replace("-", "–"),
        name.replace("–", "-"),
        name.replace("-", " "),
        name.replace("–", " "),
    ]
    return list(dict.fromkeys(candidates))


def _normalize_sets(sets_json: Optional[Dict]) -> Optional[Dict]:
    if not sets_json or not isinstance(sets_json, dict):
        return None
    weights = sets_json.get('weights') or []
    reps = sets_json.get('reps') or []
    try:
        weights = [float(w) for w in weights]
        reps = [int(r) for r in reps]
    except Exception:
        return None
    if not weights and not reps:
        return None
    return {'weights': weights, 'reps': reps}


def _has_time_hint_in_exercise_string(exercise_string: str) -> bool:
    text = str(exercise_string or "")
    if not text:
        return False
    return bool(re.search(r"\[[^\]]*\d+\s*[-–—]\s*\d+\s*s[^\]]*\]", text, flags=re.IGNORECASE))


def _has_time_history(db_session, user_id: int, exercise_name: str, *, log_ex_index=None) -> bool:
    candidates = resolve_equivalent_names(exercise_name, log_ex_index) if log_ex_index else []
    if not candidates:
        candidates = _exercise_candidates(exercise_name)
    if not candidates:
        return False

    logs = (
        db_session.query(WorkoutLog)
        .filter(WorkoutLog.user_id == user_id)
        .filter(WorkoutLog.exercise.in_(candidates))
        .all()
    )
    for log in logs:
        if _has_time_hint_in_exercise_string(getattr(log, 'exercise_string', '')):
            return True
    return False


def _extract_time_seconds(exercise_string: str, expected_sets: int) -> List[int]:
    text = str(exercise_string or "")
    if not text:
        return []

    lines = [ln.strip() for ln in text.splitlines() if ln and ln.strip()]
    if not lines:
        return []

    for line in lines:
        if ',' in line:
            rhs = line.split(',', 1)[1].strip()
            values = extract_numbers(rhs)
            if values:
                return [max(1, int(round(v))) for v in values]

    numeric_lines = [ln for ln in lines if re.search(r'\d', ln)]
    if len(numeric_lines) >= 2:
        values = extract_numbers(numeric_lines[-1])
        if values:
            return [max(1, int(round(v))) for v in values]

    values = extract_numbers(text)
    if expected_sets > 0 and len(values) == expected_sets * 2:
        latter = values[expected_sets:]
        return [max(1, int(round(v))) for v in latter]

    return []


def _parse_rep_target_sets(rep_text: str) -> Dict[str, int]:
    """Parse 'Exercise: 2, 8-15' style lines into lowercased exercise->set_count."""
    out: Dict[str, int] = {}
    if not rep_text:
        return out
    for raw in (rep_text or "").splitlines():
        line = (raw or "").strip()
        if not line or ":" not in line:
            continue
        name, value = line.split(":", 1)
        key = normalize_exercise_name(name or "")
        if not key:
            continue
        value = (value or "").strip()
        m = re.match(r'^(\d+)\s*,', value)
        if not m:
            continue
        try:
            n = int(m.group(1))
        except Exception:
            continue
        if n > 0:
            out[key] = n
    return out


def _get_best_log(
    db_session,
    user_id: int,
    exercise_name: str,
    *,
    target_sets: int = 3,
    log_ex_index=None,
    is_timed: bool = False,
) -> Optional[WorkoutLog]:
    candidates = resolve_equivalent_names(exercise_name, log_ex_index) if log_ex_index else []
    if not candidates:
        candidates = _exercise_candidates(exercise_name)
    if not candidates:
        return None
    logs = (
        db_session.query(WorkoutLog)
        .filter(WorkoutLog.user_id == user_id)
        .filter(WorkoutLog.exercise.in_(candidates))
        .all()
    )
    if not logs:
        return None

    required_sets = int(target_sets) if isinstance(target_sets, int) and target_sets > 0 else 3
    preferred = []
    fallback = []
    for log in logs:
        normalized_sets = _normalize_sets(log.sets_json)
        if not normalized_sets:
            continue
        if is_timed:
            metrics = best_workout_timed_score(normalized_sets, top_n=required_sets)
        else:
            metrics = best_workout_strength_score(normalized_sets, top_n=required_sets)
        score = float(metrics.get("score") or 0.0)
        set_count = int(metrics.get("set_count") or 0)
        if score <= 0:
            continue
        row = (log, score, set_count)
        if set_count >= required_sets:
            preferred.append(row)
        else:
            fallback.append(row)

    pool = preferred if preferred else fallback
    if not pool:
        return None

    best_log, _, _ = max(
        pool,
        key=lambda row: (
            row[1],
            row[2],
            getattr(row[0], "date", datetime.min),
        ),
    )
    return best_log


def _get_lift_record(db_session, user_id: int, exercise_name: str, *, lift_ex_index=None) -> Optional[Lift]:
    candidates = resolve_equivalent_names(exercise_name, lift_ex_index) if lift_ex_index else []
    if not candidates:
        candidates = _exercise_candidates(exercise_name)
    if not candidates:
        return None
    matches = db_session.query(Lift).filter(
        Lift.user_id == user_id,
        Lift.exercise.in_(candidates)
    ).all()
    if not matches:
        return None
    for match in matches:
        if match.best_string and match.best_string.strip():
            return match
    return matches[0]


def handle_workout_log(db_session, user, parsed_data: Dict) -> List[Dict]:
    """
    Process and save a workout log.
    
    Args:
        db_session: Database session
        user: User object
        parsed_data: Parsed workout data dictionary
        
    Returns:
        List of summary dictionaries for each exercise
    """
    summary = []
    workout_date = parsed_data.get('date', datetime.now())
    workout_name = parsed_data.get('workout_name')
    rep_row = db_session.query(RepRange).filter_by(user_id=user.id).first()
    rep_target_sets = _parse_rep_target_sets(rep_row.text_content if rep_row else "")

    # Build indices once per log submission so minimal normalization like hyphen/space
    # and safe word-order swaps can match existing history/Lift rows.
    distinct_logs = (
        db_session.query(WorkoutLog.exercise)
        .filter(WorkoutLog.user_id == user.id)
        .distinct()
        .all()
    )
    log_ex_index = build_name_index([row[0] for row in distinct_logs or []])
    distinct_lifts = (
        db_session.query(Lift.exercise)
        .filter(Lift.user_id == user.id)
        .distinct()
        .all()
    )
    lift_ex_index = build_name_index([row[0] for row in distinct_lifts or []])
    
    if 'exercises' not in parsed_data or not parsed_data['exercises']:
        logger.warning(f"No exercises found in workout data for user {user.username}")
        return summary

    for item in parsed_data["exercises"]:
        ex_name = item['name']
        target_sets = int(rep_target_sets.get(normalize_exercise_name(ex_name or ""), 3) or 3)
        new_sets = {"weights": item["weights"], "reps": item["reps"]}
        new_str = item['exercise_string']
        is_valid = item.get('valid', True)
        time_based = False

        if is_valid:
            has_explicit_hint = _has_time_hint_in_exercise_string(new_str)
            time_based = has_explicit_hint or (
                not has_explicit_hint
                and _has_time_history(db_session, user.id, ex_name, log_ex_index=log_ex_index)
            )
            current_reps = [int(r) for r in (new_sets.get('reps') or []) if r is not None]
            if time_based and current_reps and all(r <= 1 for r in current_reps):
                seconds = _extract_time_seconds(new_str, expected_sets=len(new_sets.get('weights') or []))
                if seconds:
                    weights, reps = align_sets(
                        list(new_sets.get('weights') or []),
                        [int(v) for v in seconds],
                        len(new_sets.get('weights') or []) or None,
                    )
                    new_sets = {'weights': weights, 'reps': reps}
        
        # Format display string from sets data
        formatted_display = _format_sets_display(new_sets) if is_valid else new_str

        # 1. Calculate Stats for Today
        p_peak, p_sum, p_vol = get_set_stats(new_sets)

        # Find the heaviest weight used today (for history)
        daily_max_weight = 0
        daily_max_reps = 0
        if is_valid and new_sets['weights']:
            daily_max_weight = max(new_sets['weights'])
            # Find reps corresponding to that max weight
            idx = new_sets['weights'].index(daily_max_weight)
            daily_max_reps = new_sets['reps'][idx]

        best_log = _get_best_log(
            db_session,
            user.id,
            ex_name,
            target_sets=target_sets,
            log_ex_index=log_ex_index,
            is_timed=time_based,
        )
        best_log_sets = _normalize_sets(best_log.sets_json) if best_log else None

        row = {
            'name': ex_name, 'old': '-', 'new': formatted_display,
            'status': '-', 'class': 'neutral', 'valid': is_valid,
            'is_timed': time_based if is_valid else False,
            'performance_key': None,
            'performance_label': None,
        }

        if is_valid:
            if time_based:
                p_peak, p_sum, p_vol = get_timed_set_stats(new_sets)
            # --- SAVE TO HISTORY ---
            history_log = None
            try:
                history_log = WorkoutLog(
                    user_id=user.id,
                    date=workout_date,
                    workout_name=workout_name,
                    exercise=ex_name,
                    exercise_string=new_str,
                    sets_json=new_sets,
                    bodyweight=user.bodyweight,
                    top_weight=daily_max_weight if daily_max_weight > 0 else None,
                    top_reps=daily_max_reps if daily_max_reps > 0 else None,
                    estimated_1rm=p_peak if p_peak > 0 else None
                )
                db_session.add(history_log)
                db_session.flush()
            except Exception as e:
                logger.error(f"Error creating workout log for {ex_name}: {e}", exc_info=True)
                # Continue processing other exercises even if one fails

            perf = classify_exercise_performance(
                db_session,
                user.id,
                ex_name,
                new_sets,
                target_sets=3,
                log_ex_index=log_ex_index,
                is_timed=time_based,
                current_log_id=(getattr(history_log, 'id', None) if history_log else None),
                summary_mode=True,
            )
            row['performance_key'] = perf.get('key')
            row['performance_label'] = perf.get('label')

            improvement = None
            is_new_best = False

            if best_log_sets:
                row['old'] = _format_best_string(best_log)
                if time_based:
                    comparison = compare_timed_workouts(best_log_sets, new_sets, top_n=target_sets)
                else:
                    comparison = compare_strength_workouts(best_log_sets, new_sets, top_n=target_sets)

                # Peak-first, then lexicographic set comparison, then lexicographic weight comparison.
                if comparison.get("cmp", 0) > 0:
                    is_new_best = True
                    if comparison.get("reason") == "peak":
                        diff = float(comparison.get("diff") or 0.0)
                        improvement = f"PEAK (+{diff:.1f})"
                    else:
                        improvement = "CONSISTENCY"
            else:
                row['old'] = 'First Log'
                row['status'] = "NEW"
                row['class'] = 'new'
                is_new_best = True

            if improvement:
                row['status'] = improvement
                row['class'] = 'improved'

            lift_record = _get_lift_record(db_session, user.id, ex_name, lift_ex_index=lift_ex_index)
            if is_new_best:
                lift_sets_json = new_sets
                lift_best_string = new_str
                lift_updated_at = workout_date
            else:
                lift_sets_json = best_log.sets_json if best_log else new_sets
                lift_best_string = (
                    best_log.exercise_string
                    if best_log and best_log.exercise_string
                    else _format_sets_display(lift_sets_json)
                )
                lift_updated_at = best_log.date if best_log else workout_date

            if lift_record:
                lift_record.sets_json = lift_sets_json
                lift_record.best_string = lift_best_string
                lift_record.updated_at = lift_updated_at
            else:
                new_rec = Lift(
                    user_id=user.id,
                    exercise=ex_name,
                    best_string=lift_best_string,
                    sets_json=lift_sets_json,
                    updated_at=lift_updated_at,
                )
                db_session.add(new_rec)
        else:
            row['status'] = "ERROR"

        summary.append(row)

    return summary


def _get_best_log_before_date(
    db_session,
    user_id: int,
    exercise_name: str,
    *,
    target_sets: int = 3,
    log_ex_index=None,
    is_timed: bool = False,
    workout_day_start_dt: Optional[datetime] = None,
) -> Optional[WorkoutLog]:
    """
    Like `_get_best_log`, but only considers logs strictly before `workout_day_start_dt`.
    Used to recreate "previous best" for the session summary page.
    """
    if workout_day_start_dt is None:
        return None

    candidates = resolve_equivalent_names(exercise_name, log_ex_index) if log_ex_index else []
    if not candidates:
        candidates = _exercise_candidates(exercise_name)
    if not candidates:
        return None

    required_sets = int(target_sets) if isinstance(target_sets, int) and target_sets > 0 else 3

    logs = (
        db_session.query(WorkoutLog)
        .filter(WorkoutLog.user_id == user_id)
        .filter(WorkoutLog.exercise.in_(candidates))
        .filter(WorkoutLog.date < workout_day_start_dt)
        .all()
    )
    if not logs:
        return None

    preferred = []
    fallback = []

    for log in logs:
        normalized_sets = _normalize_sets(log.sets_json)
        if not normalized_sets:
            continue

        if is_timed:
            metrics = best_workout_timed_score(normalized_sets, top_n=required_sets)
        else:
            metrics = best_workout_strength_score(normalized_sets, top_n=required_sets)

        score = float(metrics.get("score") or 0.0)
        set_count = int(metrics.get("set_count") or 0)
        if score <= 0:
            continue

        row = (log, score, set_count)
        if set_count >= required_sets:
            preferred.append(row)
        else:
            fallback.append(row)

    pool = preferred if preferred else fallback
    if not pool:
        return None

    best_log, _, _ = max(
        pool,
        key=lambda row: (
            row[1],
            row[2],
            getattr(row[0], "date", datetime.min),
        ),
    )
    return best_log


def compute_workout_summary_for_date(db_session, user, workout_date: date | datetime) -> tuple[list[Dict], int, int]:
    """
    Build the `summary` rows that `templates/result.html` expects, for an already-logged day.
    This recreates the "previous best vs today's performance" session summary without mutating history.
    """
    workout_day = workout_date.date() if isinstance(workout_date, datetime) else workout_date
    workout_day_start_dt = datetime.combine(workout_day, datetime.min.time())
    workout_day_end_dt = workout_day_start_dt + timedelta(days=1)

    logs = (
        db_session.query(WorkoutLog)
        .filter(WorkoutLog.user_id == user.id)
        .filter(WorkoutLog.date >= workout_day_start_dt)
        .filter(WorkoutLog.date < workout_day_end_dt)
        .order_by(WorkoutLog.id)
        .all()
    )
    if not logs:
        return [], 0, 0

    rep_row = db_session.query(RepRange).filter_by(user_id=user.id).first()
    rep_target_sets = _parse_rep_target_sets(rep_row.text_content if rep_row else "")

    distinct_exercises: list[str] = []
    seen: set[str] = set()
    unique_logs: list[WorkoutLog] = []
    for log in logs:
        if log.exercise in seen:
            continue
        seen.add(log.exercise)
        unique_logs.append(log)
        distinct_exercises.append(log.exercise)

    # Use the full user's distinct exercise list for conservative aliasing,
    # matching the behavior of `handle_workout_log`.
    distinct_logs = (
        db_session.query(WorkoutLog.exercise)
        .filter(WorkoutLog.user_id == user.id)
        .distinct()
        .all()
    )
    log_ex_index = build_name_index([row[0] for row in distinct_logs or []])

    summary: list[Dict] = []
    exercise_count = len(unique_logs)
    set_count = 0

    for log in unique_logs:
        ex_name = log.exercise
        target_sets = int(rep_target_sets.get(normalize_exercise_name(ex_name or ""), 3) or 3)

        new_sets = _normalize_sets(log.sets_json)
        valid = bool(new_sets)
        has_explicit_hint = _has_time_hint_in_exercise_string(getattr(log, "exercise_string", "") or "")
        time_based = has_explicit_hint or (
            (not has_explicit_hint) and _has_time_history(db_session, user.id, ex_name, log_ex_index=log_ex_index)
        )

        formatted_display = _format_sets_display(new_sets) if valid else (getattr(log, "exercise_string", "") or "")

        row = {
            "name": ex_name,
            "old": "-",
            "new": formatted_display,
            "status": "-",
            "class": "neutral",
            "valid": valid,
            "is_timed": time_based if valid else False,
            "performance_key": None,
            "performance_label": None,
        }

        if valid:
            perf = classify_exercise_performance(
                db_session,
                user.id,
                ex_name,
                new_sets,
                target_sets=3,
                log_ex_index=log_ex_index,
                is_timed=time_based,
                current_log_id=getattr(log, "id", None),
                summary_mode=True,
            )
            row["performance_key"] = perf.get("key")
            row["performance_label"] = perf.get("label")

            if time_based:
                p_peak, p_sum, p_vol = get_timed_set_stats(new_sets)
            else:
                p_peak, p_sum, p_vol = get_set_stats(new_sets)

            best_log = _get_best_log_before_date(
                db_session,
                user.id,
                ex_name,
                target_sets=target_sets,
                log_ex_index=log_ex_index,
                is_timed=time_based,
                workout_day_start_dt=workout_day_start_dt,
            )
            best_log_sets = _normalize_sets(best_log.sets_json) if best_log else None

            if best_log_sets:
                row["old"] = _format_best_string(best_log)

                if time_based:
                    comparison = compare_timed_workouts(best_log_sets, new_sets, top_n=target_sets)
                else:
                    comparison = compare_strength_workouts(best_log_sets, new_sets, top_n=target_sets)

                if comparison.get("cmp", 0) > 0:
                    if comparison.get("reason") == "peak":
                        diff = float(comparison.get("diff") or 0.0)
                        row["status"] = f"PEAK (+{diff:.1f})"
                    else:
                        row["status"] = "CONSISTENCY"
                    row["class"] = "improved"
            else:
                row["old"] = "First Log"
                row["status"] = "NEW"
                row["class"] = "new"

            if isinstance(log.sets_json, dict):
                weights = log.sets_json.get("weights") or []
                reps = log.sets_json.get("reps") or []
                try:
                    set_count += max(len(weights), len(reps))
                except Exception:
                    pass

        summary.append(row)

    return summary, exercise_count, set_count
