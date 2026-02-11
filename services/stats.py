"""
Statistics and data export services for workout tracking.
"""
import csv
import io
import json
import math
import re
from statistics import median
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from sqlalchemy import func, desc
from list_of_exercise import BW_EXERCISES
from models import WorkoutLog, RepRange
from services.helpers import get_set_stats
from services.workout_quality import WorkoutQualityScorer
from utils.dates import local_date


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


def _build_rep_range_map(rep_text: str) -> Dict[str, Tuple[int, int]]:
    mapping: Dict[str, Tuple[int, int]] = {}
    for line in (rep_text or "").splitlines():
        if ':' not in line:
            continue
        k, v = line.split(':', 1)
        key = (k or '').strip().lower()
        value = (v or '').strip()
        if not key or not value:
            continue
        if ',' in value:
            value = value.split(',', 1)[1].strip()
        nums = re.findall(r'\d+', value)
        if len(nums) >= 2:
            mapping[key] = (int(nums[0]), int(nums[1]))
        elif len(nums) == 1:
            n = int(nums[0])
            mapping[key] = (n, n)
    return mapping


def _get_target_rep_range(db_session, user, exercise_name: str) -> Optional[Tuple[int, int]]:
    rep_row = db_session.query(RepRange).filter_by(user_id=user.id).first()
    rep_text = rep_row.text_content if rep_row else ""
    rep_map = _build_rep_range_map(rep_text)
    return rep_map.get((exercise_name or '').strip().lower())


def _normalize_sets_for_log(log):
    weights, reps = _normalize_sets(log.sets_json)

    if _log_uses_bw(log):
        weights, reps = _apply_bodyweight(weights, reps, log.bodyweight)

    if weights and reps:
        return {"weights": weights, "reps": reps}
    return {}


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


def _normalize_exercise_name(exercise: str) -> str:
    value = str(exercise or '').strip().lower()
    value = re.sub(r'^\s*\d+\s*[\.)\-:]\s*', '', value)
    value = re.sub(r'[-–—]+', ' ', value)
    value = re.sub(r'[^a-z0-9\s]+', ' ', value)
    value = re.sub(r'\s+', ' ', value).strip()
    return value


def _resolve_exercise_aliases(db_session, user, exercise_name: str) -> List[str]:
    key = _normalize_exercise_name(exercise_name)
    if not key:
        return []
    exercises = (
        db_session.query(WorkoutLog.exercise)
        .filter(WorkoutLog.user_id == user.id)
        .distinct()
        .all()
    )
    aliases = [ex[0] for ex in exercises if _normalize_exercise_name(ex[0]) == key]
    return aliases or [exercise_name]


def _get_peak_1rm_for_log(log) -> float:
    weights, reps = _normalize_sets(log.sets_json)

    if (not weights or not reps) and log.top_weight is not None and log.top_reps is not None:
        weights = [float(log.top_weight)]
        reps = [int(log.top_reps)]

    if _log_uses_bw(log):
        weights, reps = _apply_bodyweight(weights, reps, log.bodyweight)

    if not weights or not reps:
        return 0.0

    quality = WorkoutQualityScorer.calculate_workout_score({'weights': weights, 'reps': reps}, None)
    return float(quality.get('peak_1rm') or 0.0)


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
    aliases = _resolve_exercise_aliases(db_session, user, exercise_name)
    if not aliases:
        logs = []
    else:
        logs = db_session.query(WorkoutLog).filter(
            WorkoutLog.user_id == user.id,
            WorkoutLog.exercise.in_(aliases)
        ).order_by(WorkoutLog.date).all()

    labels = []
    data_1rm = []
    data_weight = []
    data_reps = []
    data_volume = []
    data_effective_volume = []
    data_quality = []
    data_quality_adjusted_1rm = []

    target_rep_range = _get_target_rep_range(db_session, user, exercise_name)

    for log in logs:
        labels.append(local_date(log.date).isoformat() if log.date else "")

        one_rm, top_weight, top_reps = _get_log_metrics(log)
        sets_for_quality = _normalize_sets_for_log(log)
        quality = WorkoutQualityScorer.calculate_workout_score(sets_for_quality, target_rep_range)

        e1rm = quality.get('peak_1rm') or one_rm
        total_vol = quality.get('total_volume')
        if total_vol is None:
            total_vol = 0
        eff_vol = quality.get('effective_volume')
        if eff_vol is None:
            eff_vol = 0
        q_index = quality.get('quality_index')
        if q_index is None:
            q_index = 0

        data_1rm.append(float(e1rm or 0))
        data_weight.append(float(top_weight or 0))
        data_reps.append(int(top_reps or 0))
        data_volume.append(float(total_vol or 0))
        data_effective_volume.append(float(eff_vol or 0))
        data_quality.append(float(q_index) * 100.0)
        data_quality_adjusted_1rm.append(float(e1rm or 0) * float(q_index or 0))

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
        "volume": data_volume,
        "effective_volume": data_effective_volume,
        "quality": data_quality,
        "quality_adjusted_1rm": data_quality_adjusted_1rm,
        "series": {
            "e1rm": data_1rm,
            "top_weight": data_weight,
            "top_reps": data_reps,
            "tonnage": data_volume,
            "effective_tonnage": data_effective_volume,
            "quality": data_quality,
            "quality_adjusted_1rm": data_quality_adjusted_1rm,
        },
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


def get_average_growth_data(db_session, user) -> Dict:
    """Aggregate average percent change per exercise across dates (normalized)."""
    logs = db_session.query(WorkoutLog).filter(
        WorkoutLog.user_id == user.id,
    ).order_by(WorkoutLog.date).all()

    if not logs:
        return {
            "labels": [],
            "data": [],
            "weight": [],
            "reps": [],
            "exercise": "Overall Progress",
            "stats": {},
            "unit": "percent",
        }

    logs_by_exercise = {}
    for log in logs:
        key = _normalize_exercise_name(log.exercise)
        if not key:
            continue
        logs_by_exercise.setdefault(key, []).append(log)

    by_date = {}
    for exercise_logs in logs_by_exercise.values():
        exercise_logs.sort(key=lambda l: l.date or datetime.min)
        base = None
        per_day_values = {}

        for log in exercise_logs:
            if not log.date:
                continue
            one_rm = _get_peak_1rm_for_log(log)
            if not one_rm or one_rm <= 0:
                continue
            if base is None:
                base = one_rm
            if not base:
                continue
            pct_change = ((one_rm - base) / base) * 100.0
            pct_change = max(-300.0, min(300.0, pct_change))
            per_day_values.setdefault(local_date(log.date), []).append(pct_change)

        if not base:
            continue

        for date_key, values in per_day_values.items():
            if not values:
                continue
            day_avg = sum(values) / len(values)
            entry = by_date.setdefault(date_key, {'weighted_sum': 0.0, 'weight_sum': 0.0})
            entry['weighted_sum'] += day_avg * base
            entry['weight_sum'] += base

    if not by_date:
        return {
            "labels": [],
            "data": [],
            "weight": [],
            "reps": [],
            "exercise": "Overall Progress",
            "stats": {},
            "unit": "percent",
        }

    labels = []
    data_pct = []

    for date_key in sorted(by_date.keys()):
        values = by_date[date_key]
        labels.append(date_key.isoformat())
        weight_sum = values.get('weight_sum', 0.0)
        if weight_sum:
            data_pct.append(values.get('weighted_sum', 0.0) / weight_sum)
        else:
            data_pct.append(0)

    stats = {}
    if any(value != 0 for value in data_pct):
        avg_pct = sum(data_pct) / len(data_pct) if data_pct else 0
        stats = {
            'current_1rm': data_pct[-1] if data_pct else 0,
            'max_1rm': max(data_pct) if data_pct else 0,
            'min_1rm': min(data_pct) if data_pct else 0,
            'improvement': data_pct[-1] - data_pct[0] if len(data_pct) > 1 else 0,
            'improvement_pct': avg_pct,
        }

    return {
        "labels": labels,
        "data": data_pct,
        "weight": [],
        "reps": [],
        "exercise": "Overall Progress",
        "stats": stats,
        "unit": "percent",
    }


def _date_range(start, end):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _fade_multiplier(days_since_last: int, fade_start_days: int, fade_end_days: int) -> float:
    if days_since_last <= fade_start_days:
        return 1.0
    if days_since_last >= fade_end_days:
        return 0.0
    span = float(fade_end_days - fade_start_days)
    return max(0.0, min(1.0, (fade_end_days - float(days_since_last)) / span))


def get_overall_progress_data(
    db_session,
    user,
    mode: str = 'index',
    baseline_days_target: int = 24,
    min_sessions: int = 3,
    fade_start_days: int = 60,
    fade_end_days: int = 90,
) -> Dict:
    logs = (
        db_session.query(WorkoutLog)
        .filter(WorkoutLog.user_id == user.id)
        .order_by(WorkoutLog.date)
        .all()
    )

    if not logs:
        return {
            'labels': [],
            'data': [],
            'log_data': [],
            'weight': [],
            'reps': [],
            'exercise': 'Overall',
            'stats': {},
            'unit': 'percent',
            'overall_mode': mode,
        }

    exercise_day_values: Dict[str, Dict] = {}
    workout_days = set()

    for log in logs:
        if not log.date:
            continue
        key = _normalize_exercise_name(log.exercise)
        if not key:
            continue
        day = local_date(log.date)
        workout_days.add(day)
        value = _get_peak_1rm_for_log(log)
        if not value or value <= 0:
            continue
        per_day = exercise_day_values.setdefault(key, {})
        per_day[day] = max(float(per_day.get(day, 0.0)), float(value))

    if not exercise_day_values or not workout_days:
        return {
            'labels': [],
            'data': [],
            'log_data': [],
            'weight': [],
            'reps': [],
            'exercise': 'Overall',
            'stats': {},
            'unit': 'percent',
            'overall_mode': mode,
        }

    workout_days_sorted = sorted(workout_days)
    start_day = workout_days_sorted[0]
    end_day = workout_days_sorted[-1]
    baseline_days_actual = min(int(baseline_days_target), len(workout_days_sorted))
    baseline_end_day = workout_days_sorted[baseline_days_actual - 1]

    baseline_by_exercise: Dict[str, float] = {}
    sessions_by_exercise: Dict[str, int] = {}

    for key, day_map in exercise_day_values.items():
        session_items = sorted(day_map.items())
        sessions_by_exercise[key] = len(session_items)
        samples = [v for d, v in session_items if d <= baseline_end_day and v and v > 0]
        if not samples:
            continue
        if len(samples) >= 3:
            base = float(median(samples))
        else:
            base = float(sum(samples) / len(samples))
        if base > 0:
            baseline_by_exercise[key] = base

    universe = [
        key
        for key in baseline_by_exercise.keys()
        if sessions_by_exercise.get(key, 0) >= int(min_sessions)
    ]

    if not universe:
        return {
            'labels': [],
            'data': [],
            'log_data': [],
            'weight': [],
            'reps': [],
            'exercise': 'Overall',
            'stats': {},
            'unit': 'percent',
            'overall_mode': mode,
        }

    timelines = {}
    for key in universe:
        items = sorted(exercise_day_values.get(key, {}).items())
        timelines[key] = {
            'days': [d for d, _ in items],
            'values': [float(v) for _, v in items],
        }

    labels = []
    series = []
    log_series = []
    pointers = {key: -1 for key in universe}
    last_values = {key: None for key in universe}
    last_session_days = {key: None for key in universe}
    rate_segment = {key: 0 for key in universe}

    for day in _date_range(start_day, end_day):
        labels.append(day.isoformat())
        weighted_sum = 0.0
        weight_sum = 0.0
        daily_log_sum = 0.0
        daily_log_weight_sum = 0.0

        for key in universe:
            timeline = timelines[key]
            days = timeline['days']
            values = timeline['values']

            idx = pointers[key]
            while idx + 1 < len(days) and days[idx + 1] <= day:
                idx += 1
                last_values[key] = values[idx]
                last_session_days[key] = days[idx]
            pointers[key] = idx

            last_val = last_values.get(key)
            last_session_day = last_session_days.get(key)
            if last_val is None or not last_session_day:
                continue

            days_since = (day - last_session_day).days
            fade = _fade_multiplier(days_since, int(fade_start_days), int(fade_end_days))
            if fade <= 0:
                continue

            base = baseline_by_exercise.get(key) or 0.0
            if base <= 0:
                continue

            weight = base * fade

            if mode == 'rate':
                seg = rate_segment[key]
                while seg + 1 < len(days) and day > days[seg + 1]:
                    seg += 1
                rate_segment[key] = seg

                daily_L = 0.0
                if seg + 1 < len(days):
                    d1 = days[seg]
                    d2 = days[seg + 1]
                    v1 = values[seg]
                    v2 = values[seg + 1]
                    day_span = (d2 - d1).days
                    if day_span > 0 and v1 > 0 and v2 > 0 and day > d1 and day <= d2:
                        ratio = v2 / v1
                        if ratio > 0:
                            daily_L = math.log(ratio) / float(day_span)

                daily_log_sum += daily_L * weight
                daily_log_weight_sum += weight
            else:
                index_value = (last_val / base) * 100.0
                weighted_sum += index_value * weight
                weight_sum += weight

        if mode == 'rate':
            overall_L = (daily_log_sum / daily_log_weight_sum) if daily_log_weight_sum else 0.0
            log_series.append(overall_L)
            series.append((math.exp(overall_L) - 1.0) * 100.0)
        else:
            log_series.append(0.0)
            series.append((weighted_sum / weight_sum) if weight_sum else 0.0)

    stats = {}
    if any(value != 0 for value in series):
        cleaned = [float(v) for v in series]
        avg_value = sum(cleaned) / len(cleaned) if cleaned else 0
        stats = {
            'current_1rm': cleaned[-1] if cleaned else 0,
            'max_1rm': max(cleaned) if cleaned else 0,
            'min_1rm': min(cleaned) if cleaned else 0,
            'improvement': cleaned[-1] - cleaned[0] if len(cleaned) > 1 else 0,
            'improvement_pct': avg_value,
        }

    title = 'Strength Index (vs baseline)' if mode != 'rate' else 'Improvement Rate (per day)'

    return {
        'labels': labels,
        'data': series,
        'log_data': log_series,
        'weight': [],
        'reps': [],
        'exercise': title,
        'stats': stats,
        'unit': 'percent',
        'overall_mode': mode,
        'baseline_days': baseline_days_actual,
        'min_sessions': int(min_sessions),
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