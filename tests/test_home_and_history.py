"""Home stays a simple welcome; every workout lives on /workouts."""
import unittest
from datetime import datetime, timedelta

import tests.test_route_regressions as base
from models import User, WorkoutLog


class TestHomeAndHistory(unittest.TestCase):
    setUp = base.TestRouteRegressions.setUp
    tearDown = base.TestRouteRegressions.tearDown
    _create_logged_in_user = base.TestRouteRegressions._create_logged_in_user

    def _log(self, user, day, name, exercise="Bench Press"):
        self.session.add(WorkoutLog(
            user_id=user.id, date=day, workout_name=name, exercise=exercise,
            exercise_string=f"{exercise}\n50, 8", sets_json={"weights": [50], "reps": [8]},
            bodyweight=user.bodyweight,
        ))
        self.session.commit()

    def _home(self, user):
        return self.client.get(f"/{user.username}").get_data(as_text=True)

    def test_home_greets_by_first_name_and_shows_only_the_last_three(self):
        user = self._create_logged_in_user(username="home_user")
        self.session.query(User).filter_by(id=user.id).update({"full_name": "Asha Rao"})
        self.session.commit()
        self.session.expire_all()
        for i, name in enumerate(["Legs", "Back", "Chest", "Arms"]):
            self._log(user, datetime(2026, 9, 1 + i), name)
        html = self._home(user)
        self.assertIn(">Asha</h1>", html)
        self.assertIn("See all workouts", html)
        self.assertIn("/workout/2026-09-04", html)
        self.assertIn("/workout/2026-09-02", html)
        self.assertNotIn("/workout/2026-09-01", html)

    def test_new_account_home_points_to_retrieve_first(self):
        user = self._create_logged_in_user(username="fresh_user")
        html = self._home(user)
        self.assertIn("Your workouts will show up here.", html)
        self.assertIn("Retrieve a plan", html)
        self.assertNotIn("See all workouts", html)

    def test_all_workouts_lists_every_day_past_250(self):
        user = self._create_logged_in_user(username="long_history")
        start = datetime(2025, 1, 1)
        self.session.add_all([
            WorkoutLog(user_id=user.id, date=start + timedelta(days=i), workout_name="Session 3 - Back & Triceps",
                       exercise="Deadlift", exercise_string="Deadlift\n100, 5",
                       sets_json={"weights": [100], "reps": [5]}, bodyweight=80.0)
            for i in range(260)
        ])
        self.session.commit()
        html = self.client.get("/workouts").get_data(as_text=True)
        self.assertIn("260 workouts logged", html)
        self.assertIn("/workout/2025-01-01", html)
        # "Session 3 - Back & Triceps" reads as the name, with the session underneath.
        self.assertIn('<span class="st-row-title">Back &amp; Triceps</span>', html)
        self.assertIn("Session 3<span", html)

    def test_bulk_delete_returns_to_all_workouts(self):
        user = self._create_logged_in_user(username="bulk_user")
        self._log(user, datetime(2026, 9, 1), "Legs")
        self._log(user, datetime(2026, 9, 2), "Back")
        resp = self.client.post("/workouts/delete-selected", data={"selected_dates": ["2026-09-01"]})
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.headers["Location"].endswith("/workouts"))
        self.assertEqual(self.session.query(WorkoutLog).filter_by(user_id=user.id).count(), 1)

    def test_workout_page_goes_back_to_all_workouts(self):
        user = self._create_logged_in_user(username="back_user")
        self._log(user, datetime(2026, 9, 1), "Legs")
        html = self.client.get("/workout/2026-09-01?return_to=/workouts").get_data(as_text=True)
        self.assertIn('href="/workouts"', html)


if __name__ == "__main__":
    unittest.main()
