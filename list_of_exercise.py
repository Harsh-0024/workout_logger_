import re

list_of_exercises = [
    "Incline Dumbbell Press",
    "Triceps Rod Pushdown",
    "Flat Barbell Press",
    "Dumbbell Overhead Extension",
    "Low Cable Fly",
    "Lower Abs",
    "Incline Barbell Press",
    "Triceps Rope Pushdown",
    "Flat Dumbbell Press",
    "Barbell Overhead Extension",
    "Peck Deck Fly",
    "Dips",
    "Neutral-Grip Pull-Ups",
    "Barbell Curl",
    "Lat Dumbbell Rows",
    "Preacher Curl",
    "Wide-Grip Seated Row",
    "Hyper Extension",
    "V Tucks",
    "Pull Ups",
    "Dumbbell Curl",
    "Deadlift",
    "Hammer Dumbbell Curl",
    "Neutral-Grip Seated Row",
    "Neutral-Grip Lat Pulldown",
    "Hammer Rope Curl",
    "Lat Pulldown",
    "Smith Machine Squat",
    "Romanian Deadlift",
    "Leg Extension",
    "Leg Curl",
    "Hip Adduction",
    "Calf Raises Standing",
    "Leg Press",
    "Hip Thrust",
    "Walking Dumbbell Lunges",
    "Hip Abduction",
    "Calf Raises Sitting",
    "Machine Squat",
    "Stationary Lunges",
    "Barbell Overhead Press",
    "Reverse Barbell Curl",
    "Cable Lateral Raise",
    "Wrist Flexion - Dumbbell",
    "Rear Delt Machine Fly",
    "Rope Face Pull",
    "Crunches A",
    "Dumbbell Overhead Press",
    "Farmer's Walk",
    "Machine Lateral Raise",
    "Wrist Extension - Dumbbell",
    "Forearm Ulnar Deviation",
    "Crunches B",
    "Machine Shoulder Press",
    "Forearm Roller",
    "Forearm Radial Deviation",
    "Reverse Dumbbell Curl",
    "Wrist Flexion - Machine",
    "Wrist Extension - Machine"
]

_workout_plan_text = """
Chest & Triceps 1
1. Incline Dumbbell Press
2. Triceps Rod Pushdown
3. Flat Barbell Press
4. Dumbbell Overhead Extension
5. Low Cable Fly
6. Lower Abs
break
Chest & Triceps 2
1. Incline Barbell Press
2. Triceps Rope Pushdown
3. Flat Dumbbell Press
4. Barbell Overhead Extension
5. Peck Deck Fly
6. Lower Abs
break
Chest & Triceps 3
1. Incline Dumbbell Press
2. Triceps Rod Pushdown
3. Flat Dumbbell Press
4. Barbell Overhead Extension
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
3. Lat Dumbbell Rows
4. Preacher Curl
5. Wide-Grip Seated Row
6. Hyper Extension
7. V Tucks
break
Back & Biceps 2
1. Pull Ups
2. Dumbbell Curl
3. Deadlift
4. Hammer Dumbbell Curl
5. Neutral-Grip Seated Row
6. V Tucks
break
Back & Biceps 3
1. Neutral-Grip Lat Pulldown
2. Barbell Curl
3. Lat Dumbbell Rows
4. Hammer Rope Curl
5. Wide-Grip Seated Row
6. Hyper Extension
7. V Tucks
break
Back & Biceps 4
1. Lat Pulldown
2. Preacher Curl
3. Deadlift
4. Hammer Dumbbell Curl
5. Neutral-Grip Seated Row
6. V Tucks
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
2. Reverse Barbell Curl
3. Cable Lateral Raise
4. Wrist Flexion - Dumbbell
5. Rear Delt Machine Fly
6. Rope Face Pull
7. Crunches A
break
Arms 2
1. Dumbbell Overhead Press
2. Farmer's Walk
3. Machine Lateral Raise
4. Wrist Extension - Dumbbell
5. Rear Delt Machine Fly
6. Forearm Ulnar Deviation
7. Crunches B
break
Arms 3
1. Machine Shoulder Press
2. Forearm Roller
3. Cable Lateral Raise
4. Forearm Radial Deviation
5. Rear Delt Machine Fly
6. Rope Face Pull
7. Crunches A
break
Arms 4
1. Dumbbell Overhead Press
2. Reverse Dumbbell Curl
3. Machine Lateral Raise
4. Wrist Flexion - Machine
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
            ex = re.sub(r'^\s*\d+\s*(?:[.)\-:]?)\s*', '', line)
            exercises_list.append(ex)

        workout_days["workout"][workout_day_name][workout_name] = exercises_list

    return workout_days
