from datetime import datetime, timedelta
from list_of_exercise import EXERCISE_REP_RANGES, get_workout_days
from models import get_user_model, UserPlan, UserRepRange


# --- HELPERS ---

def get_set_stats(sets):
    """Calculates Peak 1RM, Total Strength, and Volume."""
    peak, strength_sum, volume = 0, 0, 0
    if not sets or "weights" not in sets: return 0, 0, 0
    for w, r in zip(sets["weights"], sets["reps"]):
        est_1rm = w * (1 + r / 30)
        if est_1rm > peak: peak = est_1rm
        strength_sum += est_1rm
        volume += w * r
    return peak, strength_sum, volume


def get_fuzzy_record(db_session, model, name):
    """Finds exercise record handling exact matches, title case, quotes, and dashes."""
    if not name: return None
    name = name.strip()

    candidates = [
        name,
        name.title(),
        name.replace("'", "’"),
        name.replace("'", "’").title(),
        name.replace("’", "'"),
        name.replace("’", "'").title(),
        name.replace("-", "–"),
        name.replace("–", "-")
    ]

    # Remove duplicates while preserving order
    unique_candidates = list(dict.fromkeys(candidates))

    first_match = None
    for candidate in unique_candidates:
        rec = db_session.query(model).get(candidate)
        if rec:
            if not first_match: first_match = rec
            # Prioritize record with data
            if rec.best_string and rec.best_string.strip():
                return rec

    return first_match


def get_current_plan_text(db_session, user):
    plan_record = db_session.get(UserPlan, user)
    return plan_record.plan_text if plan_record else ""


def get_current_rep_ranges_dict(db_session, user):
    range_record = db_session.get(UserRepRange, user)
    rep_dict = {}
    if range_record and range_record.rep_text:
        for line in range_record.rep_text.strip().split('\n'):
            line = line.strip()
            if ':' in line:
                parts = line.split(':', 1)
                rep_dict[parts[0].strip()] = parts[1].strip()
    return rep_dict


# --- CORE LOGIC ---

def process_workout_log(db_session, user, parsed_data):
    """
    Takes parsed workout data, compares it to DB, updates DB,
    and returns the summary list for the UI.
    """
    workout_date = parsed_data['date']
    UserModel = get_user_model(user)
    summary_data = []

    for exercise in parsed_data["exercises"]:
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

    return summary_data


def generate_retrieve_text(db_session, user, category_name, day_id):
    """Generates the copy-paste string for the Retrieve page."""
    key = f"{category_name} {day_id}"
    raw_text = get_current_plan_text(db_session, user)
    all_plans = get_workout_days(raw_text)
    custom_ranges = get_current_rep_ranges_dict(db_session, user)

    # Date Fix (IST Offset)
    ist_offset = timedelta(hours=5, minutes=30)
    today = (datetime.utcnow() + ist_offset).strftime("%d/%m")

    output_text = f"{today} {key}\n"

    try:
        exercises = all_plans["workout"][category_name][key]
        UserModel = get_user_model(user)

        for exercise in exercises:
            # Inject Rep Range
            rep_range = custom_ranges.get(exercise, "")
            formatted_range = f" - [{rep_range}]" if rep_range else ""

            # Fetch Previous Best
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

        return output_text

    except KeyError:
        return None