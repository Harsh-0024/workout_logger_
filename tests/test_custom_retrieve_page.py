"""The one-page Custom workout picker: muscle groups, sets per exercise, order kept."""
import re
import unittest

import tests.test_route_regressions as base
from models import ExerciseGroupChoice, Plan, RepRange, WorkoutLog
from services.exercise_matching import normalize_exercise_name


PLAN = "Push 1\nFlat Barbell Press - [5-8]\nCable Fly - [2, 12-20]\nOverhead Press - [4, 6-8]\n"


class TestCustomRetrievePage(unittest.TestCase):
    setUp = base.TestRouteRegressions.setUp
    tearDown = base.TestRouteRegressions.tearDown
    _create_logged_in_user = base.TestRouteRegressions._create_logged_in_user

    def _user_with_plan(self, name):
        user = self._create_logged_in_user(username=name)
        self.session.add(Plan(user_id=user.id, text_content=PLAN))
        self.session.commit()
        return user

    def _button(self, html, name):
        match = re.search(r'<button type="button" class="cr-ex[^"]*"[^>]*data-name="%s"[^>]*>' % re.escape(name), html)
        self.assertIsNotNone(match, name)
        return match.group(0)

    def test_exercises_are_grouped_by_muscle_with_their_plan_sets(self):
        self._user_with_plan("picker_groups")
        html = self.client.get("/retrieve/custom").get_data(as_text=True)
        groups = re.findall(r'<details class="cr-group" data-group="([^"]+)"', html)
        self.assertEqual(groups[:3], ["Chest", "Back", "Shoulders"])
        self.assertIn('data-sets="2"', self._button(html, "Cable Fly"))
        self.assertIn('data-reps="12-20"', self._button(html, "Cable Fly"))
        self.assertIn('data-sets="4"', self._button(html, "Overhead Press"))
        self.assertIn('data-sets="3"', self._button(html, "Flat Barbell Press"))
        # Chest group: the plan's exercises before the built-in list.
        chest = html[html.index('data-group="Chest"'):html.index('data-group="Back"')]
        self.assertLess(chest.index("Flat Barbell Press"), chest.index("Incline Barbell Press"))
        self.assertIn("is-other", self._button(html, "Incline Barbell Press"))
        self.assertNotIn("is-other", self._button(html, "Cable Fly"))

    def test_logged_exercises_count_as_yours(self):
        user = self._user_with_plan("picker_logged")
        self.session.add(WorkoutLog(user_id=user.id, date=base.datetime(2026, 9, 1), workout_name="Arms",
                                    exercise="Incline Barbell Press", exercise_string="x", sets_json={}))
        self.session.commit()
        html = self.client.get("/retrieve/custom").get_data(as_text=True)
        self.assertNotIn("is-other", self._button(html, "Incline Barbell Press"))

    def test_rep_range_settings_give_the_default_sets(self):
        user = self._user_with_plan("picker_reps")
        self.session.add(RepRange(user_id=user.id, text_content="Barbell Curl: 4, 8-10"))
        self.session.commit()
        html = self.client.get("/retrieve/custom").get_data(as_text=True)
        self.assertIn('data-sets="4"', self._button(html, "Barbell Curl"))

    def test_set_counts_follow_the_order_and_are_always_written(self):
        self._user_with_plan("picker_sets")
        keys = [normalize_exercise_name(n) for n in ("Overhead Press", "Cable Fly", "Flat Barbell Press")]
        page = self.client.post("/retrieve/custom", data={"exercise": keys, "set_count": ["4", "3", "3"]}, follow_redirects=True).get_data(as_text=True)
        self.assertIn("Overhead Press - [4, 6-8]", page)
        self.assertIn("Cable Fly - [3, 12-20]", page)          # 2 -> 3
        self.assertIn("Flat Barbell Press - [3, 5–8]", page)  # the 3 the page showed, spelled out
        self.assertIn("3 × 5–8", page)
        self.assertLess(page.index("Overhead Press - ["), page.index("Cable Fly - ["))
        self.assertLess(page.index("Cable Fly - ["), page.index("Flat Barbell Press - ["))
        self.assertIn("3 exercises", page)

    def test_bad_set_counts_are_refused(self):
        self._user_with_plan("picker_bad_sets")
        key = normalize_exercise_name("Flat Barbell Press")
        for bad in ("0", "11", "lots"):
            response = self.client.post("/retrieve/custom", data={"exercise": [key], "set_count": [bad]})
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.headers["Location"].endswith("/retrieve/custom"))

    def test_spelling_twins_in_the_history_join_the_plan_exercise(self):
        user = self._create_logged_in_user(username="picker_twins")
        self.session.add(Plan(user_id=user.id, text_content="Pull 1\nDumbbell Lat Row - [2, 8-12]\n"))
        self.session.add(WorkoutLog(user_id=user.id, date=base.datetime(2026, 9, 1), workout_name="Back",
                                    exercise="Lat Dumbbell Rows", exercise_string="x", sets_json={}))
        self.session.commit()
        html = self.client.get("/retrieve/custom").get_data(as_text=True)
        self.assertIn('data-name="Dumbbell Lat Row"', html)
        self.assertNotIn('data-name="Lat Dumbbell Rows"', html)
        self.assertNotIn('data-name="Unknown Exercise"', html)

    def test_an_exercise_with_no_target_of_its_own_still_gets_its_sets(self):
        user = self._user_with_plan("picker_no_target")
        self.session.add(WorkoutLog(user_id=user.id, date=base.datetime(2026, 9, 1), workout_name="Back",
                                    exercise="Superman", exercise_string="x", sets_json={}))
        self.session.commit()
        page = self.client.post("/retrieve/custom", data={"exercise": ["superman"], "set_count": ["3"]}, follow_redirects=True).get_data(as_text=True)
        self.assertIn("Superman - [3]", page)

    def test_names_that_say_nothing_follow_the_plan_only_when_it_is_clear(self):
        user = self._create_logged_in_user(username="picker_plan_fallback")
        self.session.add(Plan(user_id=user.id, text_content=(
            "Session 1 - Legs\nMudgal\nLeg Press\n\n"
            "Session 2 - Chest & Biceps\nDand\nBarbell Curl\n"
        )))
        self.session.commit()
        html = self.client.get("/retrieve/custom").get_data(as_text=True)

        def group_of(name):
            at = html.index('data-name="%s"' % name)
            return re.findall(r'data-group="([^"]+)"', html[:at])[-1]

        self.assertEqual(group_of("Mudgal"), "Legs")          # only ever on a Legs day
        self.assertEqual(group_of("Dand"), "Other")           # Chest or Biceps? Can't tell
        self.assertEqual(group_of("Barbell Curl"), "Biceps")  # the name decides, not the day

    def _group_of(self, html, name):
        at = html.index('data-name="%s"' % name)
        return re.findall(r'data-group="([^"]+)"', html[:at])[-1]

    def test_moving_an_exercise_is_remembered_and_moving_it_back_forgets(self):
        user = self._user_with_plan("picker_move")
        key = normalize_exercise_name("Dumbbell Curl")
        response = self.client.post("/retrieve/custom/group", data={"exercise": key, "group": "Forearms"})
        self.assertEqual(response.get_json(), {"ok": True, "group": "Forearms", "auto_group": "Biceps"})
        html = self.client.get("/retrieve/custom").get_data(as_text=True)
        self.assertEqual(self._group_of(html, "Dumbbell Curl"), "Forearms")
        self.assertIn('data-auto="Biceps"', self._button(html, "Dumbbell Curl"))
        self.assertEqual(self.session.query(ExerciseGroupChoice).filter_by(user_id=user.id).count(), 1)

        self.client.post("/retrieve/custom/group", data={"exercise": key, "group": "Biceps"})
        html = self.client.get("/retrieve/custom").get_data(as_text=True)
        self.assertEqual(self._group_of(html, "Dumbbell Curl"), "Biceps")
        self.assertEqual(self.session.query(ExerciseGroupChoice).filter_by(user_id=user.id).count(), 0)

    def test_moves_belong_to_one_account(self):
        self._user_with_plan("picker_move_a")
        self.client.post("/retrieve/custom/group", data={"exercise": normalize_exercise_name("Dumbbell Curl"), "group": "Other"})
        self._user_with_plan("picker_move_b")
        html = self.client.get("/retrieve/custom").get_data(as_text=True)
        self.assertEqual(self._group_of(html, "Dumbbell Curl"), "Biceps")

    def test_unknown_exercises_or_groups_are_refused(self):
        self._user_with_plan("picker_move_bad")
        for data in ({"exercise": "no such lift", "group": "Chest"},
                     {"exercise": normalize_exercise_name("Dumbbell Curl"), "group": "Glutes"}):
            self.assertEqual(self.client.post("/retrieve/custom/group", data=data).status_code, 400)

    def test_rep_range_exercises_are_listed_as_yours(self):
        user = self._user_with_plan("picker_rep_list")
        self.session.add(RepRange(user_id=user.id, text_content="Seated Zottman Curl: 10-12\nBarbell Curl: 4, 8-10"))
        self.session.commit()
        html = self.client.get("/retrieve/custom").get_data(as_text=True)
        button = self._button(html, "Seated Zottman Curl")
        self.assertNotIn("is-other", button)
        self.assertIn('data-reps="10-12"', button)
        self.assertIn('data-tier="rest"', button)  # set up, but never trained

    def test_often_trained_first_and_the_rest_folded_under_less_often(self):
        user = self._user_with_plan("picker_often")

        def log(name, *days):
            for month, day in days:
                self.session.add(WorkoutLog(user_id=user.id, date=base.datetime(2026, month, day), workout_name="W",
                                            exercise=name, exercise_string="x", sets_json={}))

        log("Leg Curl", (9, 1), (9, 15))          # off the plan, but on two days lately
        log("Upright Rows", (9, 10))              # once
        log("Hip Thrust", (1, 5), (1, 12))        # twice, but months before the latest workout
        log("Flat Barbell Press", (9, 20))        # the latest workout
        self.session.commit()
        html = self.client.get("/retrieve/custom").get_data(as_text=True)
        for name in ("Flat Barbell Press", "Cable Fly", "Leg Curl"):
            self.assertIn('data-tier="often"', self._button(html, name), name)
        for name in ("Upright Rows", "Hip Thrust", "Incline Barbell Press"):
            self.assertIn('data-tier="rest"', self._button(html, name), name)
        chest = html[html.index('data-group="Chest"'):html.index('data-group="Back"')]
        self.assertLess(chest.index('data-name="Cable Fly"'), chest.index('class="cr-more"'))
        self.assertLess(chest.index('class="cr-more"'), chest.index('data-name="Incline Barbell Press"'))
        self.assertIn("Less often", chest)

    def test_an_empty_group_is_still_there_to_move_into(self):
        self._user_with_plan("picker_empty_group")
        html = self.client.get("/retrieve/custom").get_data(as_text=True)
        self.assertIn('data-group="Other" hidden', html)
        self.assertIn('role="menuitemradio" aria-checked="false" data-group="Other"', html)


if __name__ == "__main__":
    unittest.main()
