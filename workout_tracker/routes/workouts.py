from datetime import datetime, timedelta
from flask import flash, redirect, render_template, request, url_for
from flask_login import login_required, current_user
from sqlalchemy import desc

from models import Session, User, WorkoutLog
from parsers.workout import workout_parser
from services.logging import handle_workout_log
from utils.errors import ParsingError, ValidationError, UserNotFoundError
from utils.logger import logger
from utils.validators import sanitize_text_input, validate_username


def register_workout_routes(app):
    def build_exercise_text(logs):
        lines = []
        for log in logs:
            if getattr(log, 'exercise_string', None):
                lines.append(log.exercise_string.strip())
                continue

            if getattr(log, 'sets_display', None):
                lines.append(f"{log.exercise} {log.sets_display}")
            elif log.top_weight and log.top_reps:
                lines.append(f"{log.exercise} {log.top_weight} x {log.top_reps}")
            else:
                lines.append(log.exercise)
        return "\n".join(lines)

    def get_recent_workouts(user, limit=50):
        try:
            logs = (
                Session.query(WorkoutLog)
                .filter_by(user_id=user.id)
                .order_by(desc(WorkoutLog.date), WorkoutLog.id)
                .all()
            )

            workouts = []
            by_date = {}
            for log in logs:
                date_key = log.date.date() if isinstance(log.date, datetime) else log.date

                if date_key not in by_date:
                    by_date[date_key] = {
                        'date': log.date,
                        'title': log.workout_name or "Workout",
                        'exercises': set(),
                    }
                by_date[date_key]['exercises'].add(log.exercise)

            for date_key in sorted(by_date.keys(), reverse=True):
                item = by_date[date_key]
                workouts.append({
                    'date': item['date'],
                    'title': item['title'],
                    'exercises': ", ".join(sorted(item['exercises'])),
                })
                if limit is not None and len(workouts) >= limit:
                    break

            return workouts
        except Exception as e:
            logger.error(f"Error getting recent workouts: {e}", exc_info=True)
            return []

    def get_workout_stats(user):
        try:
            total_workouts = (
                Session.query(WorkoutLog.date).filter_by(user_id=user.id).distinct().count()
            )

            total_exercises = (
                Session.query(WorkoutLog.exercise)
                .filter_by(user_id=user.id)
                .distinct()
                .count()
            )

            latest = (
                Session.query(WorkoutLog.date)
                .filter_by(user_id=user.id)
                .order_by(desc(WorkoutLog.date))
                .first()
            )

            latest_date = latest[0] if latest else None

            return {
                'total_workouts': total_workouts,
                'total_exercises': total_exercises,
                'latest_workout': latest_date,
            }
        except Exception as e:
            logger.error(f"Error getting workout stats: {e}", exc_info=True)
            return {
                'total_workouts': 0,
                'total_exercises': 0,
                'latest_workout': None,
            }

    def index():
        if current_user.is_authenticated:
            return redirect(url_for('user_dashboard', username=current_user.username))
        return redirect(url_for('login'))

    @login_required
    def user_dashboard(username):
        try:
            username = validate_username(username)

            if current_user.username != username and not current_user.is_admin():
                flash("You can only view your own dashboard.", "error")
                return redirect(url_for('user_dashboard', username=current_user.username))

            user = Session.query(User).filter_by(username=username).first()

            if not user:
                if username != current_user.username:
                    return redirect(url_for('user_dashboard', username=current_user.username))
                raise UserNotFoundError("User not found")

            recent_workouts = get_recent_workouts(user, limit=250)

            return render_template(
                'index.html',
                user=user.username.title(),
                recent_workouts=recent_workouts,
            )
        except (ValidationError, UserNotFoundError) as e:
            flash(str(e), "error")
            return redirect(url_for('index'))
        except Exception as e:
            logger.error(f"Error in user_dashboard: {e}", exc_info=True)
            flash("An error occurred. Please try again.", "error")
            return redirect(url_for('index'))

    @login_required
    def view_workout(date_str):
        user = current_user
        
        try:
            workout_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            start_dt = datetime.combine(workout_date, datetime.min.time())
            end_dt = start_dt + timedelta(days=1)
            
            logs = (
                Session.query(WorkoutLog)
                .filter_by(user_id=user.id)
                .filter(WorkoutLog.date >= start_dt)
                .filter(WorkoutLog.date < end_dt)
                .order_by(WorkoutLog.id)
                .all()
            )
            
            if not logs:
                flash("Workout not found.", "error")
                return redirect(url_for('user_dashboard', username=user.username))

            workout_name = logs[0].workout_name or "Workout"
            
            # Calculate volume for each exercise
            for log in logs:
                total_volume = 0
                if log.sets_json and isinstance(log.sets_json, dict):
                    weights = log.sets_json.get('weights') or []
                    reps_list = log.sets_json.get('reps') or []
                    for weight, reps in zip(weights, reps_list):
                        try:
                            total_volume += float(weight) * int(reps)
                        except (TypeError, ValueError):
                            continue
                elif log.sets_display:
                    # Parse sets_display to calculate volume (supports x/×)
                    sets = log.sets_display.split(', ')
                    for s in sets:
                        try:
                            normalized = s.replace('×', 'x')
                            parts = [p.strip() for p in normalized.split('x')]
                            if len(parts) == 2:
                                weight = float(parts[0])
                                reps = int(parts[1])
                                total_volume += weight * reps
                        except (ValueError, IndexError):
                            continue
                log.total_volume = total_volume if total_volume > 0 else None
            
            return render_template(
                'workout_detail.html',
                date=date_str,
                workout_name=workout_name,
                logs=logs
            )
        except ValueError:
            flash("Invalid date format.", "error")
            return redirect(url_for('user_dashboard', username=user.username))
        except Exception as e:
            logger.error(f"Error viewing workout: {e}", exc_info=True)
            flash("Error loading workout.", "error")
            return redirect(url_for('user_dashboard', username=user.username))

    @login_required
    def workout_history():
        return redirect(url_for('user_dashboard', username=current_user.username))

    @login_required
    def edit_workout(date_str):
        user = current_user

        try:
            workout_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            start_dt = datetime.combine(workout_date, datetime.min.time())
            end_dt = start_dt + timedelta(days=1)

            logs = (
                Session.query(WorkoutLog)
                .filter_by(user_id=user.id)
                .filter(WorkoutLog.date >= start_dt)
                .filter(WorkoutLog.date < end_dt)
                .order_by(WorkoutLog.id)
                .all()
            )

            if not logs:
                flash("Workout not found.", "error")
                return redirect(url_for('user_dashboard', username=user.username))

            workout_name = logs[0].workout_name or "Workout"
            workout_text = build_exercise_text(logs)

            if request.method == 'POST':
                title = sanitize_text_input(request.form.get('workout_title', ''), max_length=100) or "Workout"
                date_input = request.form.get('workout_date', '').strip()
                exercises_input = request.form.get('workout_text', '').strip()

                if not date_input:
                    flash("Please select a workout date.", "error")
                    return redirect(url_for('edit_workout', date_str=date_str))

                if not exercises_input:
                    flash("Please enter workout exercises.", "error")
                    return redirect(url_for('edit_workout', date_str=date_str))

                try:
                    new_date = datetime.strptime(date_input, '%Y-%m-%d').date()
                except ValueError:
                    flash("Invalid date format.", "error")
                    return redirect(url_for('edit_workout', date_str=date_str))

                new_start_dt = datetime.combine(new_date, datetime.min.time())
                new_end_dt = new_start_dt + timedelta(days=1)

                if new_date != workout_date:
                    conflict = (
                        Session.query(WorkoutLog)
                        .filter_by(user_id=user.id)
                        .filter(WorkoutLog.date >= new_start_dt)
                        .filter(WorkoutLog.date < new_end_dt)
                        .first()
                    )
                    if conflict:
                        flash("A workout already exists on that date. Edit that day instead.", "error")
                        return redirect(url_for('edit_workout', date_str=date_str))

                header_date = new_date.strftime('%d/%m')
                raw_text = f"{header_date} {title}\n{exercises_input}"

                parsed = workout_parser(raw_text)
                if not parsed:
                    raise ParsingError("Could not parse workout data. Please check the format.")

                parsed['date'] = new_start_dt
                parsed['workout_name'] = title

                Session.query(WorkoutLog).filter_by(user_id=user.id).filter(
                    WorkoutLog.date >= start_dt,
                    WorkoutLog.date < end_dt,
                ).delete(synchronize_session=False)

                handle_workout_log(Session, user, parsed)
                Session.commit()

                flash("Workout updated successfully!", "success")
                return redirect(url_for('view_workout', date_str=new_date.strftime('%Y-%m-%d')))

            return render_template(
                'workout_edit.html',
                workout_date=workout_date.strftime('%Y-%m-%d'),
                workout_name=workout_name,
                workout_text=workout_text,
                date=date_str,
            )
        except ValueError:
            flash("Invalid date format.", "error")
            return redirect(url_for('user_dashboard', username=user.username))
        except ParsingError as e:
            Session.rollback()
            flash(str(e), "error")
            return redirect(url_for('edit_workout', date_str=date_str))
        except Exception as e:
            Session.rollback()
            logger.error(f"Error editing workout: {e}", exc_info=True)
            flash("Error updating workout.", "error")
            return redirect(url_for('edit_workout', date_str=date_str))

    @login_required
    def delete_workout(date_str):
        user = current_user

        try:
            workout_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            start_dt = datetime.combine(workout_date, datetime.min.time())
            end_dt = start_dt + timedelta(days=1)

            deleted = (
                Session.query(WorkoutLog)
                .filter_by(user_id=user.id)
                .filter(WorkoutLog.date >= start_dt)
                .filter(WorkoutLog.date < end_dt)
                .delete(synchronize_session=False)
            )
            Session.commit()

            if deleted:
                flash("Workout day deleted successfully.", "success")
            else:
                flash("Workout not found.", "error")

            return redirect(url_for('user_dashboard', username=user.username))
        except ValueError:
            flash("Invalid date format.", "error")
            return redirect(url_for('user_dashboard', username=user.username))
        except Exception as e:
            Session.rollback()
            logger.error(f"Error deleting workout: {e}", exc_info=True)
            flash("Error deleting workout.", "error")
            return redirect(url_for('edit_workout', date_str=date_str))

    @login_required
    def log_workout():
        user = current_user

        if request.method == 'GET':
            return render_template('log.html')

        raw_text = request.form.get('workout_text', '').strip()

        if not raw_text:
            flash("Please enter workout data.", "error")
            return redirect(url_for('log_workout'))

        try:
            parsed = workout_parser(raw_text)
            if not parsed:
                raise ParsingError(
                    "Could not parse workout data. Please check the format."
                )
        except ParsingError as e:
            flash(str(e), "error")
            return redirect(url_for('log_workout'))
        except Exception as e:
            logger.error(f"Parsing error: {e}", exc_info=True)
            flash("Error parsing workout data. Please check the format.", "error")
            return redirect(url_for('log_workout'))

        try:
            summary = handle_workout_log(Session, user, parsed)
            Session.commit()
            logger.info(
                f"Workout logged successfully for user {user.username} on {parsed['date']}"
            )

            return render_template(
                'result.html',
                summary=summary,
                date=parsed['date'].strftime('%Y-%m-%d'),
            )
        except Exception as e:
            Session.rollback()
            logger.error(f"Error saving workout: {e}", exc_info=True)
            flash("Error saving workout. Please try again.", "error")
            return redirect(url_for('log_workout'))

    app.add_url_rule('/', endpoint='index', view_func=index, methods=['GET'])
    app.add_url_rule('/workouts', endpoint='workout_history', view_func=workout_history, methods=['GET'])
    app.add_url_rule(
        '/<username>',
        endpoint='user_dashboard',
        view_func=user_dashboard,
        methods=['GET'],
    )
    app.add_url_rule('/workout/<date_str>', endpoint='view_workout', view_func=view_workout, methods=['GET'])
    app.add_url_rule('/workout/<date_str>/edit', endpoint='edit_workout', view_func=edit_workout, methods=['GET', 'POST'])
    app.add_url_rule('/workout/<date_str>/delete', endpoint='delete_workout', view_func=delete_workout, methods=['POST'])
    app.add_url_rule('/log', endpoint='log_workout', view_func=log_workout, methods=['GET', 'POST'])
