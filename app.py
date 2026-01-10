import os
from flask import Flask, render_template, request, redirect, url_for, flash, session
from sqlalchemy import create_engine, Column, String, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker, scoped_session
from sqlalchemy_utils import JSONType
from datetime import datetime
import pyperclip
import urllib.parse

# Import your modules
from workout_parser import workout_parser
from list_of_exercise import get_workout_days, list_of_exercises, EXERCISE_REP_RANGES

app = Flask(__name__)
# Use an environment variable for secret key
app.secret_key = os.environ.get('SECRET_KEY', 'muscle_gains_secret')

# --- DATABASE SETUP ---
database_url = os.environ.get('DATABASE_URL')

if database_url:
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    engine = create_engine(database_url)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    DB_PATH = os.path.join(BASE_DIR, "workout.db")
    engine = create_engine(f"sqlite:///{DB_PATH}")

session_factory = sessionmaker(bind=engine)
Session = scoped_session(session_factory)
Base = declarative_base()


# --- DB MODELS ---

class LiftMixin:
    exercise = Column(String, primary_key=True)
    best_string = Column(String)
    sets_json = Column(JSONType)
    updated_at = Column(DateTime, default=datetime.now)


class HarshLift(Base, LiftMixin):
    __tablename__ = "harsh_lifts"


class ApurvaLift(Base, LiftMixin):
    __tablename__ = "apurva_lifts"


Base.metadata.create_all(engine)


# --- HELPERS ---

def get_user_model():
    """Returns the correct DB Class based on the logged-in user."""
    user = session.get('user')
    if user == 'apurva':
        return ApurvaLift
    return HarshLift


def initialize_database():
    db_session = Session()
    try:
        if db_session.query(HarshLift).first() is None:
            print("--- Seeding Harsh's Table ---")
            for ex in list_of_exercises:
                if not db_session.get(HarshLift, ex):
                    db_session.add(HarshLift(exercise=ex, best_string="", sets_json={"weights": [], "reps": []}))

        if db_session.query(ApurvaLift).first() is None:
            print("--- Seeding Apurva's Table ---")
            for ex in list_of_exercises:
                if not db_session.get(ApurvaLift, ex):
                    db_session.add(ApurvaLift(exercise=ex, best_string="", sets_json={"weights": [], "reps": []}))

        db_session.commit()
    except Exception as e:
        print(f"Init Error: {e}")
    finally:
        db_session.close()


def get_set_stats(sets):
    peak, strength_sum, volume = 0, 0, 0
    if not sets or "weights" not in sets: return 0, 0, 0
    for w, r in zip(sets["weights"], sets["reps"]):
        est_1rm = w * (1 + r / 30)
        if est_1rm > peak: peak = est_1rm
        strength_sum += est_1rm
        volume += w * r
    return peak, strength_sum, volume


def get_fuzzy_record(db_session, model, name):
    if not name: return None
    name = name.strip()
    rec = db_session.query(model).get(name)
    if rec: return rec
    rec = db_session.query(model).get(name.title())
    if rec: return rec
    if "'" in name:
        rec = db_session.query(model).get(name.replace("'", "’"))
        if rec: return rec
    if "’" in name:
        rec = db_session.query(model).get(name.replace("’", "'"))
        if rec: return rec
    if "-" in name:
        rec = db_session.query(model).get(name.replace("-", "–"))
        if rec: return rec
    if "–" in name:
        rec = db_session.query(model).get(name.replace("–", "-"))
        if rec: return rec
    return None


@app.template_filter('url_encode')
def url_encode_filter(s):
    return urllib.parse.quote(s)


@app.teardown_appcontext
def remove_session(exception=None):
    Session.remove()


# --- SMART ROUTES ---

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


# --- FUNCTIONAL ROUTES ---

@app.route('/log', methods=['GET', 'POST'])
def log_workout():
    if 'user' not in session: return redirect(url_for('index'))

    if request.method == 'GET':
        return render_template('log.html')

    raw_text = request.form.get('workout_text')
    if not raw_text: return redirect(url_for('log_workout'))

    parsed = workout_parser(raw_text)
    if not parsed:
        flash("Could not parse workout.", "error")
        return redirect(url_for('log_workout'))

    db_session = Session()
    summary_data = []
    workout_date = parsed['date']
    UserModel = get_user_model()

    for exercise in parsed["exercises"]:
        new_sets = {"weights": exercise["weights"], "reps": exercise["reps"]}
        ex_name = exercise['name']
        new_str = exercise['exercise_string']

        record = get_fuzzy_record(db_session, UserModel, ex_name)
        row = {'name': ex_name, 'old': '-', 'new': new_str, 'status': '-', 'class': 'neutral'}

        if record:
            row['old'] = record.best_string
            p_peak, p_sum, p_vol = get_set_stats(new_sets)
            r_peak, r_sum, r_vol = get_set_stats(record.sets_json)

            improvement = None
            if p_peak > r_peak:
                diff = p_peak - r_peak
                improvement = f"PEAK (+{diff:.1f})"
            elif p_peak == r_peak:
                if p_sum > r_sum:
                    improvement = "CONSISTENCY"
                elif p_vol > r_vol:
                    improvement = "VOLUME"

            if improvement:
                record.sets_json = new_sets
                record.best_string = new_str
                record.updated_at = workout_date
                row['status'] = improvement
                row['class'] = 'improved'
        else:
            new_record = UserModel(exercise=ex_name, best_string=new_str, sets_json=new_sets, updated_at=workout_date)
            db_session.add(new_record)
            row['old'] = 'First Log'
            row['status'] = "NEW"
            row['class'] = 'new'

        summary_data.append(row)

    db_session.commit()
    return render_template('result.html', summary=summary_data, date=workout_date.strftime('%Y-%m-%d'))


# --- DYNAMIC RETRIEVE ROUTES ---

@app.route('/retrieve/categories')
def retrieve_categories():
    if 'user' not in session: return redirect(url_for('index'))

    # DYNAMIC: Fetch plan and get list of keys (e.g., "Push", "Pull")
    all_plans = get_workout_days(session['user'])
    categories = list(all_plans["workout"].keys())

    return render_template('retrieve_step1.html', categories=categories)


@app.route('/retrieve/days/<category_name>')
def retrieve_days(category_name):
    if 'user' not in session: return redirect(url_for('index'))

    all_plans = get_workout_days(session['user'])
    if category_name not in all_plans["workout"]:
        return "Invalid Category"

    # DYNAMIC: Count how many variations exist (Push 1, Push 2...)
    # We find all keys that start with the category name
    # Actually, our parser stores them as a dict under the category key.
    # e.g. all_plans["workout"]["Chest"]["Chest 1"]

    days_dict = all_plans["workout"][category_name]
    num_days = len(days_dict)

    return render_template('retrieve_step2.html', category_name=category_name, num_days=num_days)


@app.route('/retrieve/final/<category_name>/<int:day_id>')
def retrieve_final(category_name, day_id):
    if 'user' not in session: return redirect(url_for('index'))

    # Construct key dynamically: "Chest 1"
    key = f"{category_name} {day_id}"
    all_plans = get_workout_days(session['user'])

    try:
        # Access safely
        exercises = all_plans["workout"][category_name][key]
        today = datetime.now().strftime("%d/%m")
        output_text = f"{today} {key}\n"

        db_session = Session()
        UserModel = get_user_model()

        for exercise in exercises:
            rep_range = EXERCISE_REP_RANGES.get(exercise, "")
            formatted_range = f" - [{rep_range}]" if rep_range else ""
            record = get_fuzzy_record(db_session, UserModel, exercise)

            if record and record.best_string:
                if " - [" in record.best_string:
                    output_text += "\n" + record.best_string
                else:
                    data_part = record.best_string[len(exercise):].strip()
                    if data_part.startswith("-"): data_part = data_part[1:].strip()
                    output_text += f"\n{exercise}{formatted_range} - {data_part}"
            else:
                output_text += f"\n{exercise}{formatted_range}"

        try:
            pyperclip.copy(output_text)
        except Exception:
            pass

    except KeyError:
        output_text = f"Plan '{key}' not found for {session['user'].title()}."

    return render_template('retrieve_step3.html', output=output_text)


if __name__ == '__main__':
    initialize_database()
    port = int(os.environ.get("PORT", 5001))
    app.run(host='0.0.0.0', port=port)