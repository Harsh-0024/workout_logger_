from models import Lift

def get_set_stats(sets):
    peak, strength_sum, volume = 0, 0, 0
    if not sets or "weights" not in sets:
        return 0, 0, 0
    weights = list(sets.get("weights") or [])
    reps = list(sets.get("reps") or [])
    if not weights or not reps:
        return 0, 0, 0
    if len(weights) != len(reps):
        if len(weights) < len(reps) and weights:
            weights = weights + [weights[-1]] * (len(reps) - len(weights))
        elif len(reps) < len(weights) and reps:
            reps = reps + [reps[-1]] * (len(weights) - len(reps))
    for w, r in zip(weights, reps):
        try:
            weight = float(w)
            reps_value = int(r)
        except (TypeError, ValueError):
            continue
        est_1rm = weight * (1 + reps_value / 30)
        if est_1rm > peak: peak = est_1rm
        strength_sum += est_1rm
        volume += weight * reps_value
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