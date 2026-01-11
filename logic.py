from datetime import datetime, timedelta
from list_of_exercise import get_workout_days
from models import Lift, Plan, RepRange, User


def get_set_stats(sets):
    peak, strength_sum, volume = 0, 0, 0
    if not sets or "weights" not in sets: return 0, 0, 0
    for w, r in zip(sets["weights"], sets["reps"]):
        est_1rm = w * (1 + r / 30)
        if est_1rm > peak: peak = est_1rm
        strength_sum += est_1rm
        volume += w * r
    return peak, strength_sum, volume


def find_best_match(db_session, user_id, exercise_name):
    if not exercise_name: return None
    name = exercise_name.strip()

    candidates = [
        name, name.title(),
        name.replace("'", "’"), name.replace("'", "’").title(),
        name.replace("’", "'"), name.replace("’", "'").title(),
        name.replace("-", "–"), name.replace("–", "-")
    ]
    candidates = list(dict.fromkeys(candidates))

    matches = db_session.query(Lift).filter(
        Lift.user_id == user_id,
        Lift.exercise.in_(candidates)
    ).all()

    if not matches: return None

    for m in matches:
        if m.best_string and m.best_string.strip():
            return m
    return matches[0]


def handle_workout_log(db_session, user, parsed_data):
    summary = []
    workout_date = parsed_data['date']

    for item in parsed_data["exercises"]:
        ex_name = item['name']
        new_sets = {"weights": item["weights"], "reps": item["reps"]}
        new_str = item['exercise_string']

        record = find_best_match(db_session, user.id, ex_name)
        row = {'name': ex_name, 'old': '-', 'new': new_str, 'status': '-', 'class': 'neutral'}

        if record:
            row['old'] = record.best_string
            p_peak, _, p_vol = get_set_stats(new_sets)
            r_peak, r_sum, r_vol = get_set_stats(record.sets_json)

            improvement = None
            if p_peak > r_peak:
                diff = p_peak - r_peak
                improvement = f"PEAK (+{diff:.1f})"
            elif p_peak == r_peak:
                _, p_sum, _ = get_set_stats(new_sets)
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
            new_rec = Lift(user_id=user.id, exercise=ex_name, best_string=new_str, sets_json=new_sets,
                           updated_at=workout_date)
            db_session.add(new_rec)
            row['old'] = 'First Log'
            row['status'] = "NEW"
            row['class'] = 'new'

        summary.append(row)
    return summary


def generate_retrieve_output(db_session, user, category, day_id):
    key = f"{category} {day_id}"
    plan_row = db_session.query(Plan).filter_by(user_id=user.id).first()
    if not plan_row: return "Plan not found."

    all_plans = get_workout_days(plan_row.text_content)

    rep_row = db_session.query(RepRange).filter_by(user_id=user.id).first()
    custom_ranges = {}
    if rep_row and rep_row.text_content:
        for line in rep_row.text_content.split('\n'):
            if ':' in line:
                k, v = line.split(':', 1)
                custom_ranges[k.strip()] = v.strip()

    ist_offset = timedelta(hours=5, minutes=30)
    today_str = (datetime.utcnow() + ist_offset).strftime("%d/%m")

    output = f"{today_str} {key}\n"

    try:
        exercises = all_plans["workout"][category][key]
        for ex in exercises:
            rng = custom_ranges.get(ex, "")
            fmt_rng = f" - [{rng}]" if rng else ""
            rec = find_best_match(db_session, user.id, ex)

            if rec and rec.best_string:
                if " - [" in rec.best_string:
                    output += "\n" + rec.best_string
                else:
                    data = rec.best_string[len(ex):].strip()
                    if data.startswith("-"): data = data[1:].strip()
                    output += f"\n{ex}{fmt_rng} - {data}"
            else:
                output += f"\n{ex}{fmt_rng}"
        return output
    except KeyError:
        return f"Day '{key}' not found in plan."