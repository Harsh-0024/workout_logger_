import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base, Plan, RepRange, User, UserRole, WorkoutLog
from services.best_scoring import best_workout_strength_score, coerce_equal_len_sets
from services.exercise_matching import build_name_index, resolve_equivalent_names
from services.logging import _parse_rep_target_sets
from services.retrieve import generate_retrieve_output
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


class TestRepTargetParsing(unittest.TestCase):
    def test_parse_rep_target_sets_with_normalization(self):
        rep_text = """
        Stationary Reverse Lunges: 2, 8-15
        Wrist Extension – Dumbbell: 4, 12-20
        Flat Dumbbell Press: 8-12
        """
        parsed = _parse_rep_target_sets(rep_text)
        self.assertEqual(parsed.get("stationary reverse lunges"), 2)
        self.assertEqual(parsed.get("wrist extension dumbbell"), 4)
        # No explicit set-count prefix here, so it should not be present.
        self.assertNotIn("flat dumbbell press", parsed)


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


if __name__ == "__main__":
    unittest.main()
