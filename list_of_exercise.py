import re

# Canonical list of exercises
list_of_exercises = [
    "Barbell Curl",
    "Barbell Overhead Extension",
    "Barbell Overhead Press",
    "Cable Lateral Raise",
    "Calf Raises Sitting",
    "Calf Raises Standing",
    "Crunches A",
    "Crunches B",
    "Deadlift",
    "Dips",
    "Dumbbell Curl",
    "Dumbbell Overhead Extension",
    "Dumbbell Overhead Press",
    "Farmer's Walk",
    "Flat Barbell Press",
    "Flat Dumbbell Press",
    "Forearm Radial Deviation",
    "Forearm Roller",
    "Forearm Ulnar Deviation",
    "Hammer Dumbbell Curl",
    "Hammer Rope Curl",
    "Hip Abduction",
    "Hip Adduction",
    "Hip Thrust",
    "Hyper Extension",
    "Incline Barbell Press",
    "Incline Dumbbell Press",
    "Lat Dumbbell Rows",
    "Lat Pulldown",
    "Leg Curl",
    "Leg Extension",
    "Leg Press",
    "Low Cable Fly",
    "Lower Abs",
    "Machine Lateral Raise",
    "Machine Shoulder Press",
    "Machine Squat",
    "Neutral-Grip Lat Pulldown",
    "Neutral-Grip Pull-Ups",
    "Neutral-Grip Seated Row",
    "Peck Deck Fly",
    "Preacher Curl",
    "Pull Ups",
    "Rear Delt Machine Fly",
    "Reverse Barbell Curl",
    "Reverse Dumbbell Curl",
    "Romanian Deadlift",
    "Rope Face Pull",
    "Skull Crushers",
    "Smith Machine Squat",
    "Stationary Lunges",
    "Triceps Rod Pushdown",
    "Triceps Rope Pushdown",
    "V Tucks",
    "Walking Dumbbell Lunges",
    "Wide-Grip Chest-Supported Row",
    "Wide-Grip Seated Row",
    "Wrist Extension - Dumbbell",
    "Wrist Extension - Machine",
    "Wrist Flexion - Dumbbell",
    "Wrist Flexion - Machine"
]

# --- NEW: REP RANGES CONFIGURATION ---
EXERCISE_REP_RANGES = {
    # Chest & Triceps
    "Flat Barbell Press": "5–8",
    "Incline Dumbbell Press": "6–10",
    "Incline Barbell Press": "5–8",
    "Flat Dumbbell Press": "8–12",
    "Low Cable Fly": "12–20",
    "Peck Deck Fly": "12–20",
    "Dips": "6–12",
    "Triceps Rod Pushdown": "10–15",
    "Triceps Rope Pushdown": "12–20",
    "Dumbbell Overhead Extension": "8–12",
    "Barbell Overhead Extension": "6–10",
    "Skull Crushers": "6–10",

    # Back & Biceps
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

    # Legs & Glutes
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

    # Shoulders
    "Barbell Overhead Press": "5–8",
    "Dumbbell Overhead Press": "6–10",
    "Machine Shoulder Press": "8–12",
    "Cable Lateral Raise": "12–20",
    "Machine Lateral Raise": "15–25",
    "Rear Delt Machine Fly": "12–20",
    "Rope Face Pull": "12–20",

    # Forearms & Grip
    "Wrist Flexion - Dumbbell": "12–20",
    "Wrist Extension - Dumbbell": "12–20",
    "Wrist Flexion - Machine": "15–25",
    "Wrist Extension - Machine": "15–25",
    "Forearm Roller": "30–60s",
    "Forearm Ulnar + Radial Deviation": "15–25",
    "Forearm Ulnar Deviation": "15–25",
    "Forearm Radial Deviation": "15–25",
    "Farmer's Walk": "20–60s",

    # Core
    "Lower Abs": "12–20",
    "V Tucks": "12–20",
    "Crunches A": "15–25",
    "Crunches B": "12–20"
}

_workout_plan_text = """
Chest & Triceps 1
1. Flat Barbell Press
2. Triceps Rod Pushdown
3. Incline Dumbbell Press
4. Dumbbell Overhead Extension
5. Low Cable Fly
6. Lower Abs
break
Chest & Triceps 2
1. Incline Barbell Press
2. Triceps Rope Pushdown
3. Flat Dumbbell Press
4. Skull Crushers
5. Peck Deck Fly
6. Lower Abs
break
Chest & Triceps 3
1. Incline Dumbbell Press
2. Triceps Rod Pushdown
3. Flat Dumbbell Press
4. Skull Crushers
5. Dips
6. Lower Abs
break
Chest & Triceps 4
1. Incline Barbell Press
2. Triceps Rope Pushdown
3. Flat Barbell Press
4. Dumbbell Overhead Extension
5. Peck Deck Fly
6. Lower Abs
break
Back & Biceps 1
1. Neutral-Grip Pull-Ups
2. Barbell Curl
3. Wide-Grip Seated Row
4. Preacher Curl
5. Lat Dumbbell Rows
6. Hyper Extension
7. V Tucks
break
Back & Biceps 2
1. Deadlift
2. Pull Ups
3. Dumbbell Curl
4. Neutral-Grip Seated Row
5. Hammer Dumbbell Curl
6. Wide-Grip Chest-Supported Row
7. V Tucks
break
Back & Biceps 3
1. Neutral-Grip Lat Pulldown
2. Barbell Curl
3. Wide-Grip Seated Row
4. Hammer Rope Curl
5. Lat Dumbbell Rows
6. Hyper Extension
7. V Tucks
break
Back & Biceps 4
1. Deadlift
2. Lat Pulldown
3. Preacher Curl
4. Neutral-Grip Seated Row
5. Hammer Dumbbell Curl
6. Wide-Grip Chest-Supported Row
7. V Tucks
break
Legs 1
1. Smith Machine Squat
2. Romanian Deadlift
3. Leg Extension
4. Leg Curl
5. Hip Adduction
6. Calf Raises Standing
break
Legs 2
1. Leg Press
2. Hip Thrust
3. Walking Dumbbell Lunges
4. Leg Curl
5. Hip Abduction
6. Calf Raises Sitting
break
Legs 3
1. Machine Squat
2. Stationary Lunges
3. Leg Extension
4. Leg Curl
5. Hip Adduction
6. Calf Raises Standing
break
Legs 4
1. Leg Press
2. Romanian Deadlift
3. Hip Thrust
4. Leg Extension
5. Hip Abduction
6. Calf Raises Sitting
break
Arms 1
1. Barbell Overhead Press
2. Wrist Flexion - Dumbbell
3. Cable Lateral Raise
4. Reverse Barbell Curl
5. Rear Delt Machine Fly
6. Farmer's Walk
7. Crunches A
break
Arms 2
1. Dumbbell Overhead Press
2. Wrist Extension - Dumbbell
3. Machine Lateral Raise
4. Forearm Roller
5. Rear Delt Machine Fly
6. Rope Face Pull
7. Crunches B
break
Arms 3
1. Barbell Overhead Press
2. Wrist Flexion - Machine
3. Cable Lateral Raise
4. Reverse Dumbbell Curl
5. Rear Delt Machine Fly
6. Rope Face Pull
7. Crunches A
break
Arms 4
1. Machine Shoulder Press
2. Forearm Ulnar + Radial Deviation
3. Machine Lateral Raise
4. Forearm Roller
5. Rear Delt Machine Fly
6. Wrist Extension - Machine
7. Crunches B
"""

def get_workout_days():
    """Parses the text plan into a dictionary. Called on demand."""
    workout_days = {"workout": {}}

    sections = [s.strip() for s in _workout_plan_text.strip().split("break") if s.strip()]

    for block in sections:
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        workout_name = lines[0]

        match = re.search(r"\s+\d+$", workout_name)
        if not match:
            continue

        workout_day_name = workout_name[:match.start()].strip()

        if workout_day_name not in workout_days["workout"]:
            workout_days["workout"][workout_day_name] = {}

        exercises_list = []
        for line in lines[1:]:
            # Remove leading numbers/dots/bullets to get pure name
            ex = re.sub(r'^\s*\d+\s*(?:[.)\-:]?)\s*', '', line)
            exercises_list.append(ex)

        workout_days["workout"][workout_day_name][workout_name] = exercises_list

    return workout_days