import os
from flask import Flask, render_template, request, redirect, url_for, flash
from sqlalchemy import create_engine, Column, String, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker, scoped_session
from sqlalchemy_utils import JSONType
from datetime import datetime
import pyperclip

# Import your modules
from workout_parser import workout_parser
from list_of_exercise import get_workout_days, list_of_exercises, EXERCISE_REP_RANGES

app = Flask(__name__)
# Use an environment variable for secret key, fallback to local for dev
app.secret_key = os.environ.get('SECRET_KEY', 'muscle_gains_secret')

# --- DATABASE SETUP ---
database_url = os.environ.get('DATABASE_URL')

if database_url:
    # Fix for SQLAlchemy: Railway returns "postgres://", but we need "postgresql://"
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    engine = create_engine(database_url)
else:
    # Local fallback
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    DB_PATH = os.path.join(BASE_DIR, "workout.db")
    engine = create_engine(f"sqlite:///{DB_PATH}")

session_factory = sessionmaker(bind=engine)
Session = scoped_session(session_factory)
Base = declarative_base()


class BestLift(Base):
    __tablename__ = "best_lifts"
    exercise = Column(String, primary_key=True)
    best_string = Column(String)
    sets_json = Column(JSONType)
    updated_at = Column(DateTime, default=datetime.now)


# Create Tables
Base.metadata.create_all(engine)


def initialize_database():
    """Seeds the database if it is empty."""
    session = Session()
    try:
        # Check if database is empty
        if session.query(BestLift).first() is None:
            print("--- Seeding Database with Exercises ---")
            for ex in list_of_exercises:
                if not session.get(BestLift, ex):
                    record = BestLift(
                        exercise=ex,
                        best_string="",
                        sets_json={"weights": [], "reps": []},
                    )
                    session.add(record)
            session.commit()
            print("--- Database Initialized ---")
    except Exception as e:
        print(f"Init Error: {e}")
    finally:
        session.close()


def get_set_stats(sets):
    peak = 0
    strength_sum = 0
    volume = 0
    if not sets or "weights" not in sets or not sets["weights"]:
        return 0, 0, 0
    for w, r in zip(sets["weights"], sets["reps"]):
        est_1rm = w * (1 + r / 30)
        if est_1rm > peak: peak = est_1rm
        strength_sum += est_1rm
        volume += w * r
    return peak, strength_sum, volume


def get_fuzzy_record(session, name):
    """
    Finds a record handling:
    1. Exact Match
    2. Python .title() quirk (Farmer's -> Farmer'S)
    3. Smart Punctuation (Curly ' vs Straight ')
    4. Dashes (En-dash vs Hyphen)
    """
    if not name: return None
    name = name.strip()

    # 1. Exact Match
    rec = session.query(BestLift).get(name)
    if rec: return rec

    # 2. Try Python Title Case (Fixes "Farmer's" -> "Farmer'S" issue)
    rec = session.query(BestLift).get(name.title())
    if rec: return rec

    # 3. Try Apostrophe Swap (Straight <-> Curly)
    if "'" in name:
        rec = session.query(BestLift).get(name.replace("'", "’"))
        if rec: return rec
    if "’" in name:
        rec = session.query(BestLift).get(name.replace("’", "'"))
        if rec: return rec

    # 4. Try Dash Swap (Hyphen <-> En-dash)
    if "-" in name:
        rec = session.query(BestLift).get(name.replace("-", "–"))
        if rec: return rec
    if "–" in name:
        rec = session.query(BestLift).get(name.replace("–", "-"))
        if rec: return rec

    return None


@app.teardown_appcontext
def remove_session(exception=None):
    Session.remove()


# --- ROUTES ---

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/bgfix')
def bgfix():
    # You can add fixing code here.
    return render_template('index.html')


@app.route('/log', methods=['GET', 'POST'])
def log_workout():
    if request.method == 'GET':
        return render_template('log.html')

    raw_text = request.form.get('workout_text')
    if not raw_text:
        return redirect(url_for('log_workout'))

    parsed = workout_parser(raw_text)
    if not parsed:
        flash("Could not parse workout.", "error")
        return redirect(url_for('log_workout'))

    db_session = Session()
    summary_data = []
    workout_date = parsed['date']

    for exercise in parsed["exercises"]:
        new_sets = {"weights": exercise["weights"], "reps": exercise["reps"]}
        ex_name = exercise['name']
        new_str = exercise['exercise_string']

        # Use Fuzzy Matcher to ensure we find the record to update
        record = get_fuzzy_record(db_session, ex_name)

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
            # Create new record using the name exactly as parsed
            new_record = BestLift(exercise=ex_name, best_string=new_str, sets_json=new_sets, updated_at=workout_date)
            db_session.add(new_record)
            row['old'] = 'First Log'
            row['status'] = "NEW"
            row['class'] = 'new'

        summary_data.append(row)

    db_session.commit()
    return render_template('result.html', summary=summary_data, date=workout_date.strftime('%Y-%m-%d'))


@app.route('/retrieve/categories')
def retrieve_categories():
    return render_template('retrieve_step1.html')


@app.route('/retrieve/days/<int:category_id>')
def retrieve_days(category_id):
    return render_template('retrieve_step2.html', category_id=category_id)


@app.route('/retrieve/final/<int:category_id>/<int:day_id>')
def retrieve_final(category_id, day_id):
    category_map = {1: "Chest & Triceps", 2: "Back & Biceps", 3: "Arms", 4: "Legs"}
    workout_category = category_map.get(category_id)

    if not workout_category:
        return "Invalid Category"

    key = f"{workout_category} {day_id}"
    all_plans = get_workout_days()

    try:
        exercises = all_plans["workout"][workout_category][key]
        today = datetime.now().strftime("%d/%m")
        output_text = f"{today} {key}\n"

        db_session = Session()
        for exercise in exercises:
            # Look up Rep Range from our new Dictionary
            # We strip fuzzy chars if necessary, but key lookup should match canonical name
            rep_range = EXERCISE_REP_RANGES.get(exercise, "")
            formatted_range = f" - [{rep_range}]" if rep_range else ""

            record = get_fuzzy_record(db_session, exercise)

            if record and record.best_string:
                # Logic: If the saved string ALREADY has " - [", use it as is.
                # If it's an OLD string (no range), insert the range.
                if " - [" in record.best_string:
                    output_text += "\n" + record.best_string
                else:
                    # It's an old format: "Name Weights..."
                    # We want: "Name - [Range] - Weights..."
                    # We replace the exercise name part with "Name - [Range] -"
                    # But to be safe (case sensitivity etc), we just prepend the range to the Data part?
                    # Safer: Reconstruct it.
                    # Remove the Exercise Name from the start of the string to get just data
                    # (This assumes the stored string starts with the exercise name, which it does)
                    data_part = record.best_string[len(exercise):].strip()
                    # If it starts with a comma or something weird, clean it
                    if data_part.startswith("-"): data_part = data_part[1:].strip()

                    new_line = f"{exercise}{formatted_range} - {data_part}"
                    output_text += "\n" + new_line
            else:
                # No history? Just show Name + Range
                output_text += f"\n{exercise}{formatted_range}"

        # Server-side copy attempt (User convenience)
        try:
            pyperclip.copy(output_text)
        except Exception:
            pass

    except KeyError:
        output_text = "Plan not found."

    return render_template('retrieve_step3.html', output=output_text)


if __name__ == '__main__':
    initialize_database()
    port = int(os.environ.get("PORT", 5001))
    app.run(host='0.0.0.0', port=port)