"""
Statistics and data export services for workout tracking.
"""
import csv
import io
import json
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from sqlalchemy import func, desc
from list_of_exercise import BW_EXERCISES
from models import WorkoutLog
from services.helpers import get_set_stats


def _serialize_sets_json(sets_json) -> str:
    if not sets_json:
        return ""
    if isinstance(sets_json, str):
        return sets_json
    try:
        return json.dumps(sets_json, ensure_ascii=False)
    except Exception:
        return str(sets_json)


def _format_list(values) -> str:
    if not values:
        return ""
    return ",".join(str(v) for v in values if v is not None)


def backfill_log_bodyweight(db_session, user) -> int:
    if not getattr(user, 'bodyweight', None):
        return 0
    return (
        db_session.query(WorkoutLog)
        .filter(
            WorkoutLog.user_id == user.id,
            WorkoutLog.bodyweight.is_(None),
        )
        .update({WorkoutLog.bodyweight: user.bodyweight}, synchronize_session=False)
    )


def _normalize_sets(sets_json) -> List[List[float]]:
    if not sets_json or not isinstance(sets_json, dict):
        return [], []
    weights = sets_json.get('weights') or []
    reps = sets_json.get('reps') or []
    pairs = []
    for w, r in zip(weights, reps):
        if w is None or r is None:
            continue
        try:
            pairs.append((float(w), int(r)))
        except (TypeError, ValueError):
            continue
    if not pairs:
        return [], []
    return [p[0] for p in pairs], [p[1] for p in pairs]


def _log_uses_bw(log) -> bool:
    if log.exercise in BW_EXERCISES:
        return True
    exercise_string = (log.exercise_string or '').lower()
    return 'bw' in exercise_string


def _apply_bodyweight(weights, reps, bodyweight):
    if bodyweight is None or not reps:
        return weights, reps

    def is_placeholder(weight):
        try:
            return weight is None or float(weight) <= 1
        except (TypeError, ValueError):
            return True

    if not weights:
        return [float(bodyweight)] * len(reps), reps

    if all(is_placeholder(w) for w in weights):
        return [float(bodyweight)] * len(weights), reps

    return weights, reps


def _get_top_weight(weights, reps):
    if not weights or not reps:
        return 0, 0
    top_weight = max(weights)
    try:
        idx = weights.index(top_weight)
        top_reps = reps[idx] if idx < len(reps) else reps[0]
    except Exception:
        top_reps = reps[0] if reps else 0
    return top_weight, top_reps


def _get_log_metrics(log):
    weights, reps = _normalize_sets(log.sets_json)

    if (not weights or not reps) and log.top_weight is not None and log.top_reps is not None:
        weights = [float(log.top_weight)]
        reps = [int(log.top_reps)]

    if _log_uses_bw(log):
        weights, reps = _apply_bodyweight(weights, reps, log.bodyweight)

    if weights and reps:
        peak, _, _ = get_set_stats({'weights': weights, 'reps': reps})
        top_weight, top_reps = _get_top_weight(weights, reps)
        if not peak and log.estimated_1rm:
            peak = float(log.estimated_1rm)
        return peak, top_weight, top_reps

    return (
        float(log.estimated_1rm) if log.estimated_1rm else 0,
        float(log.top_weight) if log.top_weight else 0,
        int(log.top_reps) if log.top_reps else 0,
    )


def get_csv_export(db_session, user):
    """Generates a CSV string of all workout history."""
    logs = db_session.query(WorkoutLog).filter_by(
        user_id=user.id
    ).order_by(desc(WorkoutLog.date)).all()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        'Date',
        'Workout Name',
        'Exercise',
        'Exercise String',
        'Weights',
        'Reps',
        'Sets JSON',
        'Top Weight (kg)',
        'Top Reps',
        'Estimated 1RM (kg)',
    ])

    for log in logs:
        sets_json = log.sets_json if isinstance(log.sets_json, dict) else {}
        weights = sets_json.get('weights') if isinstance(sets_json, dict) else None
        reps = sets_json.get('reps') if isinstance(sets_json, dict) else None
        writer.writerow([
            log.date.isoformat() if log.date else "",
            log.workout_name or "",
            log.exercise or "",
            log.exercise_string or "",
            _format_list(weights),
            _format_list(reps),
            _serialize_sets_json(log.sets_json),
            log.top_weight if log.top_weight is not None else "",
            log.top_reps if log.top_reps is not None else "",
            f"{log.estimated_1rm:.2f}" if log.estimated_1rm is not None else "",
        ])

    return output.getvalue()


def get_json_export(db_session, user) -> Dict:
    """Generate full JSON export payload for workout history."""
    logs = db_session.query(WorkoutLog).filter_by(
        user_id=user.id
    ).order_by(desc(WorkoutLog.date)).all()

    workouts_by_date: Dict[str, Dict] = {}
    for log in logs:
        date_key = log.date.strftime('%Y-%m-%d') if log.date else ""
        entry = {
            'id': log.id,
            'date': log.date.isoformat() if log.date else None,
            'workout_name': log.workout_name,
            'exercise': log.exercise,
            'exercise_string': log.exercise_string,
            'sets_json': log.sets_json,
            'top_weight': log.top_weight,
            'top_reps': log.top_reps,
            'estimated_1rm': log.estimated_1rm,
        }
        workouts_by_date.setdefault(date_key, {'date': date_key, 'entries': []})['entries'].append(entry)

    return {
        'user': user.username,
        'export_date': datetime.now().isoformat(),
        'workouts': list(workouts_by_date.values()),
    }


def get_chart_data(db_session, user, exercise_name):
    """Fetches date vs 1RM data for a specific exercise with additional metrics."""
    logs = db_session.query(WorkoutLog).filter(
        WorkoutLog.user_id == user.id,
        WorkoutLog.exercise == exercise_name
    ).order_by(WorkoutLog.date).all()

    labels = []  # Dates
    data_1rm = []  # 1RM values
    data_weight = []  # Top weight values
    data_reps = []  # Reps values

    for log in logs:
        labels.append(log.date.strftime("%d/%m/%y"))
        one_rm, top_weight, top_reps = _get_log_metrics(log)
        data_1rm.append(one_rm)
        data_weight.append(top_weight)
        data_reps.append(top_reps)

    # Calculate statistics
    stats = {}
    if data_1rm:
        stats = {
            'current_1rm': data_1rm[-1] if data_1rm else 0,
            'max_1rm': max(data_1rm) if data_1rm else 0,
            'min_1rm': min(data_1rm) if data_1rm else 0,
            'improvement': data_1rm[-1] - data_1rm[0] if len(data_1rm) > 1 else 0,
            'improvement_pct': ((data_1rm[-1] - data_1rm[0]) / data_1rm[0] * 100) if len(data_1rm) > 1 and data_1rm[0] > 0 else 0
        }

    return {
        "labels": labels,
        "data": data_1rm,
        "weight": data_weight,
        "reps": data_reps,
        "exercise": exercise_name,
        "stats": stats
    }


def get_exercise_summary(db_session, user, exercise_name: str) -> Dict:
    """Get summary statistics for a specific exercise."""
    logs = db_session.query(WorkoutLog).filter(
        WorkoutLog.user_id == user.id,
        WorkoutLog.exercise == exercise_name
    ).order_by(WorkoutLog.date).all()

    if not logs:
        return {}

    metrics = []
    for log in logs:
        one_rm, top_weight, top_reps = _get_log_metrics(log)
        metrics.append((log, one_rm, top_weight, top_reps))

    first_log, first_1rm, _, _ = metrics[0]
    latest_log, latest_1rm, _, _ = metrics[-1]

    # Calculate PRs
    max_1rm_log, max_1rm_value, _, _ = max(metrics, key=lambda x: x[1])
    max_weight_log, _, max_weight_value, max_weight_reps = max(metrics, key=lambda x: x[2])

    return {
        'total_sessions': len(logs),
        'first_date': first_log.date,
        'latest_date': latest_log.date,
        'current_1rm': latest_1rm or 0,
        'pr_1rm': max_1rm_value or 0,
        'pr_weight': max_weight_value or 0,
        'pr_reps': max_weight_reps or 0,
        'pr_date': max_1rm_log.date,
        'improvement': (latest_1rm or 0) - (first_1rm or 0),
        'improvement_pct': ((latest_1rm or 0) - (first_1rm or 0)) / (first_1rm or 1) * 100 if first_1rm else 0
    }


def get_all_exercises_summary(db_session, user) -> List[Dict]:
    """Get summary for all exercises."""
    exercises = db_session.query(WorkoutLog.exercise).filter_by(
        user_id=user.id
    ).distinct().all()
    
    summaries = []
    for ex in exercises:
        summary = get_exercise_summary(db_session, user, ex[0])
        summary['exercise'] = ex[0]
        summaries.append(summary)
    
    # Sort by latest date
    summaries.sort(key=lambda x: x.get('latest_date', datetime.min), reverse=True)
    return summaries