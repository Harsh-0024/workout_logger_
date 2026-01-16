"""
Statistics and data export services for workout tracking.
"""
import csv
import io
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from sqlalchemy import func, desc
from models import WorkoutLog


def get_csv_export(db_session, user):
    """Generates a CSV string of all workout history."""
    logs = db_session.query(WorkoutLog).filter_by(
        user_id=user.id
    ).order_by(desc(WorkoutLog.date)).all()

    output = io.StringIO()
    writer = csv.writer(output)

    # Headers
    writer.writerow(['Date', 'Exercise', 'Top Weight (kg)', 'Reps', 'Est 1RM (kg)'])

    for log in logs:
        writer.writerow([
            log.date.strftime("%Y-%m-%d"),
            log.exercise,
            log.top_weight or 0,
            log.top_reps or 0,
            f"{log.estimated_1rm:.1f}" if log.estimated_1rm else "0.0"
        ])

    return output.getvalue()


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
        data_1rm.append(float(log.estimated_1rm) if log.estimated_1rm else 0)
        data_weight.append(float(log.top_weight) if log.top_weight else 0)
        data_reps.append(int(log.top_reps) if log.top_reps else 0)

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

    first_log = logs[0]
    latest_log = logs[-1]
    
    # Calculate PRs
    max_1rm_log = max(logs, key=lambda x: x.estimated_1rm or 0)
    max_weight_log = max(logs, key=lambda x: x.top_weight or 0)

    return {
        'total_sessions': len(logs),
        'first_date': first_log.date,
        'latest_date': latest_log.date,
        'current_1rm': latest_log.estimated_1rm or 0,
        'pr_1rm': max_1rm_log.estimated_1rm or 0,
        'pr_weight': max_weight_log.top_weight or 0,
        'pr_reps': max_weight_log.top_reps or 0,
        'pr_date': max_1rm_log.date,
        'improvement': (latest_log.estimated_1rm or 0) - (first_log.estimated_1rm or 0),
        'improvement_pct': ((latest_log.estimated_1rm or 0) - (first_log.estimated_1rm or 0)) / (first_log.estimated_1rm or 1) * 100 if first_log.estimated_1rm else 0
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