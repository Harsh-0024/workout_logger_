import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from config import Config
from models import Base, Plan, RepRange, User, UserRole, WorkoutLog
from services.best_scoring import (
    best_workout_strength_score,
    coerce_equal_len_sets,
    compare_strength_workouts,
)
from services.exercise_matching import build_name_index, normalize_exercise_name, resolve_equivalent_names, token_signature
from services.logging import (
    _parse_rep_target_sets,
    comparison_set_count,
    resolve_target_sets_for_exercise,
)
from services.retrieve import generate_retrieve_output, get_effective_plan_text
from services.retrieve import _build_best_sets_line_from_logs


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def distinct(self):
        return self

    def all(self):
        return list(self._rows)


class _FakeDB:
    def __init__(self, rows):
        self._rows = rows

    def query(self, *_args, **_kwargs):
        return _FakeQuery(self._rows)


class _FakeLog(SimpleNamespace):
    pass


class TestBestScoring(unittest.TestCase):
    def test_coerce_equal_len_filters_invalid_sets(self):
        weights, reps = coerce_equal_len_sets([10, "x", -1, 20], [5, 8, 10, 0])
        self.assertEqual(weights, [10.0])
        self.assertEqual(reps, [5])

    def test_strength_score_uses_requested_top_n(self):
        sets_json = {"weights": [20, 18, 16], "reps": [10, 10, 10]}
        score_n1 = best_workout_strength_score(sets_json, top_n=1)["score"]
        score_n3 = best_workout_strength_score(sets_json, top_n=3)["score"]
        self.assertGreater(score_n3, score_n1)

    def test_compare_strength_workouts_prefers_better_second_best_when_peak_tied(self):
        previous = {"weights": [30, 25, 25], "reps": [6, 12, 11]}
        current = {"weights": [30, 27.8, 25], "reps": [6, 8, 9]}
        result = compare_strength_workouts(previous, current, top_n=3)
        self.assertEqual(result["cmp"], 1)
        self.assertEqual(result["reason"], "consistency")
        self.assertEqual(result["diff_index"], 1)

    def test_compare_strength_workouts_uses_weight_as_final_tiebreaker(self):
        previous = {"weights": [27, 25, 24], "reps": [10, 12, 15]}
        current = {"weights": [30, 27, 25], "reps": [6, 10, 12]}
        result = compare_strength_workouts(previous, current, top_n=3)
        self.assertEqual(result["cmp"], 1)
        self.assertEqual(result["reason"], "consistency")


class TestExerciseMatching(unittest.TestCase):
    def test_order_insensitive_match_only_when_unambiguous(self):
        idx = build_name_index(["Calf Raises Standing"])
        self.assertEqual(
            resolve_equivalent_names("Standing Calf Raises", idx),
            ["Calf Raises Standing"],
        )

    def test_order_insensitive_match_blocked_when_ambiguous(self):
        idx = build_name_index(["Calf Raises Standing", "Standing Calf Raises"])
        self.assertEqual(resolve_equivalent_names("Raises Standing Calf", idx), [])

    def test_order_insensitive_match_allows_duplicate_normalized_originals(self):
        idx = build_name_index(["Wrist Flexion - Dumbbell", "Wrist Flexion – Dumbbell"])
        self.assertEqual(
            resolve_equivalent_names("Dumbbell Wrist Flexion", idx),
            ["Wrist Flexion - Dumbbell", "Wrist Flexion – Dumbbell"],
        )

    def test_exact_match_includes_reordered_token_aliases(self):
        idx = build_name_index(["Machine Rear Delt Fly", "Rear Delt Machine Fly"])
        self.assertEqual(
            resolve_equivalent_names("Machine Rear Delt Fly", idx),
            ["Machine Rear Delt Fly", "Rear Delt Machine Fly"],
        )

    def test_plural_dips_matches_dip(self):
        idx = build_name_index(["Machine Dip"])
        self.assertEqual(resolve_equivalent_names("Machine Dips", idx), ["Machine Dip"])

    def test_tricep_singular_matches_triceps(self):
        idx = build_name_index(["Triceps Rope Pushdown"])
        self.assertEqual(
            resolve_equivalent_names("Tricep Rope Pushdown", idx),
            ["Triceps Rope Pushdown"],
        )

    def test_oh_matches_overhead(self):
        idx = build_name_index(["Single-Arm Dumbbell Oh Extension"])
        self.assertEqual(
            resolve_equivalent_names("Single-Arm Dumbbell Overhead Extension", idx),
            ["Single-Arm Dumbbell Oh Extension"],
        )

    def test_forearm_is_identity_token(self):
        self.assertNotEqual(
            normalize_exercise_name("Barbell Forearm Radial Deviation"),
            normalize_exercise_name("Barbell Radial Deviation"),
        )
        self.assertNotEqual(
            token_signature("Barbell Forearm Radial Deviation"),
            token_signature("Barbell Radial Deviation"),
        )

    def test_forearm_exercises_do_not_resolve_as_equivalent(self):
        idx = build_name_index([
            "Barbell Forearm Radial Deviation",
            "Barbell Radial Deviation",
        ])
        self.assertEqual(
            resolve_equivalent_names("Barbell Forearm Radial Deviation", idx),
            ["Barbell Forearm Radial Deviation"],
        )
        self.assertEqual(
            resolve_equivalent_names("Barbell Radial Deviation", idx),
            ["Barbell Radial Deviation"],
        )
        self.assertEqual(
            resolve_equivalent_names("Forearm Barbell Radial Deviation", idx),
            ["Barbell Forearm Radial Deviation"],
        )

    def test_ordering_does_not_matter_when_unambiguous(self):
        idx = build_name_index(["Single-Arm Cable Triceps Pushdown"])
        self.assertEqual(
            resolve_equivalent_names("Tricep Single-Arm Cable Pushdown", idx),
            ["Single-Arm Cable Triceps Pushdown"],
        )


class TestRepTargetParsing(unittest.TestCase):
    def test_parse_rep_target_sets_with_normalization(self):
        rep_text = """
        Stationary Reverse Lunges: 2, 8-15
        Wrist Extension – Dumbbell: 4, 12-20
        Flat Dumbbell Press: 8-12
        """
        parsed = _parse_rep_target_sets(rep_text)
        # Plural forms are normalized away for matching consistency (e.g., lunges -> lunge).
        self.assertEqual(parsed.get("stationary reverse lunge"), 2)
        self.assertEqual(parsed.get("wrist extension dumbbell"), 4)
        # No explicit set-count prefix here, so it should not be present.
        self.assertNotIn("flat dumbbell press", parsed)


class TestSetCountRule(unittest.TestCase):
    def test_uses_sets_json_count_before_raw_string_count(self):
        count = comparison_set_count(
            {"weights": [5, 5, 5], "reps": [21, 20]},
            "V Tucks\n5, 21 20",
        )
        self.assertEqual(count, 3)

    def test_no_explicit_uses_json_count_above_default(self):
        count = comparison_set_count(
            {"weights": [5, 5], "reps": [21, 20, 20, 20]},
            "V Tucks\n5 5, 21 20 20 20",
        )
        self.assertEqual(count, 4)

    def test_explicit_count_above_json_count_wins(self):
        count = comparison_set_count(
            {"weights": [5, 5], "reps": [21, 20]},
            "V Tucks - [4]\n5 5, 21 20",
        )
        self.assertEqual(count, 4)

    def test_json_count_above_explicit_count_wins(self):
        count = comparison_set_count(
            {"weights": [5, 5, 5, 5], "reps": [21, 20, 20, 20]},
            "V Tucks - [2]\n5 5 5 5, 21 20 20 20",
        )
        self.assertEqual(count, 4)

    def test_empty_defaults_to_three_sets(self):
        self.assertEqual(comparison_set_count(None, ""), 3)

    def test_resolved_target_uses_larger_of_explicit_and_inferred(self):
        target_sets, strict = resolve_target_sets_for_exercise(
            exercise_name="V Tucks",
            exercise_string="V Tucks - [2]\n5 5 5 5, 21 20 20 20",
            inferred_set_count=4,
            default_sets=3,
        )
        self.assertEqual(target_sets, 4)
        self.assertTrue(strict)


class TestRetrieveBestLineSelection(unittest.TestCase):
    def _user(self):
        return SimpleNamespace(id=1, bodyweight=80)

    def test_prefers_ge_n_sets_even_if_lt_n_scores_higher(self):
        now = datetime.now()
        logs = [
            _FakeLog(
                user_id=1,
                exercise="Walking Dumbbell Lunges",
                date=now,
                sets_json={"weights": [20], "reps": [6]},
                exercise_string="Walking Dumbbell Lunges - [1, 10-20]\n20, 6",
                bodyweight=80,
            ),
            _FakeLog(
                user_id=1,
                exercise="Walking Dumbbell Lunges",
                date=now - timedelta(days=1),
                sets_json={"weights": [10, 10, 10], "reps": [6, 6, 6]},
                exercise_string="Walking Dumbbell Lunges - [10-20]\n10 10 10, 6 6 6",
                bodyweight=80,
            ),
        ]
        line = _build_best_sets_line_from_logs(
            _FakeDB(logs), self._user(), "Walking Dumbbell Lunges", target_sets=3
        )
        self.assertEqual(line, "10, 6")

    def test_fallback_to_lt_n_when_no_ge_n_exists(self):
        now = datetime.now()
        logs = [
            _FakeLog(
                user_id=1,
                exercise="Flat Dumbbell Press",
                date=now - timedelta(days=2),
                sets_json={"weights": [27.5, 27.5], "reps": [8, 7]},
                exercise_string="Flat Dumbbell Press - [2, 8-12]\n27.5 27.5, 8 7",
                bodyweight=80,
            ),
            _FakeLog(
                user_id=1,
                exercise="Flat Dumbbell Press",
                date=now - timedelta(days=1),
                sets_json={"weights": [30], "reps": [6]},
                exercise_string="Flat Dumbbell Press - [1, 8-12]\n30, 6",
                bodyweight=80,
            ),
        ]
        line = _build_best_sets_line_from_logs(
            _FakeDB(logs), self._user(), "Flat Dumbbell Press", target_sets=3
        )
        # In fallback mode we still choose the highest-scoring stimulus among <N logs.
        self.assertEqual(line, "27.5, 8 7")

    def test_output_order_is_heaviest_to_lightest_with_paired_reps(self):
        now = datetime.now()
        logs = [
            _FakeLog(
                user_id=1,
                exercise="Flat Dumbbell Press",
                date=now,
                sets_json={"weights": [27.5, 30, 27.5], "reps": [10, 6, 7]},
                exercise_string="Flat Dumbbell Press - [8-12]\n27.5 30 27.5, 10 6 7",
                bodyweight=80,
            ),
        ]
        line = _build_best_sets_line_from_logs(
            _FakeDB(logs), self._user(), "Flat Dumbbell Press", target_sets=3
        )
        self.assertEqual(line, "30 27.5, 6 10 7")


class TestRetrieveIntegration(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        SessionLocal = sessionmaker(bind=self.engine)
        self.db = SessionLocal()

        self.user = User(
            username="u1",
            role=UserRole.USER,
            is_verified=True,
            bodyweight=80,
        )
        self.db.add(self.user)
        self.db.flush()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_generate_retrieve_output_prefers_ge_n_and_formats_heaviest_first(self):
        plan = Plan(
            user_id=self.user.id,
            text_content="\n".join(
                [
                    "Session 13 - Chest & Triceps",
                    "Flat Dumbbell Press",
                ]
            ),
        )
        rep = RepRange(
            user_id=self.user.id,
            text_content="Flat Dumbbell Press: 3, 8-12",
        )
        self.db.add(plan)
        self.db.add(rep)

        now = datetime.now()
        # >=N candidate (N=3): should be selected even if a <N candidate has higher score.
        self.db.add(
            WorkoutLog(
                user_id=self.user.id,
                date=now - timedelta(days=1),
                workout_name="Session 13 - Chest & Triceps",
                exercise="Flat Dumbbell Press",
                exercise_string="Flat Dumbbell Press - [8-12]\n22.5 25 22.5, 10 6 7",
                sets_json={"weights": [22.5, 25, 22.5], "reps": [10, 6, 7]},
                bodyweight=80,
            )
        )
        # <N candidate with stronger top single set, should be ignored while >=N exists.
        self.db.add(
            WorkoutLog(
                user_id=self.user.id,
                date=now,
                workout_name="Session 13 - Chest & Triceps",
                exercise="Flat Dumbbell Press",
                exercise_string="Flat Dumbbell Press - [1, 8-12]\n45, 3",
                sets_json={"weights": [45], "reps": [3]},
                bodyweight=80,
            )
        )
        self.db.commit()

        output, exercise_count, set_count = generate_retrieve_output(self.db, self.user, "Session", 13)
        self.assertEqual(exercise_count, 1)
        self.assertEqual(set_count, 3)
        self.assertRegex(output.splitlines()[0], r"^\d{1,2}/\d{1,2}/\d{2}\b")
        self.assertIn("Flat Dumbbell Press - [3, 8-12]", output)
        self.assertIn("25 22.5, 6 10 7", output)
        self.assertNotIn("45, 3", output)

    def test_generate_retrieve_output_falls_back_to_lt_n_when_needed(self):
        plan = Plan(
            user_id=self.user.id,
            text_content="\n".join(
                [
                    "Session 6 - Back & Biceps",
                    "Dumbbell Curl",
                ]
            ),
        )
        rep = RepRange(
            user_id=self.user.id,
            text_content="Dumbbell Curl: 3, 8-12",
        )
        self.db.add(plan)
        self.db.add(rep)
        self.db.add(
            WorkoutLog(
                user_id=self.user.id,
                date=datetime.now(),
                workout_name="Session 6 - Back & Biceps",
                exercise="Dumbbell Curl",
                exercise_string="Dumbbell Curl - [2, 8-12]\n12.5 10, 8 10",
                sets_json={"weights": [12.5, 10], "reps": [8, 10]},
                bodyweight=80,
            )
        )
        self.db.commit()

        output, exercise_count, set_count = generate_retrieve_output(self.db, self.user, "Session", 6)
        self.assertEqual(exercise_count, 1)
        self.assertEqual(set_count, 3)
        self.assertIn("Dumbbell Curl - [3, 8-12]", output)
        # fallback to <N keeps the best available history line
        self.assertIn("12.5 10, 8 10", output)

    def test_generate_retrieve_output_matches_dash_variant_reordered_exercise(self):
        plan = Plan(
            user_id=self.user.id,
            text_content="\n".join(
                [
                    "Session 2 - Shoulders & Forearms",
                    "Dumbbell Wrist Flexion",
                ]
            ),
        )
        rep = RepRange(
            user_id=self.user.id,
            text_content="Dumbbell Wrist Flexion: 2, 12-20",
        )
        self.db.add(plan)
        self.db.add(rep)
        self.db.add(
            WorkoutLog(
                user_id=self.user.id,
                date=datetime.now() - timedelta(days=1),
                workout_name="Session 2 - Shoulders & Forearms",
                exercise="Wrist Flexion – Dumbbell",
                exercise_string="Wrist Flexion – Dumbbell - [12–20]\n13.75 12.5 10, 16 20 20",
                sets_json={"weights": [13.75, 12.5, 10], "reps": [16, 20, 20]},
                bodyweight=80,
            )
        )
        self.db.add(
            WorkoutLog(
                user_id=self.user.id,
                date=datetime.now(),
                workout_name="Session 2 - Shoulders & Forearms",
                exercise="Wrist Flexion - Dumbbell",
                exercise_string="Wrist Flexion - Dumbbell - [12–20]\n15 13.8 12.5, 14 18 21",
                sets_json={"weights": [15, 13.8, 12.5], "reps": [14, 18, 21]},
                bodyweight=80,
            )
        )
        self.db.commit()

        output, exercise_count, set_count = generate_retrieve_output(self.db, self.user, "Session", 2)

        self.assertEqual(exercise_count, 1)
        self.assertEqual(set_count, 2)
        self.assertIn("Dumbbell Wrist Flexion - [2, 12-20]", output)
        self.assertIn("15 13.8, 14 18", output)
        self.assertNotIn("1, 1", output)

    def test_follow_admin_plan_prefers_non_empty_admin_plan(self):
        admin_one = User(
            username="admin_one",
            role=UserRole.ADMIN,
            is_verified=True,
        )
        admin_two = User(
            username="admin_two",
            role=UserRole.ADMIN,
            is_verified=True,
        )
        self.db.add_all([admin_one, admin_two])
        self.db.flush()

        self.db.add(Plan(user_id=admin_one.id, text_content=""))
        self.db.add(
            Plan(
                user_id=admin_two.id,
                text_content="\n".join([
                    "Session 3 - Legs",
                    "Hack Squat",
                ]),
            )
        )

        self.user.follow_admin_plan = True
        self.db.commit()

        effective = get_effective_plan_text(self.db, self.user)
        self.assertIn("Session 3 - Legs", effective)
        self.assertIn("Hack Squat", effective)

    def test_follow_admin_plan_prefers_configured_admin_email(self):
        original_admin_email = Config.ADMIN_EMAIL
        try:
            Config.ADMIN_EMAIL = "preferred_admin@example.com"

            preferred_admin = User(
                username="preferred_admin",
                email="preferred_admin@example.com",
                role=UserRole.ADMIN,
                is_verified=True,
            )
            other_admin = User(
                username="other_admin",
                email="other_admin@example.com",
                role=UserRole.ADMIN,
                is_verified=True,
            )
            self.db.add_all([other_admin, preferred_admin])
            self.db.flush()

            self.db.add(
                Plan(
                    user_id=other_admin.id,
                    text_content="\n".join([
                        "Session 1 - Other",
                        "Other Exercise",
                    ]),
                )
            )
            self.db.add(
                Plan(
                    user_id=preferred_admin.id,
                    text_content="\n".join([
                        "Session 1 - Preferred",
                        "Preferred Exercise",
                    ]),
                )
            )

            self.user.follow_admin_plan = True
            self.db.commit()

            effective = get_effective_plan_text(self.db, self.user)
            self.assertIn("Session 1 - Preferred", effective)
            self.assertIn("Preferred Exercise", effective)
            self.assertNotIn("Other Exercise", effective)
        finally:
            Config.ADMIN_EMAIL = original_admin_email


if __name__ == "__main__":
    unittest.main()
