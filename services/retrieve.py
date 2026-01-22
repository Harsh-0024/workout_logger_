from datetime import datetime, timedelta
import re

from models import Plan, RepRange
from list_of_exercise import BW_EXERCISES, get_workout_days
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
    output_lines = [f"{today_str} {key}", ""]

    try:
        exercises = all_plans["workout"][category][key]
        for ex in exercises:
            rng = custom_ranges.get(ex, "")
            fmt_rng = f" - [{rng}]" if rng else ""

            rec = find_best_match(db_session, user.id, ex)
            sets_line = ""
            if rec and rec.best_string:
                sets_line = _extract_sets_line(rec.best_string, ex)
                if sets_line and ex in BW_EXERCISES:
                    sets_line = _normalize_bw(sets_line)

            if not sets_line and rec and rec.sets_json:
                sets_line = _format_sets_json(rec.sets_json)

            if ex in BW_EXERCISES:
                if not sets_line or _is_default_numeric_sets(sets_line):
                    sets_line = "bw/4, 1"
                sets_line = _normalize_bw(sets_line)

            output_lines.append(f"{ex}{fmt_rng}")
            if sets_line:
                output_lines.append(sets_line)
            output_lines.append("")

        return "\n".join(output_lines).rstrip()
    except KeyError:
        return f"Day '{key}' not found in plan."


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