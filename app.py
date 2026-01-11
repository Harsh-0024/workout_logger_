import os
from flask import Flask, render_template, request, redirect, url_for, flash, session
import urllib.parse
from workout_parser import workout_parser
from list_of_exercise import get_workout_days
from models import initialize_database, Session, User, Plan, RepRange
import logic

# Import the migration module so we can run it via route
import migrate_db

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'muscle_gains_secret')


@app.template_filter('url_encode')
def url_encode_filter(s):
    return urllib.parse.quote(s)


@app.teardown_appcontext
def shutdown_session(exception=None):
    Session.remove()


def get_current_user():
    if 'user_id' not in session: return None
    return Session.query(User).get(session['user_id'])


# --- THE SECRET FIX ROUTE ---
@app.route('/internal_db_fix')
def internal_db_fix():
    """Triggers the migration logic from the browser."""
    try:
        result_log = migrate_db.run_migration()
        # Return plain text log so you can see what happened
        return f"<pre>{result_log}</pre>"
    except Exception as e:
        return f"Error launching migration: {e}"


# --- REGULAR ROUTES ---

@app.route('/')
def index():
    user = get_current_user()
    if user:
        return render_template('index.html', user=user.username.title())
    return render_template('select_user.html')


@app.route('/login/<username>')
def login(username):
    user = Session.query(User).filter_by(username=username).first()
    if user:
        session['user_id'] = user.id
        return redirect(url_for('index'))
    return "User not found", 404


@app.route('/switch')
def switch_user():
    session.clear()
    return redirect(url_for('index'))


@app.route('/log', methods=['GET', 'POST'])
def log_workout():
    user = get_current_user()
    if not user: return redirect(url_for('index'))

    if request.method == 'GET': return render_template('log.html')

    raw = request.form.get('workout_text')
    parsed = workout_parser(raw)
    if not parsed:
        flash("Parsing failed", "error")
        return redirect(url_for('log_workout'))

    summary = logic.handle_workout_log(Session, user, parsed)
    Session.commit()

    return render_template('result.html', summary=summary, date=parsed['date'].strftime('%Y-%m-%d'))


@app.route('/retrieve/categories')
def retrieve_categories():
    user = get_current_user()
    if not user: return redirect(url_for('index'))

    plan = Session.query(Plan).filter_by(user_id=user.id).first()
    raw_text = plan.text_content if plan else ""
    data = get_workout_days(raw_text)

    return render_template('retrieve_step1.html', categories=list(data["workout"].keys()))


@app.route('/retrieve/days/<category>')
def retrieve_days(category):
    user = get_current_user()
    if not user: return redirect(url_for('index'))

    plan = Session.query(Plan).filter_by(user_id=user.id).first()
    data = get_workout_days(plan.text_content)

    if category not in data["workout"]: return "Invalid"
    count = len(data["workout"][category])

    return render_template('retrieve_step2.html', category_name=category, num_days=count)


@app.route('/retrieve/final/<category>/<int:day_id>')
def retrieve_final(category, day_id):
    user = get_current_user()
    if not user: return redirect(url_for('index'))

    output = logic.generate_retrieve_output(Session, user, category, day_id)
    return render_template('retrieve_step3.html', output=output)


@app.route('/set_plan', methods=['GET', 'POST'])
def set_plan():
    user = get_current_user()
    if not user: return redirect(url_for('index'))

    plan = Session.query(Plan).filter_by(user_id=user.id).first()
    if request.method == 'POST':
        plan.text_content = request.form.get('plan_text')
        Session.commit()
        return redirect(url_for('index'))

    return render_template('set_plan.html', current_plan=plan.text_content)


@app.route('/set_exercises', methods=['GET', 'POST'])
def set_exercises():
    user = get_current_user()
    if not user: return redirect(url_for('index'))

    reps = Session.query(RepRange).filter_by(user_id=user.id).first()
    if request.method == 'POST':
        reps.text_content = request.form.get('rep_text')
        Session.commit()
        return redirect(url_for('index'))

    return render_template('set_exercises.html', current_reps=reps.text_content)


if __name__ == '__main__':
    initialize_database()
    port = int(os.environ.get("PORT", 5001))
    app.run(host='0.0.0.0', port=port)