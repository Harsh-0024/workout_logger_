"""
Workout text parser for converting raw workout text into structured data.
"""
import re
import html
from datetime import datetime
from typing import Optional, Dict, List, Tuple


def normalize(values):
    if not values:
        return []
    return list(values)


def align_sets(weights, reps, target_sets: Optional[int] = None):
    if not weights and not reps:
        return [], []

    def expand_shorthand(values, desired: int):
        if not values:
            return []
        if desired <= 0:
            return list(values)
        if len(values) == 1:
            return values * desired
        if len(values) == 2 and len(values) < desired:
            return [values[0]] + [values[1]] * (desired - 1)
        if len(values) < desired:
            return values + [values[-1]] * (desired - len(values))
        return values

    if not reps and weights:
        reps = [1] * len(weights)
    if not weights and reps:
        weights = [1.0] * len(reps)

    desired = target_sets if isinstance(target_sets, int) and target_sets > 0 else 3
    desired = max(desired, len(weights), len(reps))

    weights = expand_shorthand(weights, desired)
    reps = expand_shorthand(reps, desired)

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


def _extract_declared_sets(line: str) -> tuple[Optional[int], str]:
    if not line:
        return None, line

    m = re.search(r'(?:\s*[-–—:]\s*)?(\d+)\s*sets?\s*$', line, flags=re.IGNORECASE)
    if not m:
        return None, line
    try:
        count = int(m.group(1))
    except Exception:
        return None, line
    if count <= 0:
        return None, line

    cleaned = line[: m.start()].rstrip()
    cleaned = re.sub(r'[-–—:]\s*$', '', cleaned).rstrip()
    return count, cleaned


def _extract_sets_from_bracket(line: str) -> Optional[int]:
    if not line or '[' not in line or ']' not in line:
        return None
    try:
        inside = line.split('[', 1)[1].split(']', 1)[0].strip()
    except Exception:
        return None
    if not inside:
        return None
    first = inside.split(',', 1)[0].strip()
    if not re.match(r'^\d+$', first):
        return None
    try:
        count = int(first)
    except Exception:
        return None
    return count if count > 0 else None


def _has_time_range_hint(line: str) -> bool:
    if not line or '[' not in line or ']' not in line:
        return False
    try:
        inside = line.split('[', 1)[1].split(']', 1)[0].strip().lower()
    except Exception:
        return False
    return 's' in inside


def parse_bodyweight_line(line: str) -> Tuple[Optional[float], Optional[str]]:
    line = (line or '').strip()
    if not line:
        return None, None
    match = re.match(
        r'^(?:body\s*weight|bodyweight)\s*[-:]\s*(\d+(?:\.\d+)?)\s*(kg|kgs|lb|lbs)?\s*$',
        line,
        flags=re.IGNORECASE,
    )
    if not match:
        return None, None
    try:
        value = float(match.group(1))
    except ValueError:
        return None, None
    unit = (match.group(2) or '').lower()
    if unit == 'kgs':
        unit = 'kg'
    if unit == 'lb':
        unit = 'lbs'
    return value, unit or None


def parse_weight_x_reps(segment, base_weight=None):
    segment = (segment or '').replace('×', 'x').replace('*', 'x').lower()
    segment = re.sub(r'\bbody\s*weight\b', 'bw', segment)
    segment = re.sub(r'(kg|lbs|lb)', '', segment)
    matches = re.findall(
        r'(?:(bw(?:/\d+(?:\.\d+)?)?(?:[+-]\d+(?:\.\d+)?)?|-?\d+(?:\.\d+)?)\s*)?x\s*(\d+)',
        segment,
    )
    if not matches:
        return None, None

    weights, reps = [], []
    last_weight = None
    for w, r in matches:
        if w:
            bw_weight = parse_bw_weight(w, base_weight)
            last_weight = bw_weight if bw_weight is not None else float(w)
        if last_weight is None:
            return None, None
        weights.append(last_weight)
        reps.append(int(r))
    return weights, reps


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
    token = re.sub(r'^body\s*weight', 'bw', token)
    token = re.sub(r'^bodyweight', 'bw', token)
    if not token.startswith('bw'):
        return None
    token = token.replace('bw', '', 1)
    token = re.sub(r'(kg|lbs|lb)', '', token).strip()
    base = base_weight if base_weight is not None else 0.0

    divisor = 1.0
    if token.startswith('/'):
        m = re.match(r'^/(\d+(?:\.\d+)?)(.*)$', token)
        if m:
            try:
                divisor = float(m.group(1))
                token = (m.group(2) or '').strip()
            except ValueError:
                divisor = 1.0

    if divisor == 0:
        divisor = 1.0

    effective_base = base / divisor
    if token in ('', '+', '-'):
        return effective_base

    try:
        adjustment = float(token)
    except ValueError:
        return effective_base

    if token.startswith(('+', '-')):
        return effective_base + adjustment
    return effective_base + adjustment if base_weight is not None else adjustment


def extract_weights(segment, base_weight=None):
    segment = re.sub(r'(kg|lbs|lb)', '', segment.lower())
    segment = re.sub(r'\bbody\s*weight\b', 'bw', segment)
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
    stripped = line.strip()
    if re.match(r'^\d+(?:[.)\-:])\s*[A-Za-z]', stripped):
        return False
    tokens = stripped.split()
    if len(tokens) > 1 and re.match(r'^\d+(?:[.)\-:])?$', tokens[0]) and re.match(r'^[A-Za-z]', tokens[1]):
        return False
    return bool(re.match(r'^(?:,|-?\d|bw|body\s*weight|bodyweight)', stripped.lower()))


def is_probable_data_segment(segment: str) -> bool:
    segment = (segment or '').strip()
    if not segment:
        return False

    lowered = re.sub(r'\bbody\s*weight\b', 'bw', segment.lower())
    if re.search(r'[x×*]', lowered):
        return True
    if ',' in lowered:
        return True

    tokens = re.split(r'\s+', lowered)
    for token in tokens:
        if not token:
            continue
        cleaned = re.sub(r'[,:]+$', '', token)
        if cleaned.startswith('bw') or cleaned.startswith('bodyweight') or cleaned == 'body':
            continue
        cleaned = re.sub(r'(kg|lbs|lb)', '', cleaned)
        if re.match(r'^-?\d+(?:\.\d+)?$', cleaned):
            continue
        return False

    return True


def parse_weight_reps_pairs(segment, base_weight: Optional[float] = None, max_rep_value: int = 30):
    segment = re.sub(r'\bbody\s*weight\b', 'bw', (segment or '').strip(), flags=re.IGNORECASE)
    if not segment:
        return None, None
    if ',' in segment:
        return None, None
    if re.search(r'[x×*]', segment, flags=re.IGNORECASE):
        return None, None

    tokens = segment.split()
    if len(tokens) < 2:
        return None, None

    if len(tokens) != 2 and len(tokens) % 2 != 0:
        return None, None

    def parse_weight_token(token: str):
        bw_weight = parse_bw_weight(token, base_weight)
        if bw_weight is not None:
            return bw_weight
        try:
            return float(token)
        except ValueError:
            return None

    def parse_reps_token(token: str):
        if not re.match(r'^\d+$', token):
            return None
        try:
            return int(token)
        except ValueError:
            return None

    if len(tokens) == 2:
        w_token, r_token = tokens[0], tokens[1]
        w_val = parse_weight_token(w_token)
        r_val = parse_reps_token(r_token)
        if w_val is None or r_val is None:
            return None, None
        if r_val <= 0 or r_val > max_rep_value:
            return None, None
        weight_hint = (
            '.' in w_token
            or w_token.lower().startswith('bw')
            or w_token.startswith('-')
            or w_token != r_token
        )
        if not weight_hint:
            return None, None
        return [w_val], [r_val]

    weights, reps = [], []
    for idx in range(0, len(tokens), 2):
        w = parse_weight_token(tokens[idx])
        r = parse_reps_token(tokens[idx + 1])
        if w is None or r is None:
            return None, None
        if r <= 0 or r > max_rep_value:
            return None, None
        weights.append(w)
        reps.append(r)
    return weights, reps


def parse_weight_reps_halves(segment, base_weight: Optional[float] = None, max_rep_value: int = 30):
    segment = re.sub(r'\bbody\s*weight\b', 'bw', (segment or '').strip(), flags=re.IGNORECASE)
    if not segment:
        return None, None
    if ',' in segment:
        return None, None
    if re.search(r'[x×*]', segment, flags=re.IGNORECASE):
        return None, None

    tokens = segment.split()
    if len(tokens) < 4 or len(tokens) % 2 != 0:
        return None, None

    half = len(tokens) // 2
    weights_tokens = tokens[:half]
    reps_tokens = tokens[half:]

    reps = []
    for tok in reps_tokens:
        if not re.match(r'^\d+$', tok):
            return None, None
        try:
            val = int(tok)
        except ValueError:
            return None, None
        if val <= 0 or val > max_rep_value:
            return None, None
        reps.append(val)

    weights = []
    for tok in weights_tokens:
        bw_weight = parse_bw_weight(tok, base_weight)
        if bw_weight is not None:
            weights.append(bw_weight)
            continue
        try:
            weights.append(float(tok))
        except ValueError:
            return None, None

    return weights, reps


def workout_parser(
    workout_day_received: str,
    bodyweight: Optional[float] = None,
    preserve_bodyweight_offsets: bool = False,
) -> Optional[Dict]:
    """
    Parse raw workout text into structured data.
    
    Args:
        workout_day_received: Raw workout text string
        
    Returns:
        Dictionary with date, workout_name, and exercises list, or None if parsing fails
    """
    if not workout_day_received or not workout_day_received.strip():
        return None
    
    raw_lines: List[str] = []
    parsed_bodyweight: Optional[float] = None
    parsed_bodyweight_unit: Optional[str] = None
    for line in workout_day_received.strip().split("\n"):
        stripped = (line or "").strip()
        if not stripped:
            continue
        # Treat comment / section markers (e.g., "#Gym") as separators, not exercises.
        if stripped.startswith("#"):
            continue
        bw_value, bw_unit = parse_bodyweight_line(stripped)
        if bw_value is not None:
            parsed_bodyweight = bw_value
            parsed_bodyweight_unit = bw_unit
            continue
        raw_lines.append(stripped)
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
        if len(parts) > 1:
            workout_name = parts[1].strip()
    workout_name = html.unescape(workout_name)
    workout_name = workout_name.lstrip('-–—').strip()

    effective_bodyweight = None if preserve_bodyweight_offsets else (
        parsed_bodyweight if parsed_bodyweight is not None else bodyweight
    )
    workout_day = {
        "date": date_obj,
        "workout_name": workout_name,
        "bodyweight": parsed_bodyweight,
        "bodyweight_unit": parsed_bodyweight_unit,
        "exercises": [],
    }

    # Exercises
    list_of_lines = []
    for line in raw_lines:
        stripped = line.strip()
        if is_data_line(stripped):
            list_of_lines.append(stripped)
        else:
            list_of_lines.append(re.sub(r'^\s*\d+\s*(?:[.)\-:]?)\s*', '', stripped))
    i = 1
    while i < len(list_of_lines):
        clean_line = list_of_lines[i]
        name, weights, reps = "", [], []
        data_part = ""
        time_range_hint = _has_time_range_hint(clean_line)
        declared_sets, cleaned_line = _extract_declared_sets(clean_line)
        bracket_sets = _extract_sets_from_bracket(cleaned_line)
        if bracket_sets is not None:
            declared_sets = bracket_sets

        exercise_lines = [cleaned_line]
        consumed = 1

        if " - [" in cleaned_line:
            name = cleaned_line.split(" - [", 1)[0].strip()
            if "]" in cleaned_line:
                tail = cleaned_line.split("]", 1)[1].strip()
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
                token_lower = token.lower()
                if (
                    re.match(r'^-?\d', token)
                    or token.startswith(',')
                    or token_lower.startswith('bw')
                    or token_lower.startswith('bodyweight')
                    or (token_lower == 'body' and idx + 1 < len(tokens) and tokens[idx + 1].lower().startswith('weight'))
                ):
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

        if data_part and not is_probable_data_segment(data_part):
            data_part = ""

        if data_part:
            w_list, r_list = parse_weight_x_reps(data_part, effective_bodyweight)
            if w_list:
                weights, reps = w_list, r_list
            elif ',' in data_part:
                subparts = data_part.split(',', 1)
                weights = extract_weights(subparts[0], effective_bodyweight)
                reps = extract_numbers(subparts[1])

                if not weights and reps:
                    weights = [1.0] * len(reps)
                elif weights and not reps:
                    reps = [1] * len(weights)
            else:
                max_rep_value = 600 if time_range_hint else 30
                w_pairs, r_pairs = parse_weight_reps_pairs(
                    data_part,
                    effective_bodyweight,
                    max_rep_value=max_rep_value,
                )
                if w_pairs and r_pairs:
                    weights, reps = w_pairs, r_pairs
                else:
                    w_halves, r_halves = parse_weight_reps_halves(
                        data_part,
                        effective_bodyweight,
                        max_rep_value=max_rep_value,
                    )
                    if w_halves and r_halves:
                        weights, reps = w_halves, r_halves
                    else:
                        weights = extract_weights(data_part, effective_bodyweight)
                        reps = [1] * len(weights)

        if not name:
            name = "Unknown Exercise"

        inferred_sets = max(len(weights), len(reps)) if (weights or reps) else 0
        if declared_sets is not None:
            target_sets = max(int(declared_sets), inferred_sets)
        else:
            target_sets = inferred_sets if inferred_sets > 3 else 3

        weights, reps = align_sets(weights, reps, target_sets=target_sets)
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
