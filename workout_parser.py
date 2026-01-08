import re
from datetime import datetime


def normalize(values):
    """Ensures we always have a list of 3 values."""
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
    raw_lines = [line.strip() for line in workout_day_received.strip().split("\n") if line.strip()]

    if not raw_lines:
        return None

    # Apply the numbering cleanup to all lines immediately
    list_of_lines = [re.sub(r'^\s*\d+\s*(?:[.)\-:]?)\s*', '', line) for line in raw_lines]

    title_line = raw_lines[0]

    # Extract date nums
    date_nums = re.findall(r'\d+', title_line.split()[0])

    current_year = datetime.now().year
    current_month = datetime.now().month

    if len(date_nums) >= 2:
        parsed_month = int(date_nums[1])
        if parsed_month > current_month + 1:
            year = current_year - 1
        else:
            year = current_year
        date_str = f"{date_nums[0]}-{date_nums[1]}-{year}"
        date_obj = datetime.strptime(date_str, "%d-%m-%Y")
    else:
        date_obj = datetime.now()

    workout_name = title_line[title_line.find(" ") + 1:].strip()

    workout_day = {
        "date": date_obj,
        "workout_name": workout_name,
        "exercises": []
    }

    # Skip the title line [1:]
    for clean_line in list_of_lines[1:]:

        # --- NEW LOGIC: Handle "Name - [Range] - Data" format ---
        # If the line contains the rep range separator, split it to isolate name and data
        if " - [" in clean_line and "] - " in clean_line:
            parts = clean_line.split(" - [")
            name_part = parts[0].strip()

            # The rest is "Range] - Data"
            # Split by "] - " to get the data part
            remainder = clean_line.split("] - ")[1].strip()

            # Use the extracted name
            name = name_part
            data_part = remainder
        else:
            # Fallback to old format (just name and data mixed)
            name = ""
            data_part = clean_line

        # Now parse the 'data_part' (weights/reps) using the standard logic
        if ',' in data_part:
            parts = data_part.split(',')
            left_part = parts[0].strip()
            right_part = parts[1].strip()

            if not name:  # If we didn't extract name from the new format above
                left_tokens = left_part.split()
                num_idx = next((i for i, t in enumerate(left_tokens) if is_number(t)), None)
                if num_idx is not None:
                    name = " ".join(left_tokens[:num_idx]).strip()
                    weights = [float(i) if '.' in i else int(i) for i in left_tokens[num_idx:]]
                else:
                    name = left_part
                    weights = [1, 1, 1]
            else:
                # If name is already found, left_part is just weights
                weights = [float(i) if '.' in i else int(i) for i in left_part.split()]

            reps = [int(i) for i in right_part.split()]
        else:
            # No comma case
            tokens = data_part.split()

            if not name:
                num_idx = next((i for i, t in enumerate(tokens) if is_number(t)), None)
                if num_idx is not None:
                    name = " ".join(tokens[:num_idx]).strip()
                    weights = [float(i) if '.' in i else int(i) for i in tokens[num_idx:]]
                else:
                    name = data_part
                    weights = [1, 1, 1]
            else:
                weights = [float(i) if '.' in i else int(i) for i in tokens]

            reps = [1, 1, 1]

        exercise = {
            "name": name.title(),
            "exercise_string": clean_line,  # Save the full original line (including ranges)
            "weights": normalize(weights),
            "reps": normalize(reps),
        }
        workout_day["exercises"].append(exercise)

    return workout_day