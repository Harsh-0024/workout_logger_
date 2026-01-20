from datetime import timedelta
from flask import flash, redirect, render_template, request, url_for
from flask_login import login_required, current_user
from sqlalchemy import desc

from models import Session, User, WorkoutLog
from parsers.workout import workout_parser
from services.logging import handle_workout_log
from utils.errors import ParsingError, ValidationError, UserNotFoundError
from utils.logger import logger
from utils.validators import validate_username


def register_workout_routes(app):
    def get_recent_workouts(user, limit=5):
        try:
            recent_dates = (
                Session.query(WorkoutLog.date)
                .filter_by(user_id=user.id)
                .distinct()
                .order_by(desc(WorkoutLog.date))
                .limit(limit)
                .all()
            )
            
            workouts = []
            for date_tuple in recent_dates:
                workout_date = date_tuple[0]
                # Get first exercise of the day to extract workout title
                first_log = (
                    Session.query(WorkoutLog)
                    .filter_by(user_id=user.id, date=workout_date)
                    .order_by(WorkoutLog.id)
                    .first()
                )
                
                # Extract workout title from exercise name (e.g., "Chest & Triceps 1")
                title = "Workout"
                if first_log and first_log.exercise:
                    # Try to extract day info from exercise name
                    parts = first_log.exercise.split()
                    if len(parts) >= 2:
                        # Look for pattern like "19/01 Chest & Triceps 1"
                        if '/' in parts[0]:
                            title = ' '.join(parts[1:]) if len(parts) > 1 else "Workout"
                        else:
                            title = first_log.exercise
                
                workouts.append({
                    'date': workout_date,
                    'title': title
                })
            
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

            recent_workouts = get_recent_workouts(user, limit=5)
            stats = get_workout_stats(user)

            return render_template(
                'index.html',
                user=user.username.title(),
                recent_workouts=recent_workouts,
                stats=stats,
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
            from datetime import datetime
            workout_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            
            logs = (
                Session.query(WorkoutLog)
                .filter_by(user_id=user.id)
                .filter(WorkoutLog.date >= workout_date)
                .filter(WorkoutLog.date < workout_date + timedelta(days=1))
                .order_by(WorkoutLog.id)
                .all()
            )
            
            if not logs:
                flash("Workout not found.", "error")
                return redirect(url_for('user_dashboard', username=user.username))
            
            # Format workout data for display
            workout_text = f"{date_str}\n\n"
            for log in logs:
                workout_text += f"{log.exercise}\n"
            
            return render_template(
                'workout_detail.html',
                date=date_str,
                workout_text=workout_text,
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
    app.add_url_rule(
        '/<username>',
        endpoint='user_dashboard',
        view_func=user_dashboard,
        methods=['GET'],
    )
    app.add_url_rule('/workout/<date_str>', endpoint='view_workout', view_func=view_workout, methods=['GET'])
    app.add_url_rule('/log', endpoint='log_workout', view_func=log_workout, methods=['GET', 'POST'])
