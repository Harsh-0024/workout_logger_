import re
from datetime import datetime


def normalize(values):
    """
    Ensures specific set counts for stats (Math only).
    """
    if not values: return [0, 0, 0]
    if len(values) == 1: return [values[0], values[0], values[0]]
    if len(values) == 2: return [values[0], values[1], values[1]]
    return values[:3]


def parse_weight_x_reps(segment):
    """
    Strict Parser: Looks for 'Weight x Reps'.
    """
    segment = segment.replace('*', 'x').lower()
    matches = re.findall(r'(-?\d+(?:\.\d+)?)\s*x\s*(\d+)', segment)

    if matches:
        weights = []
        reps = []
        for w, r in matches:
            weights.append(float(w))
            reps.append(int(r))
        return weights, reps
    return None, None


def extract_numbers(segment):
    """Extracts standalone numbers."""
    segment = re.sub(r'(kg|lbs|lb)', '', segment.lower())
    tokens = segment.split()
    numbers = []
    for t in tokens:
        try:
            val = float(t)
            numbers.append(val)
        except ValueError:
            continue
    return numbers


def workout_parser(workout_day_received):
    raw_lines = [line.strip() for line in workout_day_received.strip().split("\n") if line.strip()]

    if not raw_lines:
        return None

    # --- 1. Header Parsing ---
    title_line = raw_lines[0]
    date_nums = re.findall(r'\d+', title_line.split()[0])

    current_year = datetime.now().year
    current_month = datetime.now().month

    if len(date_nums) >= 2:
        parsed_month = int(date_nums[1])
        year = current_year - 1 if parsed_month > current_month + 1 else current_year
        date_str = f"{date_nums[0]}-{date_nums[1]}-{year}"
        try:
            date_obj = datetime.strptime(date_str, "%d-%m-%Y")
        except ValueError:
            date_obj = datetime.now()
    else:
        date_obj = datetime.now()

    workout_name = title_line
    if len(date_nums) >= 2:
        parts = title_line.split(' ', 1)
        if len(parts) > 1:
            workout_name = parts[1].strip()

    workout_day = {
        "date": date_obj,
        "workout_name": workout_name,
        "exercises": []
    }

    # --- 2. Exercise Parsing ---
    list_of_lines = [re.sub(r'^\s*\d+\s*(?:[.)\-:]?)\s*', '', line) for line in raw_lines]

    for clean_line in list_of_lines[1:]:
        name = ""
        weights = []
        reps = []

        # Strategy A: Retrieve Format
        if " - [" in clean_line and "] - " in clean_line:
            try:
                parts = clean_line.split(" - [")
                name = parts[0].strip()
                data_part = clean_line.split("] - ")[1].strip()
            except IndexError:
                name = ""
                data_part = clean_line
        else:
            # Strategy B: Standard Format
            tokens = clean_line.split()
            first_num_idx = -1

            for i, token in enumerate(tokens):
                if re.match(r'^-?\d', token):
                    first_num_idx = i
                    break

            if first_num_idx != -1:
                name = " ".join(tokens[:first_num_idx]).strip()
                data_part = " ".join(tokens[first_num_idx:]).strip()
            else:
                name = clean_line
                data_part = ""

        if data_part:
            w_list, r_list = parse_weight_x_reps(data_part)
            if w_list:
                weights, reps = w_list, r_list
            elif ',' in data_part:
                subparts = data_part.split(',')
                weights = extract_numbers(subparts[0])
                reps = extract_numbers(subparts[1])
            else:
                weights = extract_numbers(data_part)
                reps = [1] * len(weights)

        if not name: name = "Unknown Exercise"

        final_weights = normalize(weights)

        # --- NEW: VALIDATION CHECK ---
        # If max weight is 0 (and it's not a negative lift), we consider it invalid/empty
        # We check if absolute max value is 0 to handle cases like just text
        is_valid = False
        for w in final_weights:
            if w != 0:
                is_valid = True
                break

        exercise = {
            "name": name.title(),
            "exercise_string": clean_line,
            "weights": final_weights,
            "reps": normalize(reps),
            "valid": is_valid  # <--- The Flag
        }
        workout_day["exercises"].append(exercise)

    return workout_day