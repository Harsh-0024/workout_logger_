"""Renaming an exercise keeps its history: logs, best lifts, preferences, plan and rep ranges."""
import unittest
from datetime import datetime

import tests.test_route_regressions as base
from models import (
    User,
    UserRole,
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



class TestRenameQuestionOnSave(unittest.TestCase):
    """Saving the plan or rep ranges with an exercise renamed asks before its history moves."""
    setUp = base.TestRouteRegressions.setUp
    tearDown = base.TestRouteRegressions.tearDown
    _create_logged_in_user = base.TestRouteRegressions._create_logged_in_user
    _log = TestExerciseRename._log
    _names = TestExerciseRename._names

    PLAN = "Abs 1\nCrunches A - [3, 10-15]\nPlank\nPull 1\nDumbbell Lat Row\n"

    def _user(self, name):
        user = self._create_logged_in_user(username=name)
        self.session.add(Plan(user_id=user.id, text_content=self.PLAN))
        self.session.commit()
        self._log(user, "Crunches A", 1)
        self._log(user, "Crunches A", 3)
        return user

    def test_renaming_in_the_plan_asks_and_yes_keeps_the_history(self):
        user = self._user("rq_yes")
        self.client.post("/set_plan", data={"plan_text": self.PLAN.replace("Crunches A", "Decline Crunches")})
        page = self.client.get("/set_plan").get_data(as_text=True)
        self.assertIn("Is Decline Crunches the same exercise as Crunches A?", page)
        self.assertIn("Crunches A has 2 logged workouts.", page)
        self.assertEqual(self._names(user), ["Crunches A"])
        self.client.post("/settings/exercise-names/answer", data={
            "old_name": "Crunches A", "new_name": "Decline Crunches", "answer": "yes", "next": "/set_plan"})
        self.assertEqual(self._names(user), ["Decline Crunches"])
        self.assertNotIn("the same exercise as", self.client.get("/set_plan").get_data(as_text=True))

    def test_no_leaves_the_history_alone_and_stops_asking(self):
        user = self._user("rq_no")
        self.client.post("/set_plan", data={"plan_text": self.PLAN.replace("Crunches A", "Cable Crunch")})
        self.client.post("/settings/exercise-names/answer", data={
            "old_name": "Crunches A", "new_name": "Cable Crunch", "answer": "no", "next": "/set_plan"})
        self.assertEqual(self._names(user), ["Crunches A"])
        self.assertNotIn("the same exercise as", self.client.get("/set_plan").get_data(as_text=True))

    def test_a_missing_answer_keeps_the_question(self):
        user = self._user("rq_blank")
        self.client.post("/set_plan", data={"plan_text": self.PLAN.replace("Crunches A", "Decline Crunches")})
        self.client.post("/settings/exercise-names/answer", data={
            "old_name": "Crunches A", "new_name": "Decline Crunches", "next": "/set_plan"})
        self.assertIn("the same exercise as", self.client.get("/set_plan").get_data(as_text=True))
        self.assertEqual(self._names(user), ["Crunches A"])

    def test_nothing_is_asked_without_history_or_without_a_replacement(self):
        user = self._user("rq_quiet")
        # Dumbbell Lat Row has no logs; Crunches A is only removed.
        self.client.post("/set_plan", data={"plan_text": "Abs 1\nPlank\nPull 1\nSingle-Arm Row\n"})
        self.assertNotIn("the same exercise as", self.client.get("/set_plan").get_data(as_text=True))

    def test_reordering_is_not_a_rename(self):
        self._user("rq_order")
        self.client.post("/set_plan", data={"plan_text": "Abs 1\nPlank\nCrunches A\nPull 1\nDumbbell Lat Row\n"})
        self.assertNotIn("the same exercise as", self.client.get("/set_plan").get_data(as_text=True))

    def test_renaming_in_rep_ranges_asks_too(self):
        user = self._user("rq_reps")
        self.session.add(RepRange(user_id=user.id, text_content="Crunches A: 3, 10–15\nPlank: 30–60s"))
        self.session.commit()
        self.client.post("/set_exercises", data={"rep_text": "Plank: 30–60s\nDecline Crunches: 3, 10–15",
                                                 "rep_text_ready": "1"})
        page = self.client.get("/set_exercises").get_data(as_text=True)
        self.assertIn("Is Decline Crunches the same exercise as Crunches A?", page)



class TestRenamesReachFollowers(unittest.TestCase):
    """Followers take their exercise names from the plan owner, so the owner's renames move
    their history too."""
    setUp = base.TestRouteRegressions.setUp
    tearDown = base.TestRouteRegressions.tearDown
    _create_logged_in_user = base.TestRouteRegressions._create_logged_in_user
    _log = TestExerciseRename._log
    _names = TestExerciseRename._names

    def _owner(self):
        owner = User(username="owner", role=UserRole.ADMIN, is_verified=True)
        self.session.add(owner)
        self.session.commit()
        self.session.add(Plan(user_id=owner.id, text_content="Abs 1\nCrunches A\n"))
        self.session.commit()
        self._log(owner, "Crunches A", 1)
        return owner

    def _user(self, name, follow):
        user = User(username=name, role=UserRole.USER, is_verified=True,
                    follow_admin_plan=follow, follow_admin_exercises=follow)
        self.session.add(user)
        self.session.commit()
        return user

    def test_followers_who_logged_it_get_the_new_name(self):
        owner = self._owner()
        follower = self._user("fan", True)
        loner = self._user("loner", False)
        self._log(follower, "Crunches A", 2)
        self._log(follower, "Crunches A (Wellness)", 3)
        self._log(loner, "Crunches A", 2)
        self.assertEqual(rename_preview(self.session, owner, "Crunches A", "Decline Crunches")["followers"], 1)
        rename_exercise(self.session, owner, "Crunches A", "Decline Crunches")
        self.assertEqual(self._names(follower), ["Decline Crunches", "Decline Crunches (Wellness)"])
        self.assertEqual(self._names(loner), ["Crunches A"])
        self.assertEqual(resolve_renamed_exercise(self.session, follower.id, "Crunches A"), "Decline Crunches")

    def test_a_follower_who_renamed_it_their_way_keeps_their_name(self):
        owner = self._owner()
        follower = self._user("own_way", True)
        self._log(follower, "Crunches A", 2)
        rename_exercise(self.session, follower, "Crunches A", "Cable Crunch")
        rename_exercise(self.session, owner, "Crunches A", "Decline Crunches")
        self.assertEqual(self._names(follower), ["Cable Crunch"])
        self.assertEqual(resolve_renamed_exercise(self.session, follower.id, "Crunches A"), "Cable Crunch")

    def test_starting_to_follow_catches_up_with_earlier_renames(self):
        owner = self._owner()
        rename_exercise(self.session, owner, "Crunches A", "Decline Crunches")
        newcomer = self._create_logged_in_user(username="newcomer")
        self._log(newcomer, "Crunches A", 2)
        self.client.post("/set_plan", data={"form_type": "toggle_follow_admin", "follow_admin_plan": "1"})
        self.assertEqual(self._names(newcomer), ["Decline Crunches"])

    def test_renaming_a_followed_name_for_yourself_is_flagged(self):
        self._owner()
        follower = self._create_logged_in_user(username="flagged", follow_admin=True)
        self._log(follower, "Crunches A", 2)
        page = self.client.get("/settings/exercise-names?from=Crunches+A&to=Cable+Crunch").get_data(as_text=True)
        self.assertIn("comes from the plan you follow", page)



class TestCopyAndEdit(unittest.TestCase):
    """Instead of following, take a copy of the owner's plan or rep ranges to change freely."""
    setUp = base.TestRouteRegressions.setUp
    tearDown = base.TestRouteRegressions.tearDown
    _create_logged_in_user = base.TestRouteRegressions._create_logged_in_user
    _log = TestExerciseRename._log
    _names = TestExerciseRename._names
    _owner = TestRenamesReachFollowers._owner

    def test_copying_the_plan_makes_it_yours_and_stops_following(self):
        owner = self._owner()
        rename_exercise(self.session, owner, "Crunches A", "Decline Crunches")
        user = self._create_logged_in_user(username="copier", follow_admin=True)
        self._log(user, "Crunches A", 2)
        user_id = user.id
        self.client.post("/set_plan", data={"form_type": "copy_followed"})
        user = self.session.get(User, user_id)
        self.assertFalse(user.follow_admin_plan)
        self.assertTrue(user.follow_admin_exercises)
        self.assertEqual(self.session.query(Plan).filter_by(user_id=user.id).one().text_content,
                         "Abs 1\nDecline Crunches\n")
        self.assertEqual(self._names(user), ["Decline Crunches"])

    def test_copying_clears_questions_about_the_old_text(self):
        self._owner()
        user = self._create_logged_in_user(username="q_then_copy")
        self.session.add(Plan(user_id=user.id, text_content="Abs 1\nCrunches A\n"))
        self.session.commit()
        self._log(user, "Crunches A", 2)
        self.client.post("/set_plan", data={"plan_text": "Abs 1\nCable Crunch\n"})
        self.assertIn("the same exercise as", self.client.get("/set_plan").get_data(as_text=True))
        self.client.post("/set_plan", data={"form_type": "copy_followed"})
        self.assertNotIn("the same exercise as", self.client.get("/set_plan").get_data(as_text=True))

    def test_copying_rep_ranges(self):
        owner = self._owner()
        self.session.add(RepRange(user_id=owner.id, text_content="Decline Crunches: 3, 10–15"))
        self.session.commit()
        user_id = self._create_logged_in_user(username="rep_copier", follow_admin=True).id
        self.client.post("/set_exercises", data={"form_type": "copy_followed"})
        user = self.session.get(User, user_id)
        self.assertFalse(user.follow_admin_exercises)
        self.assertEqual(self.session.query(RepRange).filter_by(user_id=user.id).one().text_content,
                         "Decline Crunches: 3, 10–15")

    def test_asks_first_only_when_it_would_replace_your_own_plan(self):
        self._owner()
        user = self._create_logged_in_user(username="has_own", follow_admin=True)
        page = self.client.get("/set_plan").get_data(as_text=True)
        self.assertIn("Copy and edit", page)
        self.assertNotIn("Replace your plan?", page)
        self.session.add(Plan(user_id=user.id, text_content="Legs 1\nSquat\n"))
        self.session.commit()
        self.assertIn("Replace your plan?", self.client.get("/set_plan").get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
