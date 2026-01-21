from datetime import datetime
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

            return render_template('stats.html', exercises=exercises)
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
