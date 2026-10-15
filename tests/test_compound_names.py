"""A word written as one or two is the same exercise ("Push Down" / "Pushdown"), but only when some
name already writes it as one: words are never invented."""
import unittest
from datetime import datetime

import tests.test_route_regressions as base
from models import Plan, WorkoutLog
from services.compound_report import new_merges_for_names
from services.exercise_matching import build_name_index, resolve_equivalent_names, same_exercise
from services.exercise_rename import detect_renames, rename_exercise
from services.retrieve import get_custom_retrieval_exercise_catalog
from services.stats import _resolve_exercise_aliases


class TestSameExercise(unittest.TestCase):
    def test_a_word_written_as_one_or_two(self):
        for a, b in [
            ("Rod Tricep Push-Down", "Rod Triceps Pushdown"),
            ("Tricep Rod Push Down", "Triceps Rod Pushdown"),
            ("Pull Up", "Pullups"),
            ("Push Ups", "Pushups"),
            ("Dead Lift", "Deadlift"),
        ]:
            self.assertTrue(same_exercise(a, b), (a, b))

    def test_nothing_else_is_merged(self):
        for a, b in [
            ("Rod Push Down", "Rope Pushdown"),     # another word differs
            ("Rod Pushdown", "Push"),               # a part isn't the whole
            ("Seated Cable Row", "Seatedcable"),    # a word is missing
            ("Lat Pull Down", "Lat Pull"),
        ]:
            self.assertFalse(same_exercise(a, b), (a, b))

    def test_only_two_neighbouring_words_join(self):
        self.assertFalse(same_exercise("Seated Cable Row", "Seatedcablerow"))

    def test_a_new_log_finds_its_history(self):
        index = build_name_index(["Rod Triceps Pushdown", "Bench Press"])
        self.assertEqual(resolve_equivalent_names("Rod Tricep Push-Down", index), ["Rod Triceps Pushdown"])
        self.assertEqual(resolve_equivalent_names("Rod Push", index), [])


class TestReport(unittest.TestCase):
    def test_lists_only_what_would_newly_merge(self):
        merges = new_merges_for_names([
            "Rod Triceps Pushdown", "Tricep Rod Push-Down", "Triceps Rod Pushdown",  # last two: already one
            "Pull Up", "Pullups", "Bench Press", "Rope Pushdown",
        ])
        self.assertEqual(sorted(merges), [["Pull Up", "Pullups"],
                                          ["Rod Triceps Pushdown", "Tricep Rod Push-Down", "Triceps Rod Pushdown"]])


class TestCompoundNamesInTheApp(unittest.TestCase):
    setUp = base.TestRouteRegressions.setUp
    tearDown = base.TestRouteRegressions.tearDown
    _create_logged_in_user = base.TestRouteRegressions._create_logged_in_user

    def _log(self, user, name, day):
        self.session.add(WorkoutLog(user_id=user.id, date=datetime(2026, 9, day), workout_name="Arms",
                                    exercise=name, exercise_string=f"{name}\n40, 10",
                                    sets_json={"weights": [40], "reps": [10]}, top_weight=40, top_reps=10,
                                    estimated_1rm=53))
        self.session.commit()

    def test_stats_shows_one_chart(self):
        user = self._create_logged_in_user(username="cn_stats")
        self._log(user, "Rod Triceps Pushdown", 1)
        self._log(user, "Tricep Rod Push-Down", 2)
        self._log(user, "Rope Pushdown", 3)
        self.assertEqual(sorted(_resolve_exercise_aliases(self.session, user, "Rod Triceps Pushdown")),
                         ["Rod Triceps Pushdown", "Tricep Rod Push-Down"])

    def test_the_picker_shows_one_row(self):
        user = self._create_logged_in_user(username="cn_picker")
        self.session.add(Plan(user_id=user.id, text_content="Arms 1\nRod Triceps Pushdown\n"))
        self.session.commit()
        self._log(user, "Rod Tricep Push-Down", 1)
        names = [i["name"] for i in get_custom_retrieval_exercise_catalog(self.session, user)
                 if "push" in i["name"].lower() and "rod" in i["name"].lower()]
        self.assertEqual(names, ["Rod Triceps Pushdown"])

    def test_a_rename_takes_the_other_spelling_too(self):
        user = self._create_logged_in_user(username="cn_rename")
        self._log(user, "Rod Triceps Pushdown", 1)
        self._log(user, "Tricep Rod Push-Down", 2)
        rename_exercise(self.session, user, "Rod Triceps Pushdown", "Straight-Bar Triceps Pushdown")
        names = {r[0] for r in self.session.query(WorkoutLog.exercise).filter_by(user_id=user.id)}
        self.assertEqual(names, {"Straight-Bar Triceps Pushdown"})

    def test_respelling_in_the_plan_is_not_asked_about(self):
        user = self._create_logged_in_user(username="cn_respell")
        self._log(user, "Pull Up", 1)
        self.assertEqual(detect_renames(self.session, user, ["Pull Up", "Plank"], ["Pullups", "Plank"]), [])


if __name__ == "__main__":
    unittest.main()
