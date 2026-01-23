from datetime import datetime, timedelta
import re

from models import Plan, RepRange, User, UserRole
from list_of_exercise import BW_EXERCISES, DEFAULT_PLAN, DEFAULT_REP_RANGES, get_workout_days
from services.helpers import find_best_match


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
    key = f"{category} {day_id}"
    plan_text = get_effective_plan_text(db_session, user)
    if not plan_text:
        return "Plan not found.", 0, 0

    all_plans = get_workout_days(plan_text)

    rep_text = get_effective_rep_ranges_text(db_session, user)
    custom_ranges = {}
    if rep_text:
        for line in rep_text.split('\n'):
            if ':' in line:
                k, v = line.split(':', 1)
                custom_ranges[k.strip().lower()] = v.strip()

    ist_offset = timedelta(hours=5, minutes=30)
    today_str = (datetime.utcnow() + ist_offset).strftime("%d/%m")
    output_lines = [f"{today_str} {key}", ""]

    try:
        exercises = all_plans["workout"][category][key]
        exercise_count = len(exercises)
        set_count = 0
        
        for ex in exercises:
            rng = custom_ranges.get(ex.lower(), "")
            fmt_rng = f" - [{rng}]" if rng else ""

            rec = find_best_match(db_session, user.id, ex)
            sets_line = ""
            if rec and rec.best_string:
                sets_line = _extract_sets_line(rec.best_string, ex)
                if sets_line and ex in BW_EXERCISES:
                    sets_line = _normalize_bw(sets_line)

            if not sets_line and rec and rec.sets_json:
                sets_line = _format_sets_json(rec.sets_json)

            filled_defaults = False
            if ex in BW_EXERCISES:
                if not sets_line or _is_default_numeric_sets(sets_line):
                    sets_line = "bw/4, 1"
                    filled_defaults = True
                sets_line = _normalize_bw(sets_line)
            elif not sets_line:
                # Default missing weights/reps to 1,1 (expands to 3 sets)
                sets_line = "1, 1"
                filled_defaults = True

            if sets_line:
                default_weight = "bw/4" if ex in BW_EXERCISES else "1"
                has_x = re.search(r'(bw(?:/\d+)?[+-]?\d*|-?\d+(?:\.\d+)?)\s*[x×]\s*\d+', sets_line, flags=re.IGNORECASE)
                missing_side = False
                if not has_x:
                    if ',' in sets_line:
                        parts = sets_line.split(',', 1)
                        missing_side = not parts[0].strip() or not parts[1].strip()
                    else:
                        missing_side = True

                if filled_defaults or missing_side:
                    sets_line = _normalize_sets_line(sets_line, default_weight=default_weight)

            # Count sets from sets_line
            if sets_line:
                set_count += _count_sets_from_line(sets_line)

            output_lines.append(f"{ex}{fmt_rng}")
            if sets_line:
                output_lines.append(sets_line)
            output_lines.append("")

        return "\n".join(output_lines).rstrip(), exercise_count, set_count
    except KeyError:
        return f"Day '{key}' not found in plan.", 0, 0


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


def _expand_shorthand_tokens(tokens):
    if not tokens:
        return []
    if len(tokens) == 1:
        return [tokens[0], tokens[0], tokens[0]]
    if len(tokens) == 2:
        return [tokens[0], tokens[1], tokens[1]]
    return tokens


def _normalize_sets_line(sets_line: str, default_weight: str = "1") -> str:
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

    weights = _expand_shorthand_tokens(weights)
    reps = _expand_shorthand_tokens(reps)

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


def _count_sets_from_line(sets_line: str) -> int:
    """Count number of sets from a sets line applying shorthand expansion.
    
    Shorthand rules:
    - 1 entry → 3 identical sets
    - 2 entries → first + second * 2
    - 3+ entries → as is
    """
    if not sets_line:
        return 0
    
    def apply_shorthand_expansion(count):
        if count == 1:
            return 3
        elif count == 2:
            return 3
        return count
    
    # Check for 'x' notation (e.g., 'bw x5 x5 x5' or '100kg x5 x5')
    x_matches = re.findall(r'(bw(?:/\d+)?[+-]?\d*|-?\d+(?:\.\d+)?)\s*x\s*\d+', sets_line, flags=re.IGNORECASE)
    if x_matches:
        return apply_shorthand_expansion(len(x_matches))
    
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
        return apply_shorthand_expansion(raw_count)
    
    # Single line of weights or values
    tokens = sets_line.replace('kg', '').replace('lbs', '').replace('lb', '').strip().split()
    raw_count = len([t for t in tokens if t])
    return apply_shorthand_expansion(raw_count)