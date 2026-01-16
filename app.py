"""
Main Flask application for the Workout Tracker.
"""
import os
import urllib.parse
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, session, Response, jsonify
from sqlalchemy import desc

# Application imports
from config import Config
from parsers.workout import workout_parser
from services.logging import handle_workout_log
from services.retrieve import generate_retrieve_output
from services.stats import get_csv_export, get_chart_data
from models import initialize_database, Session, User, Plan, RepRange, WorkoutLog
from list_of_exercise import get_workout_days
from utils.logger import logger
from utils.errors import ParsingError, ValidationError, UserNotFoundError
from utils.validators import validate_username, sanitize_text_input
import migrate_db

# Initialize Flask app
app = Flask(__name__)
app.secret_key = Config.SECRET_KEY
app.config.from_object(Config)

# Template filters
@app.template_filter('url_encode')
def url_encode_filter(s):
    """URL encode filter for templates."""
    return urllib.parse.quote(str(s))


@app.template_filter('format_date')
def format_date_filter(date_obj):
    """Format date for display."""
    if isinstance(date_obj, datetime):
        return date_obj.strftime('%Y-%m-%d')
    return str(date_obj)


# Database session management
@app.teardown_appcontext
def shutdown_session(exception=None):
    """Remove database session on app context teardown."""
    Session.remove()


# Error handlers
@app.errorhandler(404)
def not_found(error):
    """Handle 404 errors."""
    return render_template('error.html', error_code=404, error_message="Page not found"), 404


@app.errorhandler(500)
def internal_error(error):
    """Handle 500 errors."""
    logger.error(f"Internal server error: {error}", exc_info=True)
    Session.rollback()
    return render_template('error.html', error_code=500, error_message="Internal server error"), 500


# Helper functions
def get_current_user():
    """Get the currently logged in user."""
    if 'user_id' not in session:
        return None
    try:
        return Session.query(User).get(session['user_id'])
    except Exception as e:
        logger.error(f"Error getting current user: {e}", exc_info=True)
        session.clear()
        return None


def require_login(f):
    """Decorator to require user login."""
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user = get_current_user()
        if not user:
            flash("Please log in to continue.", "info")
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function


def get_recent_workouts(user, limit=5):
    """Get recent workout dates for a user."""
    try:
        recent_dates = Session.query(WorkoutLog.date).filter_by(
            user_id=user.id
        ).distinct().order_by(desc(WorkoutLog.date)).limit(limit).all()
        return [date[0] for date in recent_dates]
    except Exception as e:
        logger.error(f"Error getting recent workouts: {e}", exc_info=True)
        return []


def get_workout_stats(user):
    """Get quick stats for dashboard."""
    try:
        total_workouts = Session.query(WorkoutLog.date).filter_by(
            user_id=user.id
        ).distinct().count()
        
        total_exercises = Session.query(WorkoutLog.exercise).filter_by(
            user_id=user.id
        ).distinct().count()
        
        # Get most recent workout date
        latest = Session.query(WorkoutLog.date).filter_by(
            user_id=user.id
        ).order_by(desc(WorkoutLog.date)).first()
        
        latest_date = latest[0] if latest else None
        
        return {
            'total_workouts': total_workouts,
            'total_exercises': total_exercises,
            'latest_workout': latest_date
        }
    except Exception as e:
        logger.error(f"Error getting workout stats: {e}", exc_info=True)
        return {
            'total_workouts': 0,
            'total_exercises': 0,
            'latest_workout': None
        }


# --- ROUTES ---

@app.route('/')
def index():
    """Home page - redirects to user dashboard or shows user selection."""
    user = get_current_user()
    if user:
        # Redirect to personalized dashboard
        return redirect(url_for('user_dashboard', username=user.username))
    return render_template('select_user.html')


@app.route('/<username>')
def user_dashboard(username):
    """User-specific dashboard."""
    try:
        username = validate_username(username)
        user = Session.query(User).filter_by(username=username).first()
        
        if not user:
            raise UserNotFoundError(f"User '{username}' not found")
        
        # Auto-login
        session['user_id'] = user.id
        
        # Get dashboard data
        recent_workouts = get_recent_workouts(user, limit=5)
        stats = get_workout_stats(user)
        
        return render_template(
            'index.html',
            user=user.username.title(),
            recent_workouts=recent_workouts,
            stats=stats
        )
    except (ValidationError, UserNotFoundError) as e:
        flash(str(e), "error")
        return redirect(url_for('index'))
    except Exception as e:
        logger.error(f"Error in user_dashboard: {e}", exc_info=True)
        flash("An error occurred. Please try again.", "error")
        return redirect(url_for('index'))


@app.route('/login/<username>')
def login(username):
    """Login route for a specific user."""
    try:
        username = validate_username(username)
        user = Session.query(User).filter_by(username=username).first()
        
        if not user:
            raise UserNotFoundError(f"User '{username}' not found")
        
        session['user_id'] = user.id
        flash(f"Welcome back, {user.username.title()}!", "success")
        return redirect(url_for('user_dashboard', username=user.username))
    except (ValidationError, UserNotFoundError) as e:
        flash(str(e), "error")
        return redirect(url_for('index'))
    except Exception as e:
        logger.error(f"Error in login: {e}", exc_info=True)
        flash("An error occurred during login.", "error")
        return redirect(url_for('index'))


@app.route('/switch')
def switch_user():
    """Switch/logout current user."""
    session.clear()
    flash("Logged out successfully.", "info")
    return redirect(url_for('index'))


@app.route('/internal_db_fix')
def internal_db_fix():
    """Internal database migration endpoint (should be protected in production)."""
    try:
        result_log = migrate_db.run_migration()
        return f"<pre>{result_log}</pre>"
    except Exception as e:
        logger.error(f"Migration error: {e}", exc_info=True)
        return f"<pre>Error: {e}</pre>", 500


@app.route('/log', methods=['GET', 'POST'])
@require_login
def log_workout():
    """Log a workout session."""
    user = get_current_user()
    
    if request.method == 'GET':
        return render_template('log.html')
    
    # POST request - process workout
    raw_text = request.form.get('workout_text', '').strip()
    
    if not raw_text:
        flash("Please enter workout data.", "error")
        return redirect(url_for('log_workout'))
    
    # Parse workout
    try:
        parsed = workout_parser(raw_text)
        if not parsed:
            raise ParsingError("Could not parse workout data. Please check the format.")
    except ParsingError as e:
        flash(str(e), "error")
        return redirect(url_for('log_workout'))
    except Exception as e:
        logger.error(f"Parsing error: {e}", exc_info=True)
        flash("Error parsing workout data. Please check the format.", "error")
        return redirect(url_for('log_workout'))
    
    # Save workout
    try:
        summary = handle_workout_log(Session, user, parsed)
        Session.commit()
        logger.info(f"Workout logged successfully for user {user.username} on {parsed['date']}")
        
        return render_template(
            'result.html',
            summary=summary,
            date=parsed['date'].strftime('%Y-%m-%d')
        )
    except Exception as e:
        Session.rollback()
        logger.error(f"Error saving workout: {e}", exc_info=True)
        flash("Error saving workout. Please try again.", "error")
        return redirect(url_for('log_workout'))


# --- STATS ROUTES ---

@app.route('/stats')
@require_login
def stats_index():
    """Statistics dashboard."""
    user = get_current_user()
    
    try:
        # Get list of exercises the user has logged history for
        exercises = Session.query(WorkoutLog.exercise).filter_by(
            user_id=user.id
        ).distinct().order_by(WorkoutLog.exercise).all()
        exercises = [e[0] for e in exercises]
        
        return render_template('stats.html', exercises=exercises)
    except Exception as e:
        logger.error(f"Error in stats_index: {e}", exc_info=True)
        flash("Error loading statistics.", "error")
        return redirect(url_for('user_dashboard', username=user.username))


@app.route('/stats/data/<exercise>')
@require_login
def stats_data(exercise):
    """Get chart data for a specific exercise."""
    user = get_current_user()
    
    try:
        exercise = sanitize_text_input(exercise, max_length=100)
        return jsonify(get_chart_data(Session, user, exercise))
    except Exception as e:
        logger.error(f"Error getting stats data: {e}", exc_info=True)
        return jsonify({"error": "Failed to load data"}), 500


@app.route('/export_csv')
@require_login
def export_csv():
    """Export workout history as CSV."""
    user = get_current_user()
    
    try:
        csv_data = get_csv_export(Session, user)
        filename = f"workout_history_{user.username}_{datetime.now().strftime('%Y%m%d')}.csv"
        
        return Response(
            csv_data,
            mimetype="text/csv",
            headers={"Content-disposition": f"attachment; filename={filename}"}
        )
    except Exception as e:
        logger.error(f"Error exporting CSV: {e}", exc_info=True)
        flash("Error exporting data.", "error")
        return redirect(url_for('stats_index'))


@app.route('/export_json')
@require_login
def export_json():
    """Export workout history as JSON."""
    user = get_current_user()
    
    try:
        logs = Session.query(WorkoutLog).filter_by(
            user_id=user.id
        ).order_by(desc(WorkoutLog.date)).all()
        
        data = {
            'user': user.username,
            'export_date': datetime.now().isoformat(),
            'workouts': []
        }
        
        # Group by date
        workouts_by_date = {}
        for log in logs:
            date_str = log.date.strftime('%Y-%m-%d')
            if date_str not in workouts_by_date:
                workouts_by_date[date_str] = []
            
            workouts_by_date[date_str].append({
                'exercise': log.exercise,
                'top_weight': log.top_weight,
                'top_reps': log.top_reps,
                'estimated_1rm': log.estimated_1rm
            })
        
        for date_str, exercises in workouts_by_date.items():
            data['workouts'].append({
                'date': date_str,
                'exercises': exercises
            })
        
        filename = f"workout_history_{user.username}_{datetime.now().strftime('%Y%m%d')}.json"
        
        return Response(
            jsonify(data),
            mimetype="application/json",
            headers={"Content-disposition": f"attachment; filename={filename}"}
        )
    except Exception as e:
        logger.error(f"Error exporting JSON: {e}", exc_info=True)
        flash("Error exporting data.", "error")
        return redirect(url_for('stats_index'))


# --- RETRIEVE & PLAN ROUTES ---

@app.route('/retrieve/categories')
@require_login
def retrieve_categories():
    """Show workout categories."""
    user = get_current_user()
    
    try:
        plan = Session.query(Plan).filter_by(user_id=user.id).first()
        raw_text = plan.text_content if plan else ""
        data = get_workout_days(raw_text)
        
        categories = list(data.get("workout", {}).keys())
        
        if not categories:
            flash("No workout plan found. Please set up your plan first.", "info")
            return redirect(url_for('set_plan'))
        
        return render_template('retrieve_step1.html', categories=categories)
    except Exception as e:
        logger.error(f"Error in retrieve_categories: {e}", exc_info=True)
        flash("Error loading workout categories.", "error")
        return redirect(url_for('user_dashboard', username=user.username))


@app.route('/retrieve/days/<category>')
@require_login
def retrieve_days(category):
    """Show workout days for a category."""
    user = get_current_user()
    
    try:
        category = sanitize_text_input(category, max_length=100)
        plan = Session.query(Plan).filter_by(user_id=user.id).first()
        
        if not plan:
            flash("No workout plan found.", "error")
            return redirect(url_for('set_plan'))
        
        data = get_workout_days(plan.text_content)
        
        if category not in data.get("workout", {}):
            flash("Invalid category.", "error")
            return redirect(url_for('retrieve_categories'))
        
        num_days = len(data["workout"][category])
        return render_template(
            'retrieve_step2.html',
            category_name=category,
            num_days=num_days
        )
    except Exception as e:
        logger.error(f"Error in retrieve_days: {e}", exc_info=True)
        flash("Error loading workout days.", "error")
        return redirect(url_for('retrieve_categories'))


@app.route('/retrieve/final/<category>/<int:day_id>')
@require_login
def retrieve_final(category, day_id):
    """Generate final workout plan."""
    user = get_current_user()
    
    try:
        category = sanitize_text_input(category, max_length=100)
        output = generate_retrieve_output(Session, user, category, day_id)
        return render_template('retrieve_step3.html', output=output)
    except Exception as e:
        logger.error(f"Error in retrieve_final: {e}", exc_info=True)
        flash("Error generating workout plan.", "error")
        return redirect(url_for('retrieve_categories'))


@app.route('/set_plan', methods=['GET', 'POST'])
@require_login
def set_plan():
    """Set or update workout plan."""
    user = get_current_user()
    
    try:
        plan = Session.query(Plan).filter_by(user_id=user.id).first()
        
        if not plan:
            flash("Plan not found. Creating new plan.", "info")
            plan = Plan(user_id=user.id, text_content="")
            Session.add(plan)
            Session.flush()
        
        if request.method == 'POST':
            plan_text = request.form.get('plan_text', '').strip()
            plan.text_content = plan_text
            plan.updated_at = datetime.now()
            Session.commit()
            flash("Workout plan updated successfully!", "success")
            return redirect(url_for('user_dashboard', username=user.username))
        
        return render_template('set_plan.html', current_plan=plan.text_content or "")
    except Exception as e:
        Session.rollback()
        logger.error(f"Error in set_plan: {e}", exc_info=True)
        flash("Error saving workout plan.", "error")
        return redirect(url_for('user_dashboard', username=user.username))


@app.route('/set_exercises', methods=['GET', 'POST'])
@require_login
def set_exercises():
    """Set or update exercise rep ranges."""
    user = get_current_user()
    
    try:
        reps = Session.query(RepRange).filter_by(user_id=user.id).first()
        
        if not reps:
            flash("Rep ranges not found. Creating new entry.", "info")
            reps = RepRange(user_id=user.id, text_content="")
            Session.add(reps)
            Session.flush()
        
        if request.method == 'POST':
            rep_text = request.form.get('rep_text', '').strip()
            reps.text_content = rep_text
            reps.updated_at = datetime.now()
            Session.commit()
            flash("Rep ranges updated successfully!", "success")
            return redirect(url_for('user_dashboard', username=user.username))
        
        return render_template('set_exercises.html', current_reps=reps.text_content or "")
    except Exception as e:
        Session.rollback()
        logger.error(f"Error in set_exercises: {e}", exc_info=True)
        flash("Error saving rep ranges.", "error")
        return redirect(url_for('user_dashboard', username=user.username))


# Application entry point
if __name__ == '__main__':
    try:
        initialize_database()
        logger.info("Database initialized successfully")
        
        port = Config.PORT
        host = Config.HOST
        debug = Config.DEBUG
        
        logger.info(f"Starting Workout Tracker on {host}:{port} (debug={debug})")
        app.run(host=host, port=port, debug=debug)
    except Exception as e:
        logger.critical(f"Failed to start application: {e}", exc_info=True)
        raise
