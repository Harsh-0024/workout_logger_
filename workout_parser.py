import re
from datetime import datetime


def normalize(values):
    """Ensures we always have a list of 3 values."""
    # Kept this safety check (Constraint: 0 values -> [0,0,0])
    if not values: return [0, 0, 0]
    if len(values) == 1: return [values[0], values[0], values[0]]
    if len(values) == 2: return [values[0], values[1], values[1]]
    return values[:3]


def is_number(s):
    """Helper: Checks if a string is a number (integer or decimal)."""
    try:
        float(s)
        return True
    except ValueError:
        return False


def workout_parser(workout_day_received):
    # 1. CLEANUP: Filter empty lines AND remove leading numbers/dots globally here
    # This uses the better Regex she suggested.
    raw_lines = [line.strip() for line in workout_day_received.strip().split("\n") if line.strip()]

    if not raw_lines:
        return None

    # Apply the numbering cleanup to all lines immediately
    list_of_lines = [re.sub(r'^\s*\d+\s*(?:[.)\-:]?)\s*', '', line) for line in raw_lines]

    title_line = raw_lines[0]  # Use raw_line for title to grab date properly

    # Extract date nums
    date_nums = re.findall(r'\d+', title_line.split()[0])

    # Year Logic Fix
    current_year = datetime.now().year
    current_month = datetime.now().month
    # Safety: ensure we actually found numbers
    if len(date_nums) >= 2:
        parsed_month = int(date_nums[1])
        if parsed_month > current_month + 1:
            year = current_year - 1
        else:
            year = current_year
        date_str = f"{date_nums[0]}-{date_nums[1]}-{year}"
        date_obj = datetime.strptime(date_str, "%d-%m-%Y")
    else:
        # Fallback if title format is weird
        date_obj = datetime.now()

    # Extract Name (everything after the first space in the title)
    workout_name = title_line[title_line.find(" ") + 1:].strip()

    workout_day = {
        "date": date_obj,
        "workout_name": workout_name,
        "exercises": []
    }

    # Skip the title line [1:]
    for clean_line in list_of_lines[1:]:

        # Split by comma if it exists
        if ',' in clean_line:
            parts = clean_line.split(',')
            left_part = parts[0].strip()
            right_part = parts[1].strip()

            # --- BETTER LOGIC (Fixes "Triceps 21s" bug) ---
            # Tokenize and find the first numeric token
            left_tokens = left_part.split()
            # Use is_number() instead of isdigit() to allow decimals like 2.5
            num_idx = next((i for i, t in enumerate(left_tokens) if is_number(t)), None)

            if num_idx is not None:
                name = " ".join(left_tokens[:num_idx]).strip()
                weights = [float(i) if '.' in i else int(i) for i in left_tokens[num_idx:]]
            else:
                # No weights before comma
                name = left_part
                weights = [1, 1, 1]  # Explicit default

            reps = [int(i) for i in right_part.split()]
        else:
            # Case: No comma
            tokens = clean_line.split()
            num_idx = next((i for i, t in enumerate(tokens) if is_number(t)), None)

            if num_idx is not None:
                name = " ".join(tokens[:num_idx]).strip()
                weights = [float(i) if '.' in i else int(i) for i in tokens[num_idx:]]
            else:
                name = clean_line
                weights = [1, 1, 1]
            reps = [1, 1, 1]

        exercise = {
            "name": name.title(),
            "exercise_string": clean_line,
            "weights": normalize(weights),
            "reps": normalize(reps),
        }
        workout_day["exercises"].append(exercise)

    return workout_day
