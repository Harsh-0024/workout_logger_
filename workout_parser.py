import re
from datetime import datetime


def normalize(values):
    """
    Ensures a list of weights/reps always has 3 elements.
    - If 1 val:  [10] -> [10, 10, 10]
    - If 2 vals: [10, 12] -> [10, 12, 12] (repeats last)
    - If 3+ vals: Truncates to first 3
    """
    if not values: return [0, 0, 0]
    if len(values) == 1: return [values[0], values[0], values[0]]
    if len(values) == 2: return [values[0], values[1], values[1]]
    return values[:3]


def is_number(s):
    """Checks if a string can be converted to a float/int."""
    try:
        float(s)
        return True
    except ValueError:
        return False


def workout_parser(workout_day_received):
    """
    Main Logic: Converts raw pasted text from the 'Log' page into a structured object.
    """

    # 1. Basic Cleanup: split by lines, strip whitespace
    raw_lines = [line.strip() for line in workout_day_received.strip().split("\n") if line.strip()]

    if not raw_lines:
        return None

    # 2. Numbering Cleanup: Remove "1.", "2." from start of lines
    list_of_lines = [re.sub(r'^\s*\d+\s*(?:[.)\-:]?)\s*', '', line) for line in raw_lines]

    # 3. Header Extraction: The first line is treated as the Title/Date line
    title_line = raw_lines[0]

    # Try to find a date in the header (e.g., "08/01 Arms 1")
    date_nums = re.findall(r'\d+', title_line.split()[0])
    current_year = datetime.now().year
    current_month = datetime.now().month

    if len(date_nums) >= 2:
        # Date found, assume format DD/MM
        parsed_month = int(date_nums[1])
        # Logic to handle year rollovers (logging December in January)
        if parsed_month > current_month + 1:
            year = current_year - 1
        else:
            year = current_year
        date_str = f"{date_nums[0]}-{date_nums[1]}-{year}"
        date_obj = datetime.strptime(date_str, "%d-%m-%Y")
    else:
        # No date found, default to Today
        date_obj = datetime.now()

    # Extract workout name (everything after the date part)
    workout_name = title_line[title_line.find(" ") + 1:].strip()

    workout_day = {
        "date": date_obj,
        "workout_name": workout_name,
        "exercises": []
    }

    # 4. Process each Exercise Line (skipping the title)
    for clean_line in list_of_lines[1:]:

        # --- LOGIC for "Name - [Range] - Data" ---
        # Checks if line matches the Retrieve format
        if " - [" in clean_line and "] - " in clean_line:
            parts = clean_line.split(" - [")
            name_part = parts[0].strip()

            # The rest is "Range] - Data" -> Split by "] - "
            remainder = clean_line.split("] - ")[1].strip()

            name = name_part
            data_part = remainder
        else:
            # Fallback: Standard "Name Data" format
            name = ""
            data_part = clean_line

        # --- PARSING DATA (Weights & Reps) ---
        # Logic to separate weights (left) from reps (right)

        if ',' in data_part:
            # Format: "10 20 30, 10 10 10" (Comma separates weights/reps)
            parts = data_part.split(',')
            left_part = parts[0].strip()
            right_part = parts[1].strip()

            if not name:
                # If name wasn't extracted earlier, extract from left_part
                left_tokens = left_part.split()
                # Find index of first number
                num_idx = next((i for i, t in enumerate(left_tokens) if is_number(t)), None)
                if num_idx is not None:
                    name = " ".join(left_tokens[:num_idx]).strip()
                    weights = [float(i) if '.' in i else int(i) for i in left_tokens[num_idx:]]
                else:
                    name = left_part
                    weights = [1, 1, 1]
            else:
                # Name known, left_part is purely weights
                weights = [float(i) if '.' in i else int(i) for i in left_part.split()]

            reps = [int(i) for i in right_part.split()]
        else:
            # Format: "10 20 30" (No comma, assumes weights only, or mixed)
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

        # Build the final object
        exercise = {
            "name": name.title(),
            "exercise_string": clean_line,  # Keep original string for display
            "weights": normalize(weights),
            "reps": normalize(reps),
        }
        workout_day["exercises"].append(exercise)

    return workout_day