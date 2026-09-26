"""Log page checks before saving, the workout page after saving, and the one-page Retrieve."""
import unittest
from datetime import datetime
from urllib.parse import urlsplit

import tests.test_route_regressions as base
from models import Plan, WorkoutLog
from parsers.workout import workout_parser


PLAN = """Cycle 1

Session 1 – Chest & Biceps
Flat Barbell Press – [5-8]
Barbell Curl

Session 2 – Legs
Leg Press – [2]

Cycle 2

Session 3 – Back
Pull-Ups
"""


class TestParserDetails(unittest.TestCase):
    def test_names_lose_stray_dashes_and_tabs(self):
        parsed = workout_parser("1/9 Day\nForearm Roller - 5 2.5, 1\nPreacher Curl\t (Wellness) - [2]\n20 15, 10")
        names = [ex["name"] for ex in parsed["exercises"]]
        self.assertEqual(names, ["Forearm Roller", "Preacher Curl (Wellness)"])

    def test_numbers_without_a_name_are_flagged_with_their_line(self):
        text = "1/9 Day\n\nBench Press - [3]\n50 45, 8\n\n12.5 9.25, 10 16"
        exercises = workout_parser(text)["exercises"]
        self.assertEqual([ex["line"] for ex in exercises], [3, 6])
        self.assertFalse(exercises[0]["missing_name"])
        self.assertTrue(exercises[1]["missing_name"])


class TestLogAndRetrieveFlow(unittest.TestCase):
    # Same in-memory app and login as the route regression tests.
    setUp = base.TestRouteRegressions.setUp
    tearDown = base.TestRouteRegressions.tearDown
    _create_logged_in_user = base.TestRouteRegressions._create_logged_in_user

    def _log(self, user, day, name, exercise, sets_json, text):
        self.session.add(WorkoutLog(
            user_id=user.id, date=day, workout_name=name, exercise=exercise,
            exercise_string=text, sets_json=sets_json, bodyweight=user.bodyweight,
        ))
        self.session.commit()

    def _count_logs(self, user):
        return self.session.query(WorkoutLog).filter_by(user_id=user.id).count()

    # ---- Log: blocked saves keep the text and store nothing ----

    def test_numbers_without_a_name_are_not_saved(self):
        user = self._create_logged_in_user(username="orphan_user")
        text = "3/9/26 - Push\n\nBench Press - [2]\n60 55, 8\n\n12.5 9.25, 10 16"
        response = self.client.post("/log", data={"workout_text": text})
        self.assertEqual(response.status_code, 422)
        html = response.get_data(as_text=True)
        self.assertIn("Nothing was saved", html)
        self.assertIn("Line 6", html)
        self.assertIn("12.5 9.25, 10 16", html)  # text kept in the box
        self.assertEqual(self._count_logs(user), 0)

    def test_unreadable_text_is_not_reported_as_a_session(self):
        user = self._create_logged_in_user(username="garbage_user")
        response = self.client.post("/log", data={"workout_text": "hello there\nfoo"})
        self.assertEqual(response.status_code, 422)
        self.assertIn("Couldn&#39;t find any exercise", response.get_data(as_text=True))
        self.assertEqual(self._count_logs(user), 0)

    # ---- Log: success lands on the workout page ----

    def test_saving_redirects_to_the_workout_page_with_a_saved_note(self):
        user = self._create_logged_in_user(username="saver")
        text = "4/9/26 - Push\n\nBench Press - [2]\n60 55, 8\n\nfelt strong today"
        response = self.client.post("/log", data={"workout_text": text})
        self.assertEqual(response.status_code, 302)
        location = urlsplit(response.headers["Location"])
        self.assertEqual(location.path, "/workout/2026-09-04")
        self.assertEqual(location.query, "saved=1")
        self.assertEqual(self._count_logs(user), 1)

        page = self.client.get(response.headers["Location"]).get_data(as_text=True)
        self.assertIn("Saved", page)
        self.assertIn("felt strong today", page)
        self.assertIn("wasn&#39;t saved", page)
        # Without ?saved the note is not shown.
        self.assertNotIn("wd-callout saved", self.client.get("/workout/2026-09-04").get_data(as_text=True))

    def test_second_log_on_a_day_offers_to_add_and_append_keeps_the_day(self):
        user = self._create_logged_in_user(username="appender")
        self.client.post("/log", data={"workout_text": "5/9/26 - Push\nBench Press - [2]\n60 55, 8"})
        text = "5/9/26 - Extra\nBarbell Curl - [2]\n20 17.5, 10"

        response = self.client.post("/log", data={"workout_text": text})
        self.assertEqual(response.status_code, 409)
        self.assertIn("Add to that day", response.get_data(as_text=True))
        self.assertEqual(self._count_logs(user), 1)

        response = self.client.post("/log", data={"workout_text": text, "mode": "append"})
        self.assertEqual(response.status_code, 302)
        logs = self.session.query(WorkoutLog).filter_by(user_id=user.id).all()
        self.assertEqual(len(logs), 2)
        self.assertEqual({log.workout_name for log in logs}, {"Push"})
        self.assertEqual({log.date.date() for log in logs}, {datetime(2026, 9, 5).date()})

    # ---- Log: the live check ----

    def test_preview_suggests_the_known_spelling_and_flags_the_existing_day(self):
        user = self._create_logged_in_user(username="previewer")
        self._log(user, datetime(2026, 9, 1), "Push", "Flat Barbell Press",
                  {"weights": [50, 50, 50], "reps": [8, 8, 8]}, "Flat Barbell Press - [3]\n50, 8")
        text = "1/9/26 - Push\n\nFlat Barbel Pres - [3]\n52.5 50, 8\n\nFlat Barbell Press - [3]\n50, 8"
        data = self.client.post("/log/preview", data={"workout_text": text, "mode": "append"}).get_json()

        self.assertTrue(data["ok"])
        typo, dup = data["exercises"]
        self.assertEqual(typo["state"], "suggest")
        self.assertEqual(typo["suggestion"], "Flat Barbell Press")
        self.assertEqual(typo["fix_line"], "Flat Barbell Press - [3]")
        self.assertEqual(typo["line"], 3)
        self.assertEqual(dup["state"], "dup")
        self.assertEqual(data["existing"]["date_str"], "2026-09-01")
        self.assertEqual(data["existing"]["url"], "/workout/2026-09-01")

    def test_preview_for_the_edit_page_counts_lines_from_the_exercise_box(self):
        self._create_logged_in_user(username="edit_previewer")
        data = self.client.post("/log/preview", data={
            "workout_text": "Bench Press - [2]\n60 55, 8\n\n12, 10",
            "title": "Push", "date": "2026-09-06",
        }).get_json()
        self.assertFalse(data["ok"])
        self.assertEqual(data["errors"][0]["line"], 4)

    # ---- Shortcut and old links ----

    def test_shortcut_refuses_numbers_without_a_name_and_links_to_the_workout_page(self):
        user = self._create_logged_in_user(username="shortcut_checker")
        log_path = urlsplit(self.client.get("/shortcut/log").get_json()["url"]).path

        data = self.client.post(log_path, data={"workout_text": "7/9/26 Push\nBench Press 60 x 8\n\n12, 10"}).get_json()
        self.assertFalse(data["ok"])
        self.assertIn("no exercise name", data["error"])
        self.assertEqual(self._count_logs(user), 0)

        data = self.client.post(log_path, data={"workout_text": "7/9/26 Push\nBench Press 60 x 8\nnote to self"}).get_json()
        self.assertTrue(data["ok"], data)
        self.assertIn("/workout/2026-09-07", data["result_url"])
        self.assertIn("note to self", data["message"])

    def test_old_summary_link_opens_the_workout_page(self):
        self._create_logged_in_user(username="summary_user")
        response = self.client.get("/summary/2026-09-08")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(urlsplit(response.headers["Location"]).path, "/workout/2026-09-08")

    # ---- Entries saved before the check existed ----

    def test_workout_page_warns_about_an_entry_with_no_name_and_edit_refuses_it(self):
        user = self._create_logged_in_user(username="old_entry_user")
        day = datetime(2026, 7, 6)
        self._log(user, day, "Session 9", "Bench Press", {"weights": [60, 55], "reps": [8, 8]}, "Bench Press - [2]\n60 55, 8")
        self._log(user, day, "Session 9", "Unknown Exercise", {"weights": [12.5, 9.25], "reps": [10, 16]}, "12.5 9.25, 10 16")

        page = self.client.get("/workout/2026-07-06").get_data(as_text=True)
        self.assertIn("1 entry has no exercise name", page)
        self.assertIn("/workout/2026-07-06/edit", page)

        edit = self.client.get("/workout/2026-07-06/edit").get_data(as_text=True)
        self.assertIn("12.5 9.25, 10 16", edit)
        response = self.client.post("/workout/2026-07-06/edit", data={
            "workout_title": "Session 9", "workout_date": "2026-07-06",
            "workout_text": "Bench Press - [2]\n60 55, 8\n\n12.5 9.25, 10 16",
        })
        self.assertEqual(response.status_code, 422)
        self.assertIn("no exercise name", response.get_data(as_text=True))
        self.assertEqual(self._count_logs(user), 2)

        response = self.client.post("/workout/2026-07-06/edit", data={
            "workout_title": "Session 9", "workout_date": "2026-07-06",
            "workout_text": "Bench Press - [2]\n60 55, 8\n\nRope Hammer Curl - [2]\n12.5 9.25, 10 16",
        })
        self.assertEqual(response.status_code, 302)
        names = {log.exercise for log in self.session.query(WorkoutLog).filter_by(user_id=user.id)}
        self.assertEqual(names, {"Bench Press", "Rope Hammer Curl"})

    # ---- Retrieve ----

    def test_retrieve_lists_every_session_on_one_page(self):
        user = self._create_logged_in_user(username="retriever")
        self.session.add(Plan(user_id=user.id, text_content=PLAN))
        self.session.commit()

        html = self.client.get("/retrieve/categories").get_data(as_text=True)
        for text in ("Cycle 1", "Cycle 2", "Chest &amp; Biceps", "Legs", "Back", "Custom workout"):
            self.assertIn(text, html)
        for sid in (1, 2, 3):
            self.assertIn(f'href="/retrieve/final/Session/{sid}"', html)
        # No suggestion of what to do next.
        self.assertNotIn("recommend-workout", html)
        self.assertNotIn("Up next", html)

        # Old step links still land somewhere useful.
        self.assertEqual(urlsplit(self.client.get("/retrieve/heading/1").headers["Location"]).path, "/retrieve/categories")

    def test_plan_page_reads_as_a_list_and_keeps_the_text_to_copy(self):
        user = self._create_logged_in_user(username="plan_reader")
        self.session.add(Plan(user_id=user.id, text_content=PLAN))
        self.session.commit()
        self._log(user, datetime(2026, 9, 1), "Session 1", "Flat Barbell Press",
                  {"weights": [60, 55, 50], "reps": [6, 7, 8]}, "Flat Barbell Press - [5-8]\n60 55 50, 6 7 8")

        html = self.client.get("/retrieve/final/Session/1").get_data(as_text=True)
        self.assertIn("Session 1 · Cycle 1", html)
        self.assertIn("Chest &amp; Biceps", html)
        self.assertIn("60×6 · 55×7 · 50×8", html)
        self.assertIn("No history yet", html)          # Barbell Curl
        self.assertIn('id="planText"', html)
        self.assertIn("Flat Barbell Press - [5–8]", html)  # the copy text itself


if __name__ == "__main__":
    unittest.main()
