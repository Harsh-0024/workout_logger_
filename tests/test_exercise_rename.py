"""Renaming an exercise keeps its history: logs, best lifts, preferences, plan and rep ranges."""
import unittest
from datetime import datetime

import tests.test_route_regressions as base
from models import (
    BodyweightExercisePreference,
    ExerciseGroupChoice,
    ExerciseRename,
    Lift,
    Plan,
    RepRange,
    StatsExerciseView,
    WorkoutLog,
)
from services.exercise_rename import rename_exercise, rename_preview, resolve_renamed_exercise


class TestExerciseRename(unittest.TestCase):
    setUp = base.TestRouteRegressions.setUp
    tearDown = base.TestRouteRegressions.tearDown
    _create_logged_in_user = base.TestRouteRegressions._create_logged_in_user

    def _log(self, user, name, day, text=None, weight=40.0, reps=10):
        log = WorkoutLog(
            user_id=user.id, date=datetime(2026, 9, day), workout_name="Pull", exercise=name,
            exercise_string=text or f"{name} - [2]\n{weight:g}, {reps}",
            sets_json={"weights": [weight], "reps": [reps]}, top_weight=weight, top_reps=reps,
            estimated_1rm=weight * (1 + reps / 30),
        )
        self.session.add(log)
        self.session.commit()
        return log

    def _names(self, user):
        rows = self.session.query(WorkoutLog.exercise).filter_by(user_id=user.id).distinct().all()
        return sorted(r[0] for r in rows)

    def test_logs_and_their_text_move_to_the_new_name(self):
        user = self._create_logged_in_user(username="rn_logs")
        log = self._log(user, "Dumbbell Lat Row", 1)
        rename_exercise(self.session, user, "Dumbbell Lat Row", "Single-Arm Dumbbell Row")
        self.assertEqual(self._names(user), ["Single-Arm Dumbbell Row"])
        self.session.refresh(log)
        self.assertTrue(log.exercise_string.startswith("Single-Arm Dumbbell Row - [2]"))
        lift = self.session.query(Lift).filter_by(user_id=user.id).one()
        self.assertEqual((lift.exercise, lift.best_log_id), ("Single-Arm Dumbbell Row", log.id))

    def test_a_name_already_in_use_becomes_one_history(self):
        user = self._create_logged_in_user(username="rn_merge")
        self._log(user, "Crunches A", 1, weight=20)
        heavier = self._log(user, "Decline Crunches", 8, weight=30)
        self.assertEqual(rename_preview(self.session, user, "Crunches A", "Decline Crunches")["already"], 1)
        rename_exercise(self.session, user, "crunches a", "Decline Crunches")
        self.assertEqual(self._names(user), ["Decline Crunches"])
        lifts = self.session.query(Lift).filter_by(user_id=user.id).all()
        self.assertEqual([(l.exercise, l.best_log_id) for l in lifts], [("Decline Crunches", heavier.id)])

    def test_gym_tagged_versions_keep_their_tag(self):
        user = self._create_logged_in_user(username="rn_tags")
        self._log(user, "Lat Row", 1)
        self._log(user, "Lat Row (Wellness)", 2)
        self._log(user, "Lat Pulldown", 3)
        preview = rename_preview(self.session, user, "Lat Row", "Single-Arm Row")
        self.assertEqual((preview["moving"], preview["tagged"]), (2, ["Lat Row (Wellness)"]))
        rename_exercise(self.session, user, "Lat Row", "Single-Arm Row")
        self.assertEqual(self._names(user), ["Lat Pulldown", "Single-Arm Row", "Single-Arm Row (Wellness)"])

    def test_plan_and_rep_ranges_use_the_new_name(self):
        user = self._create_logged_in_user(username="rn_text")
        self.session.add(Plan(user_id=user.id, text_content=(
            "Pull 1\nDumbbell Lat Row - [3, 8-12]\nLat Row (Wellness) [2]\nLat Pulldown\n")))
        self.session.add(RepRange(user_id=user.id, text_content="Dumbbell Lat Row: 3, 8-12\nLat Pulldown: 10-12"))
        self.session.commit()
        rename_exercise(self.session, user, "Dumbbell Lat Row", "Single-Arm Dumbbell Row")
        plan = self.session.query(Plan).filter_by(user_id=user.id).one().text_content
        self.assertEqual(plan, "Pull 1\nSingle-Arm Dumbbell Row - [3, 8-12]\nLat Row (Wellness) [2]\nLat Pulldown\n")
        rep = self.session.query(RepRange).filter_by(user_id=user.id).one().text_content
        self.assertEqual(rep, "Single-Arm Dumbbell Row: 3, 8–12\nLat Pulldown: 10–12")

    def test_the_new_names_own_rep_range_wins(self):
        user = self._create_logged_in_user(username="rn_reps")
        self.session.add(RepRange(user_id=user.id, text_content="Crunches A: 12-15\nDecline Crunches: 3, 10-12\nPlank:"))
        self.session.commit()
        rename_exercise(self.session, user, "Crunches A", "Decline Crunches")
        rep = self.session.query(RepRange).filter_by(user_id=user.id).one().text_content
        self.assertEqual(rep, "Decline Crunches: 3, 10–12\nPlank:")

    def test_preferences_follow_and_merge(self):
        user = self._create_logged_in_user(username="rn_prefs")
        self.session.add_all([
            BodyweightExercisePreference(user_id=user.id, exercise_key="chin up", exercise_name="Chin Up"),
            ExerciseGroupChoice(user_id=user.id, exercise_key="chin up", group_name="Biceps"),
            StatsExerciseView(user_id=user.id, exercise_key="chin up", view_count=3),
            StatsExerciseView(user_id=user.id, exercise_key="chin up weighted", view_count=2),
        ])
        self.session.commit()
        rename_exercise(self.session, user, "Chin Up", "Weighted Chin Up")
        bw = self.session.query(BodyweightExercisePreference).filter_by(user_id=user.id).one()
        self.assertEqual((bw.exercise_key, bw.exercise_name), ("weighted chin up", "Weighted Chin Up"))
        self.assertEqual(self.session.query(ExerciseGroupChoice).filter_by(user_id=user.id).one().exercise_key,
                         "weighted chin up")
        views = self.session.query(StatsExerciseView).filter_by(user_id=user.id).all()
        self.assertEqual([v.view_count for v in views], [5])

    def test_logging_the_old_name_saves_under_the_new_one(self):
        user = self._create_logged_in_user(username="rn_redirect")
        self.session.add(Plan(user_id=user.id, text_content="Abs 1\nDecline Crunches\n"))
        self.session.commit()
        self._log(user, "Crunches A", 1)
        rename_exercise(self.session, user, "Crunches A", "Decline Crunches")
        self.client.post("/log", data={"workout_text": "9/9/26 - Abs\nCrunches A (Wellness) - [1]\n25, 12"})
        self.assertEqual(self._names(user), ["Decline Crunches", "Decline Crunches (Wellness)"])
        logged = self.session.query(WorkoutLog).filter_by(exercise="Decline Crunches (Wellness)").one()
        self.assertTrue(logged.exercise_string.startswith("Decline Crunches (Wellness)"))

    def test_renames_chain_and_renaming_back_clears_the_redirect(self):
        user = self._create_logged_in_user(username="rn_chain")
        self._log(user, "Row A", 1)
        rename_exercise(self.session, user, "Row A", "Row B")
        rename_exercise(self.session, user, "Row B", "Row C")
        self.assertEqual(resolve_renamed_exercise(self.session, user.id, "Row A"), "Row C")
        self.assertEqual(resolve_renamed_exercise(self.session, user.id, "Row B"), "Row C")
        rename_exercise(self.session, user, "Row C", "Row A")
        self.assertEqual(resolve_renamed_exercise(self.session, user.id, "Row A"), "Row A")
        self.assertEqual(self._names(user), ["Row A"])
        redirects = {(r.old_key, r.new_name) for r in self.session.query(ExerciseRename).filter_by(user_id=user.id)}
        self.assertEqual(redirects, {("row b", "Row A"), ("row c", "Row A")})

    def test_other_users_are_untouched(self):
        user = self._create_logged_in_user(username="rn_me")
        other = self._create_logged_in_user(username="rn_other")
        self._log(user, "Crunches A", 1)
        self._log(other, "Crunches A", 1)
        rename_exercise(self.session, user, "Crunches A", "Decline Crunches")
        self.assertEqual(self._names(other), ["Crunches A"])
        self.assertEqual(resolve_renamed_exercise(self.session, other.id, "Crunches A"), "Crunches A")

    def test_settings_page_previews_then_renames(self):
        user = self._create_logged_in_user(username="rn_page")
        self._log(user, "Crunches A", 1)
        page = self.client.get("/settings/exercise-names").get_data(as_text=True)
        self.assertIn('<option value="Crunches A">', page)
        page = self.client.get("/settings/exercise-names?from=Crunches+A&to=Decline+Crunches").get_data(as_text=True)
        self.assertIn("1 logged workout moves to the new name.", page)
        self.assertEqual(self._names(user), ["Crunches A"])
        response = self.client.post("/settings/exercise-names",
                                    data={"old_name": "Crunches A", "new_name": "Decline Crunches"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._names(user), ["Decline Crunches"])
        page = self.client.get("/settings/exercise-names").get_data(as_text=True)
        self.assertIn("Renamed", page)

    def test_same_name_is_refused(self):
        self._create_logged_in_user(username="rn_same")
        page = self.client.get("/settings/exercise-names?from=Plank&to=Plank").get_data(as_text=True)
        self.assertIn("already its name", page)


if __name__ == "__main__":
    unittest.main()
