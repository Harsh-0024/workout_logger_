import os
from flask import Flask, render_template, request, redirect, url_for, flash, session
from sqlalchemy import create_engine, Column, String, DateTime, Text
from sqlalchemy.orm import declarative_base, sessionmaker, scoped_session
from sqlalchemy_utils import JSONType
from datetime import datetime, timedelta
import pyperclip
import urllib.parse

# Custom Module Imports
from workout_parser import workout_parser
from list_of_exercise import get_workout_days, list_of_exercises, EXERCISE_REP_RANGES, HARSH_DEFAULT_PLAN, \
    APURVA_DEFAULT_PLAN

app = Flask(__name__)
# SECRET_KEY is required for session management (cookies)
app.secret_key = os.environ.get('SECRET_KEY', 'muscle_gains_secret')

# ==========================================
# 1. DATABASE SETUP
# ==========================================
database_url = os.environ.get('DATABASE_URL')

if database_url:
    # Railway-specific fix: Ensure protocol is postgresql://
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    engine = create_engine(database_url)
else:
    # Local Development: Use SQLite file 'workout.db'
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    DB_PATH = os.path.join(BASE_DIR, "workout.db")
    engine = create_engine(f"sqlite:///{DB_PATH}")

session_factory = sessionmaker(bind=engine)
Session = scoped_session(session_factory)
Base = declarative_base()


# ==========================================
# 2. DATABASE MODELS
# ==========================================

# Mixin: Defines common columns used by both users
class LiftMixin:
    exercise = Column(String, primary_key=True)  # e.g., "Bench Press"
    best_string = Column(String)  # e.g., "Bench Press - 100 80 60, 10 10 10"
    sets_json = Column(JSONType)  # Structured data for stats
    updated_at = Column(DateTime, default=datetime.now)


# Harsh's Table
class HarshLift(Base, LiftMixin):
    __tablename__ = "harsh_lifts"


# Apurva's Table
class ApurvaLift(Base, LiftMixin):
    __tablename__ = "apurva_lifts"


# Plan Table: Stores the editable text plan for each user
class UserPlan(Base):
    __tablename__ = "user_plans"
    username = Column(String, primary_key=True)  # 'harsh' or 'apurva'
    plan_text = Column(Text)  # Stores the full raw text plan
    updated_at = Column(DateTime, default=datetime.now)


# Create tables if they don't exist
Base.metadata.create_all(engine)


# ==========================================
# 3. HELPER FUNCTIONS
# ==========================================

def get_user_model():
    """Returns the correct SQL Table Class based on the logged-in user."""
    user = session.get('user')
    if user == 'apurva': return ApurvaLift
    return HarshLift


def initialize_database():
    """Runs on startup. Seeds DB with default exercises and plans if empty."""
    db_session = Session()
    try:
        # Seed Exercises for Harsh
        if db_session.query(HarshLift).first() is None:
            for ex in list_of_exercises:
                if not db_session.get(HarshLift, ex):
                    db_session.add(HarshLift(exercise=ex, best_string="", sets_json={"weights": [], "reps": []}))

        # Seed Exercises for Apurva
        if db_session.query(ApurvaLift).first() is None:
            for ex in list_of_exercises:
                if not db_session.get(ApurvaLift, ex):
                    db_session.add(ApurvaLift(exercise=ex, best_string="", sets_json={"weights": [], "reps": []}))

        # Seed Default Plans Text
        if not db_session.get(UserPlan, 'harsh'):
            db_session.add(UserPlan(username='harsh', plan_text=HARSH_DEFAULT_PLAN))

        if not db_session.get(UserPlan, 'apurva'):
            db_session.add(UserPlan(username='apurva', plan_text=APURVA_DEFAULT_PLAN))

        db_session.commit()
    except Exception as e:
        print(f"Init Error: {e}")
    finally:
        db_session.close()


def get_current_plan_text():
    """Helper to fetch the plan text string for the active user."""
    user = session.get('user', 'harsh')
    db_session = Session()
    plan_record = db_session.get(UserPlan, user)
    if plan_record:
        return plan_record.plan_text
    return ""


def get_set_stats(sets):
    """Calculates Peak 1RM, Total Strength, and Volume from a set."""
    peak, strength_sum, volume = 0, 0, 0
    if not sets or "weights" not in sets: return 0, 0, 0
    for w, r in zip(sets["weights"], sets["reps"]):
        est_1rm = w * (1 + r / 30)
        if est_1rm > peak: peak = est_1rm
        strength_sum += est_1rm
        volume += w * r
    return peak, strength_sum, volume


def get_fuzzy_record(db_session, model, name):
    """
    Intelligent Search: Finds exercise record even with typos/formatting diffs.
    Checks: Exact match -> Title Case -> Smart Quotes -> Dash/Hyphen variations.
    """
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


# Custom Filter for templates to handle URL spaces
@app.template_filter('url_encode')
def url_encode_filter(s):
    return urllib.parse.quote(s)


@app.teardown_appcontext
def remove_session(exception=None):
    """Closes DB session automatically after every request."""
    Session.remove()


# ==========================================
# 4. ENTRY POINTS & AUTH ROUTES
# ==========================================

@app.route('/')
def index():
    """
    Root URL logic:
    - If user cookie exists -> Go straight to Dashboard.
    - If no cookie -> Show Profile Selector.
    """
    if 'user' in session:
        return redirect(url_for(f"{session['user']}_dashboard"))
    return render_template('select_user.html')


@app.route('/switch')
def switch_user():
    """Logout Logic: Clears session and returns to Selector."""
    session.pop('user', None)
    return redirect(url_for('index'))


@app.route('/harsh')
def harsh_dashboard():
    """Direct Login URL for Harsh"""
    session['user'] = 'harsh'
    return render_template('index.html', user='Harsh')


@app.route('/apurva')
def apurva_dashboard():
    """Direct Login URL for Apurva"""
    session['user'] = 'apurva'
    return render_template('index.html', user='Apurva')


# ==========================================
# 5. PLAN EDITING ROUTE
# ==========================================

@app.route('/set_plan', methods=['GET', 'POST'])
def set_plan():
    """
    Page to View/Edit the raw text plan.
    Accessed via hidden button or direct URL.
    """
    if 'user' not in session: return redirect(url_for('index'))

    db_session = Session()
    user_plan = db_session.get(UserPlan, session['user'])

    if request.method == 'POST':
        # Save new text to DB
        new_text = request.form.get('plan_text')
        if user_plan:
            user_plan.plan_text = new_text
            user_plan.updated_at = datetime.now()
            db_session.commit()
            flash("Plan updated successfully!", "success")
            return redirect(url_for(f"{session['user']}_dashboard"))

    return render_template('set_plan.html', current_plan=user_plan.plan_text)


# ==========================================
# 6. LOGGING & RETRIEVAL ROUTES
# ==========================================

@app.route('/log', methods=['GET', 'POST'])
def log_workout():
    """Handles pasting and processing workout logs."""
    if 'user' not in session: return redirect(url_for('index'))
    if request.method == 'GET': return render_template('log.html')

    raw_text = request.form.get('workout_text')
    if not raw_text: return redirect(url_for('log_workout'))

    parsed = workout_parser(raw_text)
    if not parsed:
        flash("Could not parse workout.", "error")
        return redirect(url_for('log_workout'))

    db_session = Session()
    workout_date = parsed['date']
    UserModel = get_user_model()  # Get correct table
    summary_data = []

    for exercise in parsed["exercises"]:
        new_sets = {"weights": exercise["weights"], "reps": exercise["reps"]}
        ex_name = exercise['name']
        new_str = exercise['exercise_string']

        # Find existing record
        record = get_fuzzy_record(db_session, UserModel, ex_name)
        row = {'name': ex_name, 'old': '-', 'new': new_str, 'status': '-', 'class': 'neutral'}

        if record:
            row['old'] = record.best_string
            # Compare Stats
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

            # Update if improved
            if improvement:
                record.sets_json = new_sets
                record.best_string = new_str
                record.updated_at = workout_date
                row['status'] = improvement
                row['class'] = 'improved'
        else:
            # Create New Record
            new_record = UserModel(exercise=ex_name, best_string=new_str, sets_json=new_sets, updated_at=workout_date)
            db_session.add(new_record)
            row['old'] = 'First Log'
            row['status'] = "NEW"
            row['class'] = 'new'
        summary_data.append(row)

    db_session.commit()
    return render_template('result.html', summary=summary_data, date=workout_date.strftime('%Y-%m-%d'))


@app.route('/retrieve/categories')
def retrieve_categories():
    """Step 1: Show list of Categories (Dynamic from DB text)."""
    if 'user' not in session: return redirect(url_for('index'))

    # 1. Get Text from DB
    raw_text = get_current_plan_text()
    # 2. Parse text into structure
    all_plans = get_workout_days(raw_text)
    # 3. Extract keys (Chest, Back, etc.)
    categories = list(all_plans["workout"].keys())
    return render_template('retrieve_step1.html', categories=categories)


@app.route('/retrieve/days/<category_name>')
def retrieve_days(category_name):
    """Step 2: Show available days for selected Category."""
    if 'user' not in session: return redirect(url_for('index'))

    raw_text = get_current_plan_text()
    all_plans = get_workout_days(raw_text)

    if category_name not in all_plans["workout"]: return "Invalid Category"

    # Count how many days exist in this category
    days_dict = all_plans["workout"][category_name]
    num_days = len(days_dict)

    return render_template('retrieve_step2.html', category_name=category_name, num_days=num_days)


@app.route('/retrieve/final/<category_name>/<int:day_id>')
def retrieve_final(category_name, day_id):
    """Step 3: Generate the copy-pasteable workout text."""
    if 'user' not in session: return redirect(url_for('index'))

    key = f"{category_name} {day_id}"

    raw_text = get_current_plan_text()
    all_plans = get_workout_days(raw_text)

    try:
        exercises = all_plans["workout"][category_name][key]

        # --- DATE FIX: IST Offset (UTC + 5:30) ---
        # Forces the server to display India time regardless of where it is hosted.
        ist_offset = timedelta(hours=5, minutes=30)
        today = (datetime.utcnow() + ist_offset).strftime("%d/%m")

        output_text = f"{today} {key}\n"

        db_session = Session()
        UserModel = get_user_model()

        for exercise in exercises:
            # Inject Rep Range Guidelines
            rep_range = EXERCISE_REP_RANGES.get(exercise, "")
            formatted_range = f" - [{rep_range}]" if rep_range else ""

            # Fetch Previous Best
            record = get_fuzzy_record(db_session, UserModel, exercise)

            if record and record.best_string:
                # If history exists, format appropriately
                if " - [" in record.best_string:
                    output_text += "\n" + record.best_string
                else:
                    data_part = record.best_string[len(exercise):].strip()
                    if data_part.startswith("-"): data_part = data_part[1:].strip()
                    output_text += f"\n{exercise}{formatted_range} - {data_part}"
            else:
                # No history, just show name + range
                output_text += f"\n{exercise}{formatted_range}"

        try:
            pyperclip.copy(output_text)
        except Exception:
            pass

    except KeyError:
        output_text = f"Plan '{key}' not found."

    return render_template('retrieve_step3.html', output=output_text)


if __name__ == '__main__':
    initialize_database()
    port = int(os.environ.get("PORT", 5001))
    app.run(host='0.0.0.0', port=port)