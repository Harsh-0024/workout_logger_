import re
import unittest
from datetime import datetime
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker
from sqlalchemy.pool import StaticPool

import workout_tracker
from config import Config
from models import Base, Lift, User, UserRole, WorkoutLog
from services.exercise_matching import build_name_index
from services.logging import (
    _get_best_log,
    comparison_set_count,
    handle_workout_log,
    resolve_target_sets_for_exercise,
)
from workout_tracker import create_app


class _RouteTestConfig(Config):
    TESTING = True
    SECRET_KEY = "test-secret-key"
    ENABLE_CSRF = False
    WTF_CSRF_ENABLED = False


class TestRouteRegressions(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.session = scoped_session(sessionmaker(bind=self.engine))
        Base.metadata.create_all(self.engine)

        # Rebind app/session globals used across route modules.
        self._patchers = [
            patch("models.Session", self.session),
            patch("workout_tracker.Session", self.session),
            patch("workout_tracker.routes.auth.Session", self.session),
            patch("workout_tracker.routes.workouts.Session", self.session),
            patch("workout_tracker.routes.stats.Session", self.session),
            patch("workout_tracker.routes.plans.Session", self.session),
        ]
        for p in self._patchers:
            p.start()

        self.app = create_app(config_object=_RouteTestConfig, init_db=False)
        self.client = self.app.test_client()

    def tearDown(self):
        for p in reversed(self._patchers):
            p.stop()
        self.session.remove()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _create_logged_in_user(self, *, username="route_tester"):
        user = User(
            username=username,
            role=UserRole.USER,
            is_verified=True,
            bodyweight=80.0,
        )
        self.session.add(user)
        self.session.commit()
        user_id = int(user.id)
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(user_id)
            sess["_fresh"] = True
            sess["_id"] = "route-test-session"
        return user

    def test_bulk_import_invalid_header_date_is_handled_as_failed_block(self):
        self._create_logged_in_user(username="bulk_user")

        payload = "\n".join(
            [
                "32/13 Impossible Date Day",
                "Flat Dumbbell Press - [8-12]",
                "30, 8",
                "",
                "12/01 Valid Day",
                "Flat Dumbbell Press - [8-12]",
                "25, 10",
            ]
        )

        response = self.client.post(
            "/bulk-import",
            data={
                "bulk_workouts_text": payload,
                "confirm_import": "0",
            },
        )

        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn("Failed days", page)
        self.assertIn("32/13", page)

    def test_workout_detail_best_link_uses_same_selection_as_topn_best_logic(self):
        user = self._create_logged_in_user(username="workout_user")
        exercise = "Flat Dumbbell Press"

        # Top-N best by scoring logic: this older 3-set log should win among preferred logs.
        self.session.add(
            WorkoutLog(
                user_id=user.id,
                date=datetime(2026, 1, 1, 9, 0, 0),
                workout_name="Session 1",
                exercise=exercise,
                exercise_string="Flat Dumbbell Press - [8-12]\n100 95 90, 6 6 6",
                sets_json={"weights": [100, 95, 90], "reps": [6, 6, 6]},
                bodyweight=user.bodyweight,
                estimated_1rm=120.0,
            )
        )
        # Highest estimated_1rm but only one set: should not be selected by top-N best logic.
        self.session.add(
            WorkoutLog(
                user_id=user.id,
                date=datetime(2026, 1, 5, 9, 0, 0),
                workout_name="Session 2",
                exercise=exercise,
                exercise_string="Flat Dumbbell Press - [8-12]\n120, 3",
                sets_json={"weights": [120], "reps": [3]},
                bodyweight=user.bodyweight,
                estimated_1rm=132.0,
            )
        )
        # Current workout view date.
        current_log = WorkoutLog(
            user_id=user.id,
            date=datetime(2026, 1, 10, 9, 0, 0),
            workout_name="Session 3",
            exercise=exercise,
            exercise_string="Flat Dumbbell Press - [8-12]\n85 80 80, 8 8 8",
            sets_json={"weights": [85, 80, 80], "reps": [8, 8, 8]},
            bodyweight=user.bodyweight,
            estimated_1rm=107.0,
        )
        self.session.add(current_log)
        self.session.commit()

        log_index = build_name_index([exercise])
        target_sets, strict_target_sets = resolve_target_sets_for_exercise(
            exercise_name=exercise,
            exercise_string=current_log.exercise_string,
            rep_target_sets={},
            plan_target_sets={},
            inferred_set_count=comparison_set_count(current_log.sets_json, current_log.exercise_string),
            default_sets=3,
        )
        expected_best = _get_best_log(
            self.session,
            user.id,
            exercise,
            target_sets=target_sets,
            strict_target_sets=strict_target_sets,
            log_ex_index=log_index,
            is_timed=False,
        )
        self.assertIsNotNone(expected_best)
        expected_date = expected_best.date.strftime("%Y-%m-%d")

        def _fake_render(template_name, **kwargs):
            if template_name == "workout_detail.html":
                return {
                    "rows": [
                        {
                            "exercise": log.exercise,
                            "best_workout_url": getattr(log, "best_workout_url", None),
                            "performance_key": getattr(log, "performance_key", None),
                        }
                        for log in kwargs.get("logs", [])
                    ]
                }
            return {"template": template_name}

        with patch("workout_tracker.routes.workouts.render_template", side_effect=_fake_render):
            response = self.client.get("/workout/2026-01-10")

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertIsNotNone(data)
        self.assertTrue(data.get("rows"))
        best_url = data["rows"][0].get("best_workout_url") or ""

        m = re.search(r"/workout/(\d{4}-\d{2}-\d{2})", best_url)
        self.assertIsNotNone(m, msg=f"best_workout_url missing date: {best_url}")
        best_link_date = m.group(1)

        self.assertEqual(best_link_date, expected_date)

    def test_incomplete_strict_session_gets_compared_badge_not_consistent_short_circuit(self):
        user = self._create_logged_in_user(username="incomplete_badge_user")
        exercise = "Flat Dumbbell Press"

        self.session.add(
            WorkoutLog(
                user_id=user.id,
                date=datetime(2026, 1, 1, 9, 0, 0),
                workout_name="Session 1",
                exercise=exercise,
                exercise_string="Flat Dumbbell Press - [3, 6-8]\n100 100 100, 5 5 5",
                sets_json={"weights": [100, 100, 100], "reps": [5, 5, 5]},
                bodyweight=user.bodyweight,
                estimated_1rm=116.67,
            )
        )
        self.session.add(
            WorkoutLog(
                user_id=user.id,
                date=datetime(2026, 1, 10, 9, 0, 0),
                workout_name="Session 2",
                exercise=exercise,
                exercise_string="Flat Dumbbell Press - [3, 6-8]\n90 90, 5 5",
                sets_json={"weights": [90, 90], "reps": [5, 5]},
                bodyweight=user.bodyweight,
                estimated_1rm=105.0,
            )
        )
        self.session.commit()

        def _fake_render(template_name, **kwargs):
            if template_name == "workout_detail.html":
                return {
                    "rows": [
                        {
                            "exercise": log.exercise,
                            "performance_key": getattr(log, "performance_key", None),
                        }
                        for log in kwargs.get("logs", [])
                    ]
                }
            return {"template": template_name}

        with patch("workout_tracker.routes.workouts.render_template", side_effect=_fake_render):
            response = self.client.get("/workout/2026-01-10")

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data.get("rows"))
        key = data["rows"][0].get("performance_key")
        self.assertIsNotNone(key)
        self.assertNotEqual(key, "consistent")

    def test_incomplete_strict_session_does_not_update_pr_record(self):
        user = self._create_logged_in_user(username="incomplete_pr_user")
        exercise = "Flat Dumbbell Press"
        baseline_date = datetime(2026, 1, 1, 9, 0, 0)
        baseline_sets = {"weights": [100, 95, 90], "reps": [5, 5, 5]}
        baseline_best_string = "Flat Dumbbell Press - [3, 6-8]\n100 95 90, 5 5 5"

        self.session.add(
            WorkoutLog(
                user_id=user.id,
                date=baseline_date,
                workout_name="Session 1",
                exercise=exercise,
                exercise_string=baseline_best_string,
                sets_json=baseline_sets,
                bodyweight=user.bodyweight,
                estimated_1rm=116.67,
            )
        )
        self.session.add(
            Lift(
                user_id=user.id,
                exercise=exercise,
                best_string=baseline_best_string,
                sets_json=baseline_sets,
                updated_at=baseline_date,
            )
        )
        self.session.commit()

        parsed = {
            "date": datetime(2026, 1, 10, 9, 0, 0),
            "workout_name": "Session 2",
            "exercises": [
                {
                    "name": exercise,
                    "weights": [130, 130],
                    "reps": [5, 5],
                    "exercise_string": "Flat Dumbbell Press - [3, 6-8]\n130 130, 5 5",
                    "valid": True,
                }
            ],
        }

        handle_workout_log(self.session, user, parsed)
        self.session.commit()

        lift = (
            self.session.query(Lift)
            .filter(Lift.user_id == user.id, Lift.exercise == exercise)
            .one()
        )
        self.assertEqual(lift.sets_json, baseline_sets)
        self.assertEqual(lift.best_string, baseline_best_string)
        self.assertEqual(lift.updated_at, baseline_date)

    def test_incomplete_strict_with_only_smaller_history_shows_no_comparable_baseline_label(self):
        user = self._create_logged_in_user(username="incomplete_no_baseline_user")
        exercise = "Flat Dumbbell Press"

        # Historical log exists but has fewer sets than the current incomplete log.
        self.session.add(
            WorkoutLog(
                user_id=user.id,
                date=datetime(2026, 1, 1, 9, 0, 0),
                workout_name="Session 1",
                exercise=exercise,
                exercise_string="Flat Dumbbell Press - [3, 6-8]\n100, 5",
                sets_json={"weights": [100], "reps": [5]},
                bodyweight=user.bodyweight,
                estimated_1rm=116.67,
            )
        )
        self.session.add(
            WorkoutLog(
                user_id=user.id,
                date=datetime(2026, 1, 10, 9, 0, 0),
                workout_name="Session 2",
                exercise=exercise,
                exercise_string="Flat Dumbbell Press - [3, 6-8]\n95 95, 5 5",
                sets_json={"weights": [95, 95], "reps": [5, 5]},
                bodyweight=user.bodyweight,
                estimated_1rm=110.83,
            )
        )
        self.session.commit()

        def _fake_render(template_name, **kwargs):
            if template_name == "workout_detail.html":
                return {
                    "rows": [
                        {
                            "exercise": log.exercise,
                            "performance_key": getattr(log, "performance_key", None),
                            "performance_label": getattr(log, "performance_label", None),
                        }
                        for log in kwargs.get("logs", [])
                    ]
                }
            return {"template": template_name}

        with patch("workout_tracker.routes.workouts.render_template", side_effect=_fake_render):
            response = self.client.get("/workout/2026-01-10")

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data.get("rows"))
        row = data["rows"][0]
        self.assertEqual(row.get("performance_key"), "first_log")
        self.assertEqual(row.get("performance_label"), "No Comparable Baseline")


if __name__ == "__main__":
    unittest.main()
