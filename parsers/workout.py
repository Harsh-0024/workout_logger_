"""
Workout text parser for converting raw workout text into structured data.
"""
import re
from datetime import datetime
from typing import Optional, Dict, List, Tuple


def normalize(values):
    if not values:
        return []
    return list(values)


def align_sets(weights, reps):
    if not weights and not reps:
        return [], []

    def expand_shorthand(values):
        if not values:
            return []
        if len(values) == 1:
            return values * 3
        if len(values) == 2:
            return [values[0], values[1], values[1]]
        return values

    if not reps and weights:
        reps = [1] * len(weights)
    if not weights and reps:
        weights = [1.0] * len(reps)

    weights = expand_shorthand(weights)
    reps = expand_shorthand(reps)

    if len(weights) == 1 and len(reps) > 1:
        weights = weights * len(reps)
    elif len(reps) == 1 and len(weights) > 1:
        reps = reps * len(weights)
    elif len(weights) < len(reps) and weights:
        weights = weights + [weights[-1]] * (len(reps) - len(weights))
    elif len(reps) < len(weights) and reps:
        reps = reps + [reps[-1]] * (len(weights) - len(reps))

    reps = [int(r) for r in reps]
    return weights, reps


def parse_weight_x_reps(segment, base_weight=None):
    segment = segment.replace('*', 'x').lower()
    matches = re.findall(r'(bw[+-]?\d*|-?\d+(?:\.\d+)?)\s*x\s*(\d+)', segment)
    if matches:
        weights, reps = [], []
        for w, r in matches:
            bw_weight = parse_bw_weight(w, base_weight)
            weights.append(bw_weight if bw_weight is not None else float(w))
            reps.append(int(r))
        return weights, reps
    return None, None


def extract_numbers(segment):
    segment = re.sub(r'(kg|lbs|lb)', '', segment.lower())
    numbers = []
    # FIX: Replace comma with space to ensure "16,16" parses as two numbers
    segment = segment.replace(',', ' ')
    for t in segment.split():
        try:
            numbers.append(float(t))
        except ValueError:
            continue
    return numbers


def parse_bw_weight(token, base_weight=None):
    token = token.strip().lower()
    if not token.startswith('bw'):
        return None
    token = token.replace('bw', '', 1)
    token = re.sub(r'(kg|lbs|lb)', '', token).strip()
    base = base_weight if base_weight is not None else 0.0
    if token in ('', '+', '-'):
        return base
    try:
        adjustment = float(token)
    except ValueError:
        return base
    if token.startswith(('+', '-')):
        return base + adjustment
    return base + adjustment if base_weight is not None else adjustment


def extract_weights(segment, base_weight=None):
    segment = re.sub(r'(kg|lbs|lb)', '', segment.lower())
    segment = segment.replace(',', ' ')
    numbers = []
    for t in segment.split():
        bw_weight = parse_bw_weight(t, base_weight)
        if bw_weight is not None:
            numbers.append(bw_weight)
            continue
        try:
            numbers.append(float(t))
        except ValueError:
            continue
    return numbers


def is_data_line(line):
    if not line:
        return False
    return bool(re.match(r'^(?:,|-?\d|bw)', line.strip().lower()))


def workout_parser(workout_day_received: str, bodyweight: Optional[float] = None) -> Optional[Dict]:
    """
    Parse raw workout text into structured data.
    
    Args:
        workout_day_received: Raw workout text string
        
    Returns:
        Dictionary with date, workout_name, and exercises list, or None if parsing fails
    """
    if not workout_day_received or not workout_day_received.strip():
        return None
    
    raw_lines = [line.strip() for line in workout_day_received.strip().split("\n") if line.strip()]
    if not raw_lines:
        return None

    # Header
    title_line = raw_lines[0]
    date_nums = re.findall(r'\d+', title_line.split()[0])
    current_year = datetime.now().year

    if len(date_nums) >= 2:
        parsed_month = int(date_nums[1])
        year = current_year - 1 if parsed_month > datetime.now().month + 1 else current_year
        try:
            date_obj = datetime.strptime(f"{date_nums[0]}-{date_nums[1]}-{year}", "%d-%m-%Y")
        except ValueError:
            date_obj = datetime.now()
    else:
        date_obj = datetime.now()

    workout_name = title_line
    if len(date_nums) >= 2:
        parts = title_line.split(' ', 1)
        if len(parts) > 1: workout_name = parts[1].strip()

    workout_day = {"date": date_obj, "workout_name": workout_name, "exercises": []}

    # Exercises
    list_of_lines = [re.sub(r'^\s*\d+\s*(?:[.)\-:]?)\s*', '', line) for line in raw_lines]
    i = 1
    while i < len(list_of_lines):
        clean_line = list_of_lines[i]
        name, weights, reps = "", [], []
        data_part = ""
        exercise_lines = [clean_line]
        consumed = 1

        if " - [" in clean_line:
            name = clean_line.split(" - [", 1)[0].strip()
            if "]" in clean_line:
                tail = clean_line.split("]", 1)[1].strip()
                tail = tail.lstrip("-:").strip()
                data_part = tail

            if not data_part and i + 1 < len(list_of_lines) and is_data_line(list_of_lines[i + 1]):
                data_line = list_of_lines[i + 1].strip()
                exercise_lines.append(data_line)
                data_part = data_line
                consumed += 1

                if "," not in data_part and i + 2 < len(list_of_lines) and is_data_line(list_of_lines[i + 2]):
                    reps_line = list_of_lines[i + 2].strip()
                    exercise_lines.append(reps_line)
                    data_part = f"{data_part}, {reps_line}"
                    consumed += 1
        else:
            tokens = clean_line.split()
            first_num_idx = -1
            for idx, token in enumerate(tokens):
                if re.match(r'^-?\d', token) or token.startswith(',') or token.lower().startswith('bw'):
                    first_num_idx = idx
                    break
            if first_num_idx != -1:
                name = " ".join(tokens[:first_num_idx]).strip()
                data_part = " ".join(tokens[first_num_idx:]).strip()
            else:
                name, data_part = clean_line, ""

        if not data_part and i + 1 < len(list_of_lines) and is_data_line(list_of_lines[i + 1]):
            data_line = list_of_lines[i + 1].strip()
            exercise_lines.append(data_line)
            data_part = data_line
            consumed += 1

            if "," not in data_part and i + 2 < len(list_of_lines) and is_data_line(list_of_lines[i + 2]):
                reps_line = list_of_lines[i + 2].strip()
                exercise_lines.append(reps_line)
                data_part = f"{data_part}, {reps_line}"
                consumed += 1

        if data_part:
            w_list, r_list = parse_weight_x_reps(data_part, bodyweight)
            if w_list:
                weights, reps = w_list, r_list
            elif ',' in data_part:
                subparts = data_part.split(',', 1)
                weights = extract_weights(subparts[0], bodyweight)
                reps = extract_numbers(subparts[1])

                if not weights and reps:
                    weights = [1.0] * len(reps)
                elif weights and not reps:
                    reps = [1] * len(weights)
            else:
                weights = extract_weights(data_part, bodyweight)
                reps = [1] * len(weights)

        if not name:
            name = "Unknown Exercise"

        weights, reps = align_sets(weights, reps)
        is_valid = bool(reps) or any(w != 0 for w in weights)

        workout_day["exercises"].append({
            "name": name.title(),
            "exercise_string": "\n".join(exercise_lines).strip(),
            "weights": weights,
            "reps": reps,
            "valid": is_valid
        })

        i += consumed

    return workout_day