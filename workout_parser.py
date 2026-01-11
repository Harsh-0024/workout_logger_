import re
from datetime import datetime


def normalize(values):
    """
    Ensures specific set counts for stats (Math only).
    Input:  [100]
    Output: [100, 100, 100] (Expands to 3 sets)
    Input:  [100, 110]
    Output: [100, 110, 110] (Repeats last set)
    """
    if not values: return [0, 0, 0]
    if len(values) == 1: return [values[0], values[0], values[0]]
    if len(values) == 2: return [values[0], values[1], values[1]]
    return values[:3]


def parse_weight_x_reps(segment):
    """
    Strict Parser: Looks for 'Weight x Reps'.
    Removes units (kg, lbs) but does NOT convert words like 'BW'.
    Allows negative numbers (e.g. -35x5).
    """
    # Just handle simple cleanup (kg/lbs removal happens in regex matching implicitly)
    # Allow * to be used as x
    segment = segment.replace('*', 'x').lower()

    # Regex: Number (float/int) x Number
    # Matches: 100x5, 100.5 x 5, -35x5
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
    """Extracts standalone numbers (e.g. for comma separated format)."""
    # Remove units first
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

    # --- 1. Header Parsing (Date & Title) ---
    title_line = raw_lines[0]
    date_nums = re.findall(r'\d+', title_line.split()[0])

    current_year = datetime.now().year
    current_month = datetime.now().month

    if len(date_nums) >= 2:
        parsed_month = int(date_nums[1])
        # Handle year rollover (Logging December in January)
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
    # Remove numbering "1." from start of lines
    list_of_lines = [re.sub(r'^\s*\d+\s*(?:[.)\-:]?)\s*', '', line) for line in raw_lines]

    for clean_line in list_of_lines[1:]:
        name = ""
        weights = []
        reps = []

        # Strategy A: Retrieve Format "Name - [Range] - Data"
        if " - [" in clean_line and "] - " in clean_line:
            try:
                parts = clean_line.split(" - [")
                name = parts[0].strip()
                data_part = clean_line.split("] - ")[1].strip()
            except IndexError:
                name = ""
                data_part = clean_line
        else:
            # Strategy B: Standard "Name Data"
            # Split at first digit
            tokens = clean_line.split()
            first_num_idx = -1

            for i, token in enumerate(tokens):
                # Look strictly for digits (no BW checks)
                if re.match(r'^-?\d', token):
                    first_num_idx = i
                    break

            if first_num_idx != -1:
                name = " ".join(tokens[:first_num_idx]).strip()
                data_part = " ".join(tokens[first_num_idx:]).strip()
            else:
                name = clean_line
                data_part = ""

        # --- Data Extraction ---
        if data_part:
            # 1. Try "Weight x Reps" (Priority)
            w_list, r_list = parse_weight_x_reps(data_part)
            if w_list:
                weights = w_list
                reps = r_list

            # 2. Try Comma Separation "Weights , Reps"
            elif ',' in data_part:
                subparts = data_part.split(',')
                weights = extract_numbers(subparts[0])
                reps = extract_numbers(subparts[1])

            # 3. Fallback: Implicit Reps (Weights only, Reps=1)
            else:
                weights = extract_numbers(data_part)
                reps = [1] * len(weights)

        if not name: name = "Unknown Exercise"

        exercise = {
            "name": name.title(),
            "exercise_string": clean_line,
            "weights": normalize(weights),  # Expands 1 set to 3
            "reps": normalize(reps),  # Expands 1 set to 3
        }
        workout_day["exercises"].append(exercise)

    return workout_day