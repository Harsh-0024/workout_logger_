from datetime import datetime, timedelta
from models import Plan, RepRange
from list_of_exercise import get_workout_days
from services.helpers import find_best_match


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