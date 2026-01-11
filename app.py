import os
from flask import Flask, render_template, request, redirect, url_for, flash, session
import urllib.parse

# --- NEW IMPORTS ---
from parsers.workout import workout_parser
from services.logging import handle_workout_log
from services.retrieve import generate_retrieve_output
from models import initialize_database, Session, User, Plan, RepRange
from list_of_exercise import get_workout_days
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


# --- ROUTES ---

@app.route('/')
def index():
    user = get_current_user()
    if user: return render_template('index.html', user=user.username.title())
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


@app.route('/internal_db_fix')
def internal_db_fix():
    try:
        result_log = migrate_db.run_migration()
        return f"<pre>{result_log}</pre>"
    except Exception as e:
        return f"Error: {e}"


@app.route('/log', methods=['GET', 'POST'])
def log_workout():
    user = get_current_user()
    if not user: return redirect(url_for('index'))
    if request.method == 'GET': return render_template('log.html')

    raw = request.form.get('workout_text')
    # Call Parser from new location
    parsed = workout_parser(raw)

    if not parsed:
        flash("Could not parse workout data.", "error")
        return redirect(url_for('log_workout'))

    try:
        # Call Service from new location
        summary = handle_workout_log(Session, user, parsed)
        Session.commit()
        return render_template('result.html', summary=summary, date=parsed['date'].strftime('%Y-%m-%d'))
    except Exception as e:
        Session.rollback()
        print(f"Transaction Failed: {e}")
        flash("Error saving workout.", "error")
        return redirect(url_for('log_workout'))


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
    return render_template('retrieve_step2.html', category_name=category, num_days=len(data["workout"][category]))


@app.route('/retrieve/final/<category>/<int:day_id>')
def retrieve_final(category, day_id):
    user = get_current_user()
    if not user: return redirect(url_for('index'))
    # Call Service from new location
    output = generate_retrieve_output(Session, user, category, day_id)
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