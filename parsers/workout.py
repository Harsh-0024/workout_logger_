import re
from datetime import datetime


def normalize(values):
    if not values: return [0, 0, 0]
    if len(values) == 1: return [values[0], values[0], values[0]]
    if len(values) == 2: return [values[0], values[1], values[1]]
    return values[:3]


def parse_weight_x_reps(segment):
    segment = segment.replace('*', 'x').lower()
    matches = re.findall(r'(-?\d+(?:\.\d+)?)\s*x\s*(\d+)', segment)
    if matches:
        weights, reps = [], []
        for w, r in matches:
            weights.append(float(w))
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


def workout_parser(workout_day_received):
    raw_lines = [line.strip() for line in workout_day_received.strip().split("\n") if line.strip()]
    if not raw_lines: return None

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
    for clean_line in list_of_lines[1:]:
        name, weights, reps = "", [], []

        # Strategy A vs B Parsing
        if " - [" in clean_line and "] - " in clean_line:
            try:
                name = clean_line.split(" - [")[0].strip()
                data_part = clean_line.split("] - ")[1].strip()
            except:
                name, data_part = "", clean_line
        else:
            tokens = clean_line.split()
            first_num_idx = -1
            for i, token in enumerate(tokens):
                # FIX 1: Recognize tokens starting with comma (e.g. ",16") as data start
                if re.match(r'^-?\d', token) or token.startswith(','):
                    first_num_idx = i
                    break
            if first_num_idx != -1:
                name = " ".join(tokens[:first_num_idx]).strip()
                data_part = " ".join(tokens[first_num_idx:]).strip()
            else:
                name, data_part = clean_line, ""

        if data_part:
            w_list, r_list = parse_weight_x_reps(data_part)
            if w_list:
                weights, reps = w_list, r_list
            elif ',' in data_part:
                # Comma separated (Weights, Reps)
                subparts = data_part.split(',', 1)  # Split only on first comma
                weights = extract_numbers(subparts[0])
                reps = extract_numbers(subparts[1])

                # FIX 2: Default Logic for "Lower Abs ,16"
                if not weights and reps:
                    weights = [1.0] * len(reps)
                elif weights and not reps:
                    reps = [1] * len(weights)
            else:
                # Space separated (Weights only -> Reps=1)
                weights = extract_numbers(data_part)
                reps = [1] * len(weights)

        if not name: name = "Unknown Exercise"

        final_weights = normalize(weights)
        is_valid = any(w != 0 for w in final_weights)

        workout_day["exercises"].append({
            "name": name.title(),
            "exercise_string": clean_line,
            "weights": final_weights,
            "reps": normalize(reps),
            "valid": is_valid
        })

    return workout_day