import csv
import io
from models import WorkoutLog


def get_csv_export(db_session, user):
    """Generates a CSV string of all workout history."""
    logs = db_session.query(WorkoutLog).filter_by(user_id=user.id).order_by(WorkoutLog.date.desc()).all()

    output = io.StringIO()
    writer = csv.writer(output)

    # Headers
    writer.writerow(['Date', 'Exercise', 'Top Weight', 'Reps', 'Est 1RM'])

    for log in logs:
        writer.writerow([
            log.date.strftime("%Y-%m-%d"),
            log.exercise,
            log.top_weight,
            log.top_reps,
            f"{log.estimated_1rm:.1f}"
        ])

    return output.getvalue()


def get_chart_data(db_session, user, exercise_name):
    """Fetches date vs 1RM data for a specific exercise."""
    logs = db_session.query(WorkoutLog).filter(
        WorkoutLog.user_id == user.id,
        WorkoutLog.exercise == exercise_name
    ).order_by(WorkoutLog.date).all()

    labels = []  # Dates
    data = []  # 1RM values

    for log in logs:
        labels.append(log.date.strftime("%d/%m"))
        data.append(log.estimated_1rm)

    return {"labels": labels, "data": data, "exercise": exercise_name}