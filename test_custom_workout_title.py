from services.exercise_matching import normalize_exercise_name
from services.workout_title import classify_by_name, infer_workout_title, split_title


def _keys(name):
    return [normalize_exercise_name(name)]


SESSIONS = [
    ("Chest & Biceps", ["Hanging Leg Raises", "Flat Barbell Press", "Barbell Curl", "Low Cable Fly"]),
    ("Shoulders & Forearms", ["Crunches A", "Barbell Overhead Press", "Reverse Barbell Curl", "Rear Delt Fly"]),
    ("Back & Triceps", ["Lat Pulldown", "Rod Triceps Pushdown", "Mystery Machine"]),
    ("Legs", ["Hanging Oblique Knee Raises", "Leg Press", "Hip Thrust"]),
    ("Chest & Triceps", ["Hanging Leg Raises", "Incline Barbell Press", "Dips", "Low Cable Fly"]),
    ("Back & Biceps", ["Deadlift", "Preacher Curl"]),
]


def infer(names):
    return infer_workout_title(names, SESSIONS, key_fn=_keys)


def test_split_title_handles_session_prefix_and_separators():
    assert split_title("Session 3 – Back & Triceps") == ["Back", "Triceps"]
    assert split_title("Chest & tricep and Legs") == ["Chest", "Triceps", "Legs"]


def test_plan_and_name_combine_into_title_in_selection_order():
    assert infer(["Flat Barbell Press", "Low Cable Fly", "Rod Triceps Pushdown", "Leg Press", "Hip Thrust"]) == "Chest & Triceps & Legs"


def test_exercise_shared_by_two_titles_uses_the_common_part():
    assert infer(["Low Cable Fly"]) == "Chest"


def test_abs_is_always_included_and_placed_last():
    # Abs never appears in these titles, but it is still shown, at the end.
    assert infer(["Hanging Leg Raises", "Barbell Curl"]) == "Biceps & Abs"
    assert infer(["Hanging Oblique Knee Raises"]) == "Abs"
    assert infer(["Crunches A", "Leg Press", "Hip Thrust"]) == "Legs & Abs"


def test_unknown_name_falls_back_to_plan_title():
    assert infer(["Mystery Machine"]) == "Back & Triceps"


def test_exercise_not_in_plan_uses_name_classifier():
    assert infer(["Barbell Overhead Extension", "Bent-Over Dumbbell Rear Delt Fly"]) == "Triceps & Shoulders"


def test_nothing_known_returns_none():
    assert infer(["Totally Unknown"]) is None


def test_user_labels_that_are_not_muscles_are_respected():
    sessions = [("Arms", ["Barbell Overhead Press"]), ("Legs", ["Leg Press"])]
    assert infer_workout_title(["Barbell Overhead Press", "Leg Press"], sessions, key_fn=_keys) == "Arms & Legs"


def test_classifier_edge_cases():
    assert classify_by_name("Flat Barbell Press") == {"Chest"}
    assert classify_by_name("T-bar Wide-Grip Row") == {"Back"}
    assert classify_by_name("Leg Curl") == {"Legs"}
    assert classify_by_name("Reverse Barbell Curl") == {"Forearms"}
    assert classify_by_name("Back-Assisted Leg Raises") == {"Abs"}
    assert classify_by_name("Upright Rows") == {"Shoulders"}
    assert classify_by_name("Chest-Supported Machine High Row") == {"Back"}
