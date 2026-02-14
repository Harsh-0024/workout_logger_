"""
Workout logging service for processing and saving workout data.
"""
from typing import List, Dict, Optional
from datetime import datetime
import re

from models import Lift, RepRange, WorkoutLog
from services.best_scoring import best_workout_strength_score
from services.helpers import get_set_stats
from services.exercise_matching import (
    build_name_index,
    normalize_exercise_name,
    resolve_equivalent_names,
)
from utils.logger import logger


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
        )
        best_log_sets = _normalize_sets(best_log.sets_json) if best_log else None

        row = {
            'name': ex_name, 'old': '-', 'new': formatted_display,
            'status': '-', 'class': 'neutral', 'valid': is_valid
        }

        if is_valid:
            # --- SAVE TO HISTORY ---
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
            except Exception as e:
                logger.error(f"Error creating workout log for {ex_name}: {e}", exc_info=True)
                # Continue processing other exercises even if one fails

            improvement = None
            is_new_best = False

            if best_log_sets:
                row['old'] = _format_best_string(best_log)
                r_peak, r_sum, r_vol = get_set_stats(best_log_sets)
                r_score = float(best_workout_strength_score(best_log_sets, top_n=target_sets).get("score") or 0.0)
                p_score = float(best_workout_strength_score(new_sets, top_n=target_sets).get("score") or 0.0)

                # Strength-first best update:
                # - Prevents 1-set days from replacing a better multi-set best unless the improvement is real.
                if p_score > r_score:
                    is_new_best = True
                    if p_peak > r_peak:
                        diff = p_peak - r_peak
                        improvement = f"PEAK (+{diff:.1f})"
                    elif p_sum > r_sum:
                        improvement = "VOLUME"
                    elif p_vol > r_vol:
                        improvement = "CONSISTENCY"
                    else:
                        improvement = "PEAK (CONSISTENCY)"
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
