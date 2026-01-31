import re

# --- SHARED EXERCISE LIST ---
list_of_exercises = [
    "Barbell Curl", "Barbell Overhead Extension", "Barbell Overhead Press",
    "Cable Lateral Raise", "Calf Raises Sitting", "Calf Raises Standing",
    "Crunches A", "Crunches B", "Deadlift", "Dips", "Dumbbell Curl",
    "Dumbbell Overhead Extension", "Dumbbell Overhead Press", "Farmer's Walk",
    "Flat Barbell Press", "Flat Dumbbell Press", "Forearm Radial Deviation",
    "Forearm Roller", "Forearm Ulnar Deviation", "Hammer Dumbbell Curl",
    "Hammer Rope Curl", "Hip Abduction", "Hip Adduction", "Hip Thrust",
    "Hyper Extension", "Incline Barbell Press", "Incline Dumbbell Press",
    "Lat Dumbbell Rows", "Lat Pulldown", "Leg Curl", "Leg Extension",
    "Leg Press", "Low Cable Fly", "Lower Abs", "Machine Lateral Raise",
    "Machine Shoulder Press", "Machine Squat", "Neutral-Grip Lat Pulldown",
    "Neutral-Grip Pull-Ups", "Neutral-Grip Seated Row", "Pec Deck Fly",
    "Preacher Curl", "Pull Ups", "Rear Delt Machine Fly", "Reverse Barbell Curl",
    "Reverse Dumbbell Curl", "Romanian Deadlift", "Rope Face Pull",
    "Skull Crushers", "Smith Machine Squat", "Stationary Lunges",
    "Triceps Rod Pushdown", "Triceps Rope Pushdown", "V Tucks",
    "Walking Dumbbell Lunges", "Wide-Grip Chest-Supported Row",
    "Wide-Grip Seated Row", "Wrist Extension - Dumbbell",
    "Wrist Extension - Machine", "Wrist Flexion - Dumbbell",
    "Wrist Flexion - Machine"
]

# --- DEFAULT REP RANGES ---
DEFAULT_REP_RANGES = {
    "Flat Barbell Press": "5–8",
    "Incline Dumbbell Press": "6–10",
    "Incline Barbell Press": "5–8",
    "Flat Dumbbell Press": "8–12",
    "Low Cable Fly": "12–20",
    "Pec Deck Fly": "12–20",
    "Dips": "6–12",
    "Triceps Rod Pushdown": "10–15",
    "Triceps Rope Pushdown": "12–20",
    "Dumbbell Overhead Extension": "8–12",
    "Barbell Overhead Extension": "6–10",
    "Skull Crushers": "6–10",
    "Deadlift": "3–6",
    "Neutral-Grip Pull-Ups": "6–10",
    "Pull Ups": "6–10",
    "Neutral-Grip Lat Pulldown": "8–12",
    "Lat Pulldown": "8–12",
    "Wide-Grip Seated Row": "8–12",
    "Neutral-Grip Seated Row": "8–12",
    "Wide-Grip Chest-Supported Row": "10–15",
    "Lat Dumbbell Rows": "8–12",
    "Hyper Extension": "12–20",
    "Barbell Curl": "6–10",
    "Dumbbell Curl": "8–12",
    "Preacher Curl": "8–12",
    "Hammer Dumbbell Curl": "10–15",
    "Hammer Rope Curl": "12–15",
    "Reverse Barbell Curl": "10–15",
    "Reverse Dumbbell Curl": "10–15",
    "Smith Machine Squat": "6–10",
    "Machine Squat": "8–12",
    "Leg Press": "10–20",
    "Romanian Deadlift": "6–10",
    "Leg Extension": "12–20",
    "Leg Curl": "10–15",
    "Hip Thrust": "8–12",
    "Walking Dumbbell Lunges": "10–20",
    "Stationary Lunges": "8–15",
    "Hip Adduction": "12–20",
    "Hip Abduction": "12–20",
    "Calf Raises Standing": "12–20",
    "Calf Raises Sitting": "15–25",
    "Barbell Overhead Press": "5–8",
    "Dumbbell Overhead Press": "6–10",
    "Machine Shoulder Press": "8–12",
    "Cable Lateral Raise": "12–20",
    "Machine Lateral Raise": "15–25",
    "Rear Delt Machine Fly": "12–20",
    "Rope Face Pull": "12–20",
    "Wrist Flexion - Dumbbell": "12–20",
    "Wrist Extension - Dumbbell": "12–20",
    "Wrist Flexion - Machine": "15–25",
    "Wrist Extension - Machine": "15–25",
    "Forearm Roller": "30–60s",
    "Forearm Ulnar + Radial Deviation": "15–25",
    "Forearm Ulnar Deviation": "15–25",
    "Forearm Radial Deviation": "15–25",
    "Farmer's Walk": "20–60s",
    "Lower Abs": "12–20",
    "V Tucks": "12–20",
    "Crunches A": "15–25",
    "Crunches B": "12–20"
}

# --- BODYWEIGHT EXERCISES ---
BW_EXERCISES = {
    "Dips",
    "Pull Ups",
    "Neutral-Grip Pull-Ups",
    "Lower Abs",
    "V Tucks",
    "Crunches A",
    "Crunches B",
    "Hyper Extension",
}

# --- DEFAULT PLANS ---
HARSH_DEFAULT_PLAN = """
Chest & Triceps 1
Flat Barbell Press
Triceps Rod Pushdown
Incline Dumbbell Press
Dumbbell Overhead Extension
Low Cable Fly
Lower Abs

Chest & Triceps 2
Incline Barbell Press
Triceps Rope Pushdown
Flat Dumbbell Press
Skull Crushers
Pec Deck Fly
Lower Abs

Chest & Triceps 3
Incline Dumbbell Press
Triceps Rod Pushdown
Flat Dumbbell Press
Skull Crushers
Dips
Lower Abs

Chest & Triceps 4
Incline Barbell Press
Triceps Rope Pushdown
Flat Barbell Press
Dumbbell Overhead Extension
Pec Deck Fly
Lower Abs

Back & Biceps 1
Neutral-Grip Pull-Ups
Barbell Curl
Wide-Grip Seated Row
Preacher Curl
Lat Dumbbell Rows
Hyper Extension
V Tucks

Back & Biceps 2
Deadlift
Pull Ups
Dumbbell Curl
Neutral-Grip Seated Row
Hammer Dumbbell Curl
Wide-Grip Chest-Supported Row
V Tucks

Back & Biceps 3
Neutral-Grip Lat Pulldown
Barbell Curl
Wide-Grip Seated Row
Hammer Rope Curl
Lat Dumbbell Rows
Hyper Extension
V Tucks

Back & Biceps 4
Deadlift
Lat Pulldown
Preacher Curl
Neutral-Grip Seated Row
Hammer Dumbbell Curl
Wide-Grip Chest-Supported Row
V Tucks

Legs 1
Smith Machine Squat
Romanian Deadlift
Leg Extension
Leg Curl
Hip Adduction
Calf Raises Standing

Legs 2
Leg Press
Hip Thrust
Walking Dumbbell Lunges
Leg Curl
Hip Abduction
Calf Raises Sitting

Legs 3
Machine Squat
Stationary Lunges
Leg Extension
Leg Curl
Hip Adduction
Calf Raises Standing

Legs 4
Leg Press
Romanian Deadlift
Hip Thrust
Leg Extension
Hip Abduction
Calf Raises Sitting

Arms 1
Barbell Overhead Press
Wrist Flexion - Dumbbell
Cable Lateral Raise
Reverse Barbell Curl
Rear Delt Machine Fly
Farmer's Walk
Crunches A

Arms 2
Dumbbell Overhead Press
Wrist Extension - Dumbbell
Machine Lateral Raise
Forearm Roller
Rear Delt Machine Fly
Rope Face Pull
Crunches B

Arms 3
Barbell Overhead Press
Wrist Flexion - Machine
Cable Lateral Raise
Reverse Dumbbell Curl
Rear Delt Machine Fly
Rope Face Pull
Crunches A

Arms 4
Machine Shoulder Press
Forearm Ulnar + Radial Deviation
Machine Lateral Raise
Forearm Roller
Rear Delt Machine Fly
Wrist Extension - Machine
Crunches B
"""

APURVA_DEFAULT_PLAN = """
Chest & Triceps 1
Incline Barbell Press
Flat Dumbbell Press
Low Cable Fly
Dumbbell Overhead Extension
Triceps Rod Pushdown
Lower Abs

Chest & Triceps 2
Flat Barbell Press
Incline Dumbbell Press
Pec Deck Fly
Dips
Triceps Rope Pushdown
Lower Abs

Back & Biceps 1
Neutral-Grip Pull-Ups
Lat Dumbbell Row
Wide-Grip Seated Row
Hyperextension
Barbell Curl
Hammer Rope Curl
V Tucks

Back & Biceps 2
Pull-Ups
Neutral-Grip Seated Row
Wide-Grip Seated Row
Deadlift
Dumbbell Curl
Machine Preacher Curl
V Tucks

Arms 1
Dumbbell Shoulder Press
Cable Lateral Raise
Rear Delt Machine Fly
Wrist Extension
Farmer’s Walk
Reverse Dumbbell Curl
Crunches A

Arms 2
Barbell Shoulder Press
Cable Lateral Raise
Rear Delt Machine Fly
Rope Face Pull
Wrist Flexion
Reverse Barbell Curl
Crunches B

Legs 1
Leg Press
Leg Curl
Walking Dumbbell Lunges
Hip Thrust
Standing Calf Raises
V Tucks

Legs 2
Barbell Squat
Leg Extension
Stationary Dumbbell Lunges
Romanian Deadlift
Seated Calf Raises
V Tucks
"""

DEFAULT_PLAN = HARSH_DEFAULT_PLAN

def get_workout_days(raw_text):
    """
    Parses the provided text string into a dictionary.
    Dynamic: Splits by double-newlines to separate blocks.
    """
    workout_days = {"workout": {}}

    def _norm_plan_line(s: str) -> str:
        s = str(s or "")
        s = s.replace("•", " ")
        s = s.replace("–", "-")
        s = s.replace("—", "-")
        s = s.replace("’", "'")
        s = re.sub(r"\s+", " ", s).strip()
        return s

    def _parse_header(line: str):
        line = _norm_plan_line(line)
        if not line:
            return None

        m = re.match(r"^session\s+(\d+)\s*[-:]\s*(.+)$", line, flags=re.IGNORECASE)
        if m:
            day_id = int(m.group(1))
            session_title = _norm_plan_line(m.group(2))
            return {
                "category": "Session",
                "day_id": day_id,
                "day_name": f"Session {day_id}",
                "session_title": session_title,
            }

        m = re.match(r"^session\s+(\d+)\s*$", line, flags=re.IGNORECASE)
        if m:
            day_id = int(m.group(1))
            return {
                "category": "Session",
                "day_id": day_id,
                "day_name": f"Session {day_id}",
                "session_title": "",
            }

        m = re.match(r"^(.+?)\s+(\d+)$", line)
        if m:
            day_id = int(m.group(2))
            category = _norm_plan_line(m.group(1))
            return {
                "category": category,
                "day_id": day_id,
                "day_name": f"{category} {day_id}",
                "session_title": None,
            }

        return None

    current_header = None
    current_exercises = []
    current_heading = None
    headings = []
    heading_sessions = {}

    def _flush_current():
        nonlocal current_header, current_exercises
        if not current_header:
            return
        cat = current_header.get("category")
        day_name = current_header.get("day_name")
        if not cat or not day_name:
            current_header = None
            current_exercises = []
            return

        workout_days["workout"].setdefault(cat, {})[day_name] = list(current_exercises)
        if cat == "Session":
            titles = workout_days.setdefault("session_titles", {})
            day_id = current_header.get("day_id")
            title = current_header.get("session_title")
            if isinstance(day_id, int) and title:
                titles[str(day_id)] = str(title)

        current_header = None
        current_exercises = []

    for raw_line in str(raw_text or "").split("\n"):
        line = _norm_plan_line(raw_line)
        if not line:
            continue

        m = re.match(r"^(cycle|heading)\s+(\d+)\s*$", line, flags=re.IGNORECASE)
        if m:
            _flush_current()
            prefix = str(m.group(1) or "").strip().title()
            num = int(m.group(2))
            current_heading = f"{prefix} {num}"
            if current_heading not in headings:
                headings.append(current_heading)
            heading_sessions.setdefault(current_heading, [])
            continue

        header = _parse_header(line)
        if header:
            _flush_current()
            current_header = header
            current_exercises = []

            if current_heading and header.get("category") == "Session":
                day_id = header.get("day_id")
                if isinstance(day_id, int):
                    s_list = heading_sessions.setdefault(current_heading, [])
                    if day_id not in s_list:
                        s_list.append(day_id)
            continue

        if current_header:
            current_exercises.append(line)

    _flush_current()

    if headings:
        workout_days["headings"] = list(headings)
        workout_days["heading_sessions"] = dict(heading_sessions)
    return workout_days