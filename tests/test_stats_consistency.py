"""Stats: the overall data carries what the Consistency view and the strength tiles need."""
import unittest
from datetime import datetime

import tests.test_route_regressions as base
from models import WorkoutLog


class TestStatsConsistencyData(unittest.TestCase):
    setUp = base.TestRouteRegressions.setUp
    tearDown = base.TestRouteRegressions.tearDown
    _create_logged_in_user = base.TestRouteRegressions._create_logged_in_user

    def _log(self, user, day, title, exercise, weight, reps):
        self.session.add(WorkoutLog(
            user_id=user.id, date=day, workout_name=title, exercise=exercise,
            exercise_string=f"{exercise}\n{weight}, {reps}",
            sets_json={"weights": [weight], "reps": [reps]},
            top_weight=weight, top_reps=reps, bodyweight=user.bodyweight,
        ))
        self.session.commit()

    def test_overall_data_lists_days_titles_and_each_exercise(self):
        user = self._create_logged_in_user(username="consistent")
        for i, w in enumerate([50, 55, 60, 65]):
            self._log(user, datetime(2026, 9, 1 + 2 * i), "Chest", "Bench Press", w, 8)
        self._log(user, datetime(2026, 9, 2), "Cardio", "Rowing", 0, 10)

        data = self.client.get("/stats/data/average?mode=index").get_json()
        self.assertEqual(data["workout_days"], ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-05", "2026-09-07"])
        self.assertEqual(data["day_titles"]["2026-09-02"], "Cardio")
        bench = [ex for ex in data["exercises"] if ex["name"] == "Bench Press"]
        self.assertEqual(len(bench), 1)
        self.assertEqual(bench[0]["days"], ["2026-09-01", "2026-09-03", "2026-09-05", "2026-09-07"])
        self.assertEqual(len(bench[0]["values"]), 4)
        self.assertGreater(bench[0]["values"][-1], bench[0]["values"][0])
        self.assertGreater(bench[0]["baseline"], 0)

    def test_days_still_listed_when_no_exercise_has_enough_sessions(self):
        user = self._create_logged_in_user(username="new_lifter")
        self._log(user, datetime(2026, 9, 1), "Legs", "Squat", 60, 5)
        data = self.client.get("/stats/data/average?mode=index").get_json()
        self.assertEqual(data["workout_days"], ["2026-09-01"])
        self.assertEqual(data["day_titles"], {"2026-09-01": "Legs"})

    def test_stats_page_has_the_view_switch(self):
        self._create_logged_in_user(username="switcher")
        html = self.client.get("/stats").get_data(as_text=True)
        self.assertIn('id="viewToggle"', html)
        self.assertIn('id="consistencyGrid"', html)


if __name__ == "__main__":
    unittest.main()
