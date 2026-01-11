from models import Lift

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

    # Prioritize record with data
    for m in matches:
        if m.best_string and m.best_string.strip():
            return m
    return matches[0]