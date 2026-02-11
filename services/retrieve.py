from datetime import datetime, timedelta
import re

from models import Plan, RepRange, User, UserRole, WorkoutLog
from list_of_exercise import BW_EXERCISES, DEFAULT_PLAN, DEFAULT_REP_RANGES, get_workout_days
from parsers.workout import align_sets
from services.workout_quality import WorkoutQualityScorer


def _normalize_text(text: str) -> str:
    return "\n".join(line.rstrip() for line in (text or "").strip().splitlines())


def _build_default_rep_text() -> str:
    return "\n".join(f"{ex}: {rng}" for ex, rng in DEFAULT_REP_RANGES.items())


def _get_admin_user(db_session):
    return (
        db_session.query(User)
        .filter(User.role == UserRole.ADMIN)
        .order_by(User.id.asc())
        .first()
    )


def get_effective_plan_text(db_session, user) -> str:
    plan_row = db_session.query(Plan).filter_by(user_id=user.id).first()
    user_text = plan_row.text_content if plan_row else ""
    default_text = DEFAULT_PLAN or ""

    if user.is_admin():
        return user_text or default_text

    admin_user = _get_admin_user(db_session)
    admin_text = ""
    if admin_user:
        admin_plan = db_session.query(Plan).filter_by(user_id=admin_user.id).first()
        admin_text = admin_plan.text_content if admin_plan else ""

    if not user_text:
        return admin_text or default_text

    if _normalize_text(user_text) == _normalize_text(default_text):
        return admin_text or user_text

    return user_text


def get_effective_rep_ranges_text(db_session, user) -> str:
    rep_row = db_session.query(RepRange).filter_by(user_id=user.id).first()
    user_text = rep_row.text_content if rep_row else ""
    default_text = _build_default_rep_text()

    if user.is_admin():
        return user_text or default_text

    admin_user = _get_admin_user(db_session)
    admin_text = ""
    if admin_user:
        admin_rep = db_session.query(RepRange).filter_by(user_id=admin_user.id).first()
        admin_text = admin_rep.text_content if admin_rep else ""

    if not user_text:
        return admin_text or default_text

    if _normalize_text(user_text) == _normalize_text(default_text):
        return admin_text or user_text

    return user_text


def generate_retrieve_output(db_session, user, category, day_id):
    day_key = f"{category} {day_id}"
    plan_text = get_effective_plan_text(db_session, user)
    if not plan_text:
        return "Plan not found.", 0, 0

    all_plans = get_workout_days(plan_text)

    rep_text = get_effective_rep_ranges_text(db_session, user)
    custom_ranges = {}
    custom_sets = {}
    if rep_text:
        for line in rep_text.split('\n'):
            if ':' in line:
                k, v = line.split(':', 1)
                exercise_key = k.strip().lower()
                value = v.strip()
                m = re.match(r'^(\d+)\s*,\s*(.+)$', value)
                if m:
                    try:
                        custom_sets[exercise_key] = int(m.group(1))
                    except Exception:
                        custom_sets[exercise_key] = None
                    custom_ranges[exercise_key] = m.group(2).strip()
                else:
                    custom_ranges[exercise_key] = value

    ist_offset = timedelta(hours=5, minutes=30)
    today_str = (datetime.utcnow() + ist_offset).strftime("%d/%m")
    header_line = f"{today_str} - {day_key}"
    if str(category).strip().lower() == "session":
        titles = all_plans.get("session_titles") if isinstance(all_plans, dict) else None
        if isinstance(titles, dict):
            session_title = titles.get(str(day_id))
            if session_title:
                header_line = f"{today_str} - Session {day_id} - {session_title}"
    output_lines = [header_line, ""]

    try:
        exercises = all_plans["workout"][category][day_key]
        exercise_count = len(exercises)
        set_count = 0
        
        for ex in exercises:
            ex_key = ex.lower()
            rng = custom_ranges.get(ex_key, "")
            declared_sets = custom_sets.get(ex_key)
            fmt_rng = ""
            if rng and declared_sets:
                fmt_rng = f" - [{declared_sets}, {rng}]"
            elif rng:
                fmt_rng = f" - [{rng}]"

            target_sets = int(declared_sets) if isinstance(declared_sets, int) and declared_sets > 0 else 3

            target_rep_range = _parse_rep_range(rng)

            sets_line = _build_best_sets_line_from_logs(
                db_session,
                user,
                ex,
                target_sets=target_sets,
                target_rep_range=target_rep_range,
            )

            if ex in BW_EXERCISES:
                if not sets_line:
                    sets_line = "bw/4, 1"
                sets_line = _normalize_bw(sets_line)
            elif not sets_line:
                sets_line = "1, 1"

            # Count sets from sets_line
            if sets_line:
                set_count += _count_sets_from_line(sets_line, target_sets=target_sets)

            output_lines.append(f"{ex}{fmt_rng}")
            if sets_line:
                output_lines.append(sets_line)
            output_lines.append("")

        return "\n".join(output_lines).rstrip(), exercise_count, set_count
    except KeyError:
        return f"Day '{day_key}' not found in plan.", 0, 0


def _exercise_candidates(exercise_name: str):
    if not exercise_name:
        return []
    name = exercise_name.strip()
    candidates = [
        name,
        name.title(),
        name.replace("'", "’"),
        name.replace("'", "’").title(),
        name.replace("’", "'"),
        name.replace("’", "'").title(),
        name.replace("-", "–"),
        name.replace("–", "-"),
    ]
    return list(dict.fromkeys(candidates))


def _parse_rep_range(value: str):
    if not value:
        return None
    nums = re.findall(r"\d+", str(value))
    if len(nums) >= 2:
        return int(nums[0]), int(nums[1])
    if len(nums) == 1:
        n = int(nums[0])
        return n, n
    return None


def _compress_shorthand_values(values):
    if not values:
        return []
    if len(values) == 1:
        return list(values)
    if all(v == values[0] for v in values):
        return [values[0]]
    if all(v == values[1] for v in values[1:]) and values[0] != values[1]:
        return [values[0], values[1]]
    return list(values)


def _format_weight_token(exercise: str, weight, bodyweight, *, force_bw: bool = False):
    token = _format_value(weight)
    if exercise not in BW_EXERCISES:
        return token
    if force_bw:
        return 'bw'
    if bodyweight is None:
        return token
    try:
        bw = float(bodyweight)
    except Exception:
        return token

    candidates = [
        ("bw", bw),
        ("bw/2", bw / 2.0 if bw else 0.0),
        ("bw/4", bw / 4.0 if bw else 0.0),
    ]
    for label, value in candidates:
        if not value:
            continue
        if abs(float(weight) - value) <= abs(value) * 0.02:
            return label
    return token


def _build_best_sets_line_from_logs(
    db_session,
    user,
    exercise: str,
    target_sets: int = 3,
    target_rep_range=None,
) -> str:
    candidates = _exercise_candidates(exercise)
    if not candidates:
        return ""

    logs = (
        db_session.query(WorkoutLog)
        .filter(WorkoutLog.user_id == user.id)
        .filter(WorkoutLog.exercise.in_(candidates))
        .order_by(WorkoutLog.date.desc())
        .all()
    )
    if not logs:
        return ""

    best_log = None
    best_score = None

    for log in logs:
        sets_json = log.sets_json if isinstance(log.sets_json, dict) else None
        if not sets_json:
            continue
        weights = sets_json.get("weights") or []
        reps = sets_json.get("reps") or []
        if not weights and not reps:
            continue

        try:
            weights = [float(w) for w in weights]
            reps = [int(r) for r in reps]
        except Exception:
            continue

        weights, reps = align_sets(weights, reps, target_sets=target_sets)

        quality = WorkoutQualityScorer.calculate_workout_score(
            {"weights": weights, "reps": reps},
            target_rep_range,
        )
        q_index = float(quality.get("quality_index") or 0.0)
        avg_1rm = float(quality.get("avg_1rm") or 0.0)
        score = avg_1rm * q_index
        if score <= 0:
            continue

        if best_score is None or score > best_score or (
            score == best_score and log.date > getattr(best_log, 'date', log.date)
        ):
            best_score = score
            best_log = log

    if not best_log:
        return ""

    use_bw_format = False
    if best_log.exercise_string and "bw" in best_log.exercise_string.lower():
        extracted = _extract_sets_line(best_log.exercise_string, best_log.exercise)
        if extracted:
            return _normalize_bw(extracted)
        use_bw_format = True

    sets_json = best_log.sets_json if isinstance(best_log.sets_json, dict) else None
    if not sets_json:
        return ""
    weights = sets_json.get("weights") or []
    reps = sets_json.get("reps") or []

    try:
        weights = [float(w) for w in weights]
        reps = [int(r) for r in reps]
    except Exception:
        return ""

    weights, reps = align_sets(weights, reps, target_sets=target_sets)

    scored = []
    for w, r in zip(weights, reps):
        if w is None or r is None:
            continue
        if r <= 0:
            continue
        est = float(w) * (1.0 + float(r) / 30.0)
        scored.append((est, float(w), int(r)))

    if not scored:
        return ""

    scored.sort(key=lambda t: (t[0], t[1], t[2]), reverse=True)
    top = scored[: max(3, int(target_sets) if target_sets else 3)]
    weights_top = [t[1] for t in top]
    reps_top = [t[2] for t in top]
    weights_top, reps_top = align_sets(weights_top, reps_top, target_sets=target_sets)
    if target_sets:
        weights_top = weights_top[: int(target_sets)]
        reps_top = reps_top[: int(target_sets)]

    log_bodyweight = getattr(best_log, "bodyweight", None)
    weight_tokens = [
        _format_weight_token(
            exercise,
            w,
            log_bodyweight or getattr(user, "bodyweight", None),
            force_bw=use_bw_format,
        )
        for w in weights_top
    ]
    rep_tokens = [str(int(r)) for r in reps_top]

    weight_tokens = _compress_shorthand_values(weight_tokens)
    rep_tokens = _compress_shorthand_values(rep_tokens)

    weights_part = " ".join(weight_tokens).strip()
    reps_part = " ".join(rep_tokens).strip()
    if not weights_part or not reps_part:
        return ""
    return f"{weights_part}, {reps_part}"


def _format_value(value):
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _format_sets_json(sets_json):
    if not sets_json or not isinstance(sets_json, dict):
        return ""
    weights = sets_json.get("weights") or []
    reps = sets_json.get("reps") or []
    if not weights and not reps:
        return ""
    weights_line = " ".join(_format_value(w) for w in weights) if weights else ""
    reps_line = " ".join(str(int(r)) if isinstance(r, (int, float)) else str(r) for r in reps) if reps else ""
    if weights_line and reps_line:
        return f"{weights_line}, {reps_line}"
    return weights_line or reps_line


def _is_default_numeric_sets(value: str) -> bool:
    normalized = (value or "").strip().lower().replace("  ", " ")
    return normalized in {"1 1 1, 1 1 1", "1, 1"}


def _extract_sets_line(best_string: str, exercise: str) -> str:
    trimmed = (best_string or "").strip()
    if not trimmed:
        return ""
    lines = [line.strip() for line in trimmed.splitlines() if line.strip()]
    if len(lines) >= 2:
        return lines[-1]
    if " - [" in trimmed and "]" in trimmed:
        tail = trimmed.split("]", 1)[1].strip()
        tail = tail.lstrip("-:").strip()
        if tail:
            return tail
    if trimmed.lower().startswith(exercise.lower()):
        tail = trimmed[len(exercise):].strip()
        tail = tail.lstrip("-:").strip()
        if tail:
            return tail
    return trimmed


def _normalize_bw(value: str) -> str:
    value = re.sub(r"\bbw/", "bw/", value, flags=re.IGNORECASE)
    value = re.sub(r"\bbw(?=[+-])", "bw", value, flags=re.IGNORECASE)
    value = re.sub(r"\bbw\b", "bw", value, flags=re.IGNORECASE)
    return value


def _expand_shorthand_tokens(tokens, target_sets: int = 3):
    if not tokens:
        return []
    if target_sets <= 0:
        return list(tokens)
    if len(tokens) == 1:
        return [tokens[0]] * target_sets
    if len(tokens) == 2 and len(tokens) < target_sets:
        return [tokens[0]] + [tokens[1]] * (target_sets - 1)
    if len(tokens) < target_sets:
        return list(tokens) + [tokens[-1]] * (target_sets - len(tokens))
    return list(tokens)


def _normalize_sets_line(sets_line: str, default_weight: str = "1", target_sets: int = 3) -> str:
    if not sets_line:
        return ""

    x_matches = re.findall(
        r'(bw(?:/\d+)?[+-]?\d*|-?\d+(?:\.\d+)?)\s*[x×]\s*(\d+)',
        sets_line,
        flags=re.IGNORECASE,
    )
    if x_matches:
        weights = [m[0] for m in x_matches]
        reps = [m[1] for m in x_matches]
    else:
        parts = sets_line.split(',', 1)
        weights_part = parts[0].strip() if parts else ""
        reps_part = parts[1].strip() if len(parts) > 1 else ""

        weights_part = re.sub(r'(kg|lbs|lb)', '', weights_part, flags=re.IGNORECASE)
        reps_part = re.sub(r'(kg|lbs|lb)', '', reps_part, flags=re.IGNORECASE)

        weights = [t for t in weights_part.replace(',', ' ').split() if t]
        reps = [t for t in reps_part.replace(',', ' ').split() if t]

    if not weights and not reps:
        weights = [default_weight]
        reps = ["1"]
    elif not weights:
        weights = [default_weight] * len(reps)
    elif not reps:
        reps = ["1"] * len(weights)

    weights = _expand_shorthand_tokens(weights, target_sets=target_sets)
    reps = _expand_shorthand_tokens(reps, target_sets=target_sets)

    if len(weights) == 1 and len(reps) > 1:
        weights = weights * len(reps)
    elif len(reps) == 1 and len(weights) > 1:
        reps = reps * len(weights)
    elif len(weights) < len(reps) and weights:
        weights = weights + [weights[-1]] * (len(reps) - len(weights))
    elif len(reps) < len(weights) and reps:
        reps = reps + [reps[-1]] * (len(weights) - len(reps))

    if x_matches:
        return ", ".join(f"{w} x {r}" for w, r in zip(weights, reps))

    return f"{' '.join(weights)}, {' '.join(reps)}"


def _count_sets_from_line(sets_line: str, target_sets: int = 3) -> int:
    """Count sets for the display line.

    If the line contains fewer tokens than target_sets, we assume trailing-repeat
    expansion up to target_sets.
    """
    if not sets_line:
        return 0

    # Check for 'x' notation (e.g., 'bw x5 x5 x5' or '100kg x5 x5')
    x_matches = re.findall(r'(bw(?:/\d+)?[+-]?\d*|-?\d+(?:\.\d+)?)\s*x\s*\d+', sets_line, flags=re.IGNORECASE)
    if x_matches:
        raw = len(x_matches)
        return int(target_sets) if target_sets and raw < int(target_sets) else raw
    
    # Check for comma-separated format (weights, reps)
    if ',' in sets_line:
        parts = sets_line.split(',', 1)
        weights_part = parts[0].strip()
        reps_part = parts[1].strip() if len(parts) > 1 else ''
        
        # Count tokens in weights part
        weight_tokens = weights_part.replace('kg', '').replace('lbs', '').replace('lb', '').strip().split()
        weight_count = len([t for t in weight_tokens if t])
        
        # Count tokens in reps part
        rep_tokens = reps_part.replace('kg', '').replace('lbs', '').replace('lb', '').strip().split()
        rep_count = len([t for t in rep_tokens if t])
        
        # Use maximum and apply shorthand
        raw_count = max(weight_count, rep_count)
        return int(target_sets) if target_sets and raw_count < int(target_sets) else raw_count
    
    # Single line of weights or values
    tokens = sets_line.replace('kg', '').replace('lbs', '').replace('lb', '').strip().split()
    raw_count = len([t for t in tokens if t])
    return int(target_sets) if target_sets and raw_count < int(target_sets) else raw_count