import os
from flask import Flask, render_template, request, redirect, url_for, flash
from sqlalchemy import create_engine, Column, String, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker, scoped_session
from sqlalchemy_utils import JSONType
from datetime import datetime
import pyperclip

# Import your modules
from workout_parser import workout_parser
from list_of_exercise import get_workout_days, list_of_exercises

# ... imports remain the same ...

app = Flask(__name__)
# Use an environment variable for secret key, fallback to local for dev
app.secret_key = os.environ.get('SECRET_KEY', 'muscle_gains_secret')

# --- DATABASE SETUP ---
# Check if we are on Railway (DATABASE_URL will exist)
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


@app.teardown_appcontext
def remove_session(exception=None):
    Session.remove()


# --- ROUTES ---

@app.route('/')
def index():
    return render_template('index.html')


@app.route("/debug/env")
def debug_env():
    return {
        "DATABASE_URL_exists": bool(os.environ.get("DATABASE_URL")),
        "PGHOST": os.environ.get("PGHOST"),
        "PGDATABASE": os.environ.get("PGDATABASE"),
    }

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

        record = db_session.query(BestLift).get(ex_name)

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
def     retrieve_final(category_id, day_id):
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
            record = db_session.query(BestLift).get(exercise)
            if record and record.best_string:
                output_text += "\n" + record.best_string
            else:
                output_text += "\n" + exercise

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
    # Railway provides a PORT variable. Localhost uses 5001.
    port = int(os.environ.get("PORT", 5001))
    app.run(host='0.0.0.0', port=port)
