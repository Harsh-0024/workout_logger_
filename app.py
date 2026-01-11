import os
from flask import Flask, render_template, request, redirect, url_for, flash, session
from datetime import datetime
import pyperclip
import urllib.parse

# Import Custom Modules (The new structure)
from workout_parser import workout_parser
from list_of_exercise import get_workout_days
# We import the DB engine and logic functions
from models import initialize_database, Session, UserPlan, UserRepRange
import logic

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'muscle_gains_secret')


# --- HELPERS ---

@app.template_filter('url_encode')
def url_encode_filter(s):
    return urllib.parse.quote(s)


@app.teardown_appcontext
def remove_session(exception=None):
    Session.remove()


# --- ENTRY POINTS ---

@app.route('/')
def index():
    if 'user' in session:
        return redirect(url_for(f"{session['user']}_dashboard"))
    return render_template('select_user.html')


@app.route('/switch')
def switch_user():
    session.pop('user', None)
    return redirect(url_for('index'))


@app.route('/harsh')
def harsh_dashboard():
    session['user'] = 'harsh'
    return render_template('index.html', user='Harsh')


@app.route('/apurva')
def apurva_dashboard():
    session['user'] = 'apurva'
    return render_template('index.html', user='Apurva')


# --- PLAN & SETTINGS ---

@app.route('/set_plan', methods=['GET', 'POST'])
def set_plan():
    if 'user' not in session: return redirect(url_for('index'))
    db_session = Session()
    user_plan = db_session.get(UserPlan, session['user'])

    if request.method == 'POST':
        user_plan.plan_text = request.form.get('plan_text')
        user_plan.updated_at = datetime.now()
        db_session.commit()
        flash("Plan updated successfully!", "success")
        return redirect(url_for(f"{session['user']}_dashboard"))

    return render_template('set_plan.html', current_plan=user_plan.plan_text)


@app.route('/set_exercises', methods=['GET', 'POST'])
def set_exercises():
    if 'user' not in session: return redirect(url_for('index'))
    db_session = Session()
    user_reps = db_session.get(UserRepRange, session['user'])

    if request.method == 'POST':
        user_reps.rep_text = request.form.get('rep_text')
        user_reps.updated_at = datetime.now()
        db_session.commit()
        flash("Exercise list updated successfully!", "success")
        return redirect(url_for(f"{session['user']}_dashboard"))

    return render_template('set_exercises.html', current_reps=user_reps.rep_text)


# --- CORE ROUTES ---

@app.route('/log', methods=['GET', 'POST'])
def log_workout():
    if 'user' not in session: return redirect(url_for('index'))
    if request.method == 'GET': return render_template('log.html')

    raw_text = request.form.get('workout_text')
    if not raw_text: return redirect(url_for('log_workout'))

    parsed = workout_parser(raw_text)
    if not parsed:
        flash("Could not parse workout.", "error")
        return redirect(url_for('log_workout'))

    # Delegate logic to logic.py
    db_session = Session()
    summary_data = logic.process_workout_log(db_session, session['user'], parsed)
    db_session.commit()

    return render_template('result.html', summary=summary_data, date=parsed['date'].strftime('%Y-%m-%d'))


@app.route('/retrieve/categories')
def retrieve_categories():
    if 'user' not in session: return redirect(url_for('index'))

    # Logic is cleaner: Get text -> Parse -> Show keys
    db_session = Session()
    raw_text = logic.get_current_plan_text(db_session, session['user'])
    all_plans = get_workout_days(raw_text)
    categories = list(all_plans["workout"].keys())

    return render_template('retrieve_step1.html', categories=categories)


@app.route('/retrieve/days/<category_name>')
def retrieve_days(category_name):
    if 'user' not in session: return redirect(url_for('index'))

    db_session = Session()
    raw_text = logic.get_current_plan_text(db_session, session['user'])
    all_plans = get_workout_days(raw_text)

    if category_name not in all_plans["workout"]: return "Invalid Category"

    days_dict = all_plans["workout"][category_name]
    num_days = len(days_dict)

    return render_template('retrieve_step2.html', category_name=category_name, num_days=num_days)


@app.route('/retrieve/final/<category_name>/<int:day_id>')
def retrieve_final(category_name, day_id):
    if 'user' not in session: return redirect(url_for('index'))

    # Delegate logic to logic.py
    db_session = Session()
    output_text = logic.generate_retrieve_text(db_session, session['user'], category_name, day_id)

    if output_text:
        try:
            pyperclip.copy(output_text)
        except Exception:
            pass
    else:
        output_text = f"Plan '{category_name} {day_id}' not found."

    return render_template('retrieve_step3.html', output=output_text)


if __name__ == '__main__':
    initialize_database()
    port = int(os.environ.get("PORT", 5001))
    app.run(host='0.0.0.0', port=port)