from datetime import datetime
import re
import csv
import io
import json

from flask import Response, flash, jsonify, redirect, render_template, request, url_for
from flask_login import login_required, current_user
from sqlalchemy import desc

from models import Session, WorkoutLog
from services.stats import (
    _normalize_exercise_name,
    backfill_log_bodyweight,
    get_average_growth_data,
    get_chart_data,
    get_csv_export,
    get_export_log_count,
    get_overall_progress_data,
    get_json_export,
)
from utils.logger import logger
from utils.validators import sanitize_text_input


def register_stats_routes(app):
    def _parse_export_date_range():
        start_raw = (request.args.get('start_date') or '').strip()
        end_raw = (request.args.get('end_date') or '').strip()

        if not start_raw and not end_raw:
            return None, None

        if not start_raw or not end_raw:
            raise ValueError("Please provide both start and end dates.")

        try:
            start_date = datetime.strptime(start_raw, '%Y-%m-%d').date()
            end_date = datetime.strptime(end_raw, '%Y-%m-%d').date()
        except ValueError as exc:
            raise ValueError("Invalid date format. Use YYYY-MM-DD.") from exc

        if end_date < start_date:
            raise ValueError("End date cannot be earlier than start date.")

        return start_date, end_date

    @login_required
    def stats_index():
        user = current_user

        try:
            updated = backfill_log_bodyweight(Session, user)
            if updated:
                Session.commit()
            exercises = (
                Session.query(WorkoutLog.exercise)
                .filter_by(user_id=user.id)
                .distinct()
                .order_by(WorkoutLog.exercise)
                .all()
            )
            exercises = [e[0] for e in exercises]
            exercise_options = []
            seen_keys = set()
            for ex in exercises:
                cleaned = re.sub(r'^\s*\d+\s*[\.)]\s*', '', ex).strip()
                label = cleaned or ex
                key = _normalize_exercise_name(label)
                if not key or key in seen_keys:
                    continue
                seen_keys.add(key)
                exercise_options.append({
                    'value': label,
                    'label': label,
                })

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
                csv_data = get_csv_export(Session, user)
                csv_bytes = csv_data.encode('utf-8')
                csv_size_kb = (len(csv_bytes) + 1023) // 1024

                json_payload = get_json_export(Session, user)
                json_bytes = json.dumps(json_payload, ensure_ascii=False, indent=2).encode('utf-8')
                json_size_kb = (len(json_bytes) + 1023) // 1024

            return render_template(
                'stats.html',
                exercise_options=exercise_options,
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
            updated = backfill_log_bodyweight(Session, user)
            if updated:
                Session.commit()
            return jsonify(get_chart_data(Session, user, exercise))
        except Exception as e:
            logger.error(f"Error getting stats data: {e}", exc_info=True)
            return jsonify({'error': 'Failed to load data'}), 500

    @login_required
    def stats_average_data():
        user = current_user

        try:
            updated = backfill_log_bodyweight(Session, user)
            if updated:
                Session.commit()

            mode = (request.args.get('mode') or '').strip().lower()
            if mode in {'index', 'rate'}:
                baseline_days = request.args.get('baseline_days', type=int) or 24
                min_sessions = request.args.get('min_sessions', type=int) or 3
                fade_start_days = request.args.get('fade_start_days', type=int) or 60
                fade_end_days = request.args.get('fade_end_days', type=int) or 90
                return jsonify(
                    get_overall_progress_data(
                        Session,
                        user,
                        mode=mode,
                        baseline_days_target=baseline_days,
                        min_sessions=min_sessions,
                        fade_start_days=fade_start_days,
                        fade_end_days=fade_end_days,
                    )
                )

            return jsonify(get_average_growth_data(Session, user))
        except Exception as e:
            logger.error(f"Error getting average stats data: {e}", exc_info=True)
            return jsonify({'error': 'Failed to load data'}), 500

    @login_required
    def export_csv():
        user = current_user

        try:
            start_date, end_date = _parse_export_date_range()
            log_count = get_export_log_count(Session, user, start_date=start_date, end_date=end_date)
            if log_count == 0:
                flash("No workouts found in that date range.", "warning")
                return redirect(url_for('user_settings') + '#quick-actions')
            csv_data = get_csv_export(Session, user, start_date=start_date, end_date=end_date)
            date_suffix = datetime.now().strftime('%Y%m%d')
            if start_date and end_date:
                date_suffix = f"{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}"
            filename = f"workout_history_{user.username}_{date_suffix}.csv"

            return Response(
                csv_data,
                mimetype='text/csv',
                headers={'Content-disposition': f"attachment; filename={filename}"},
            )
        except ValueError as e:
            flash(str(e), "error")
            return redirect(url_for('user_settings') + '#quick-actions')
        except Exception as e:
            logger.error(f"Error exporting CSV: {e}", exc_info=True)
            flash("Error exporting data.", "error")
            return redirect(url_for('user_settings') + '#quick-actions')

    @login_required
    def export_json():
        user = current_user

        try:
            start_date, end_date = _parse_export_date_range()
            log_count = get_export_log_count(Session, user, start_date=start_date, end_date=end_date)
            if log_count == 0:
                flash("No workouts found in that date range.", "warning")
                return redirect(url_for('user_settings') + '#quick-actions')
            data = get_json_export(Session, user, start_date=start_date, end_date=end_date)
            date_suffix = datetime.now().strftime('%Y%m%d')
            if start_date and end_date:
                date_suffix = f"{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}"
            filename = f"workout_history_{user.username}_{date_suffix}.json"

            return Response(
                json.dumps(data, ensure_ascii=False, indent=2),
                mimetype='application/json',
                headers={'Content-disposition': f"attachment; filename={filename}"},
            )
        except ValueError as e:
            flash(str(e), "error")
            return redirect(url_for('user_settings') + '#quick-actions')
        except Exception as e:
            logger.error(f"Error exporting JSON: {e}", exc_info=True)
            flash("Error exporting data.", "error")
            return redirect(url_for('user_settings') + '#quick-actions')

    app.add_url_rule('/stats', endpoint='stats_index', view_func=stats_index, methods=['GET'])
    app.add_url_rule(
        '/stats/data/<exercise>',
        endpoint='stats_data',
        view_func=stats_data,
        methods=['GET'],
    )
    app.add_url_rule(
        '/stats/data/average',
        endpoint='stats_average_data',
        view_func=stats_average_data,
        methods=['GET'],
    )
    app.add_url_rule('/export_csv', endpoint='export_csv', view_func=export_csv, methods=['GET'])
    app.add_url_rule('/export_json', endpoint='export_json', view_func=export_json, methods=['GET'])
