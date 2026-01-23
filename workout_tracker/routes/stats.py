from datetime import datetime
import csv
import io
import json

from flask import Response, flash, jsonify, redirect, render_template, url_for
from flask_login import login_required, current_user
from sqlalchemy import desc

from models import Session, WorkoutLog
from services.stats import get_chart_data, get_csv_export
from utils.logger import logger
from utils.validators import sanitize_text_input


def register_stats_routes(app):
    @login_required
    def stats_index():
        user = current_user

        try:
            exercises = (
                Session.query(WorkoutLog.exercise)
                .filter_by(user_id=user.id)
                .distinct()
                .order_by(WorkoutLog.exercise)
                .all()
            )
            exercises = [e[0] for e in exercises]

            logs = (
                Session.query(WorkoutLog)
                .filter_by(user_id=user.id)
                .order_by(desc(WorkoutLog.date))
                .all()
            )

            bw_exercises = []
            if user.bodyweight is None and logs:
                bw_exercises = sorted({
                    log.exercise
                    for log in logs
                    if 'bw' in (getattr(log, 'exercise_string', '') or '').lower()
                    or 'bw' in (log.sets_display or '').lower()
                })

            csv_size_kb = 0
            json_size_kb = 0

            if logs:
                output = io.StringIO()
                writer = csv.writer(output)
                writer.writerow(['Date', 'Exercise', 'Top Weight (kg)', 'Reps', 'Est 1RM (kg)'])
                for log in logs:
                    writer.writerow([
                        log.date.strftime("%Y-%m-%d"),
                        log.exercise,
                        log.top_weight or 0,
                        log.top_reps or 0,
                        f"{log.estimated_1rm:.1f}" if log.estimated_1rm else "0.0",
                    ])
                csv_bytes = output.getvalue().encode('utf-8')
                csv_size_kb = (len(csv_bytes) + 1023) // 1024

                workouts_by_date = {}
                for log in logs:
                    date_str = log.date.strftime('%Y-%m-%d')
                    workouts_by_date.setdefault(date_str, []).append(
                        {
                            'exercise': log.exercise,
                            'top_weight': log.top_weight,
                            'top_reps': log.top_reps,
                            'estimated_1rm': log.estimated_1rm,
                        }
                    )

                data = {
                    'user': user.username,
                    'export_date': datetime.now().isoformat(),
                    'workouts': [
                        {'date': date_str, 'exercises': exercises}
                        for date_str, exercises in workouts_by_date.items()
                    ],
                }
                json_bytes = json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')
                json_size_kb = (len(json_bytes) + 1023) // 1024

            return render_template(
                'stats.html',
                exercises=exercises,
                csv_size_kb=csv_size_kb,
                json_size_kb=json_size_kb,
                bw_exercises=bw_exercises,
                bw_warning_enabled=user.bodyweight is None,
            )
        except Exception as e:
            logger.error(f"Error in stats_index: {e}", exc_info=True)
            flash("Error loading statistics.", "error")
            return redirect(url_for('user_dashboard', username=user.username))

    @login_required
    def stats_data(exercise):
        user = current_user

        try:
            exercise = sanitize_text_input(exercise, max_length=100)
            return jsonify(get_chart_data(Session, user, exercise))
        except Exception as e:
            logger.error(f"Error getting stats data: {e}", exc_info=True)
            return jsonify({'error': 'Failed to load data'}), 500

    @login_required
    def export_csv():
        user = current_user

        try:
            csv_data = get_csv_export(Session, user)
            filename = (
                f"workout_history_{user.username}_{datetime.now().strftime('%Y%m%d')}.csv"
            )

            return Response(
                csv_data,
                mimetype='text/csv',
                headers={'Content-disposition': f"attachment; filename={filename}"},
            )
        except Exception as e:
            logger.error(f"Error exporting CSV: {e}", exc_info=True)
            flash("Error exporting data.", "error")
            return redirect(url_for('stats_index'))

    @login_required
    def export_json():
        user = current_user

        try:
            logs = (
                Session.query(WorkoutLog)
                .filter_by(user_id=user.id)
                .order_by(desc(WorkoutLog.date))
                .all()
            )

            data = {'user': user.username, 'export_date': datetime.now().isoformat(), 'workouts': []}

            workouts_by_date = {}
            for log in logs:
                date_str = log.date.strftime('%Y-%m-%d')
                if date_str not in workouts_by_date:
                    workouts_by_date[date_str] = []

                workouts_by_date[date_str].append(
                    {
                        'exercise': log.exercise,
                        'top_weight': log.top_weight,
                        'top_reps': log.top_reps,
                        'estimated_1rm': log.estimated_1rm,
                    }
                )

            for date_str, exercises in workouts_by_date.items():
                data['workouts'].append({'date': date_str, 'exercises': exercises})

            filename = (
                f"workout_history_{user.username}_{datetime.now().strftime('%Y%m%d')}.json"
            )

            return Response(
                json.dumps(data, ensure_ascii=False, indent=2),
                mimetype='application/json',
                headers={'Content-disposition': f"attachment; filename={filename}"},
            )
        except Exception as e:
            logger.error(f"Error exporting JSON: {e}", exc_info=True)
            flash("Error exporting data.", "error")
            return redirect(url_for('stats_index'))

    app.add_url_rule('/stats', endpoint='stats_index', view_func=stats_index, methods=['GET'])
    app.add_url_rule(
        '/stats/data/<exercise>',
        endpoint='stats_data',
        view_func=stats_data,
        methods=['GET'],
    )
    app.add_url_rule('/export_csv', endpoint='export_csv', view_func=export_csv, methods=['GET'])
    app.add_url_rule('/export_json', endpoint='export_json', view_func=export_json, methods=['GET'])
