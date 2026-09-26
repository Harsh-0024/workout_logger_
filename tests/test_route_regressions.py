import io
import re
import unittest
import os
import sys
from io import BytesIO
from datetime import date, datetime, timedelta
from urllib.parse import urlsplit
from unittest.mock import Mock, patch

from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker
from sqlalchemy.pool import StaticPool

import workout_tracker
from config import Config
from models import (
    Base,
    BodyweightExercisePreference,
    CustomRetrievalEvent,
    CustomRetrievalPreference,
    Lift,
    Plan,
    StatsExerciseView,
    StatsPreference,
    User,
    UserRole,
    WorkoutLog,
)
from parsers.workout import workout_parser
from services.bodyweight import bodyweight_exercise_key
from services.exercise_matching import build_name_index, normalize_exercise_name
from services.logging import (
    _get_best_log,
    classify_exercise_performance,
    compute_workout_summary_for_date,
    comparison_set_count,
    get_best_log_for_exercise_before_date,
    handle_workout_log,
    resolve_target_sets_for_exercise,
)
from workout_tracker import create_app
from workout_tracker.routes.auth import _infer_bulk_import_dates
from utils import profile_images


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

    def _create_logged_in_user(self, *, username="route_tester", follow_admin=False):
        # Tests usually give the user their own plan; new accounts in the app follow the admin.
        user = User(
            username=username,
            role=UserRole.USER,
            is_verified=True,
            bodyweight=80.0,
            follow_admin_plan=follow_admin,
            follow_admin_exercises=follow_admin,
        )
        self.session.add(user)
        self.session.commit()
        user_id = int(user.id)
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(user_id)
            sess["_fresh"] = True
            sess["_id"] = "route-test-session"
        return user

    def test_shared_workout_page_shows_medals_preview_and_invite_when_logged_out(self):
        from itsdangerous import URLSafeSerializer

        user = self._create_logged_in_user(username="share_owner")
        exercise = "Flat Dumbbell Press"
        self.session.add(
            WorkoutLog(
                user_id=user.id,
                date=datetime(2026, 1, 1, 9, 0, 0),
                workout_name="Push",
                exercise=exercise,
                exercise_string="Flat Dumbbell Press\n30 30 30, 8 8 8",
                sets_json={"weights": [30, 30, 30], "reps": [8, 8, 8]},
                bodyweight=user.bodyweight,
            )
        )
        self.session.add(
            WorkoutLog(
                user_id=user.id,
                date=datetime(2026, 1, 10, 9, 0, 0),
                workout_name="Push Day",
                exercise=exercise,
                exercise_string="Flat Dumbbell Press\n32.5 30 30, 8 8 8",
                sets_json={"weights": [32.5, 30, 30], "reps": [8, 8, 8]},
                bodyweight=user.bodyweight,
            )
        )
        self.session.commit()

        token = URLSafeSerializer(self.app.config.get("SECRET_KEY", "workout-share")).dumps(
            {"user_id": user.id, "date": "2026-01-10"}
        )
        visitor = self.app.test_client()  # a friend opening the link, not logged in
        response = visitor.get(f"/share/{token}")

        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn("Push Day", page)
        self.assertIn('property="og:title"', page)
        self.assertIn("1 new personal best", page)
        self.assertIn("🥇", page)
        self.assertIn("32.5×8", page)
        self.assertIn("Start free", page)
        self.assertNotIn("Est. 1RM", page)
        # "Copy" text spells every set out for newcomers instead of the app shorthand.
        self.assertIn("32.5 kg × 8, 30 kg × 8, 30 kg × 8", page)

        expired = visitor.get("/share/not-a-real-token")
        self.assertEqual(expired.status_code, 200)
        self.assertIn("This link has expired", expired.get_data(as_text=True))

    def test_shared_workout_page_shows_owner_profile_photo(self):
        from itsdangerous import URLSafeSerializer

        user = self._create_logged_in_user(username="share_photo_owner")
        # The login helper leaves `user` detached, so set the photo on a fresh copy.
        self.session.get(User, user.id).profile_image = "uploads/avatars/share_photo_owner.png"
        self.session.add(
            WorkoutLog(
                user_id=user.id,
                date=datetime(2026, 1, 10, 9, 0, 0),
                workout_name="Push Day",
                exercise="Flat Dumbbell Press",
                exercise_string="Flat Dumbbell Press\n30 30, 8 8",
                sets_json={"weights": [30, 30], "reps": [8, 8]},
                bodyweight=user.bodyweight,
            )
        )
        self.session.commit()

        token = URLSafeSerializer(self.app.config.get("SECRET_KEY", "workout-share")).dumps(
            {"user_id": user.id, "date": "2026-01-10"}
        )
        page = self.app.test_client().get(f"/share/{token}").get_data(as_text=True)

        # Photo from the public avatar store, with the initial kept as a fallback.
        self.assertIn('class="sw-avatar"', page)
        self.assertRegex(page, r'<img src="[^"]*avatars/share_photo_owner\.png"')
        self.assertIn('class="sw-initial"', page)

    def test_shortcut_pick_url_is_available_to_regular_users(self):
        self._create_logged_in_user(username="shortcut_pick_user")

        response = self.client.get("/shortcut/pick")

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertIsNotNone(data)
        self.assertTrue(data.get("ok"))
        shortcut_url = data.get("url") or ""
        self.assertIn("/shortcut/pick/", shortcut_url)
        self.assertEqual(data.get("query_param"), "key")

        token_path = urlsplit(shortcut_url).path
        token_response = self.client.get(token_path)
        self.assertEqual(token_response.status_code, 400)
        self.assertIn("Missing key", token_response.get_data(as_text=True))

    def test_shortcut_replies_name_the_server_so_shortcuts_can_fall_back(self):
        self._create_logged_in_user(username="shortcut_fallback_user")
        pick_path = urlsplit(self.client.get("/shortcut/pick").get_json()["url"]).path
        log_path = urlsplit(self.client.get("/shortcut/log").get_json()["url"]).path

        # Plain text stays the default for existing Shortcuts.
        self.assertEqual(self.client.get(pick_path).mimetype, "text/plain")

        pick = self.client.get(f"{pick_path}?format=json").get_json()
        self.assertEqual(pick["server"], "Local")
        self.assertFalse(pick["ok"])
        self.assertIn("Missing key", pick["error"])

        log = self.client.post(log_path, data={"workout_text": ""}).get_json()
        self.assertEqual(log["server"], "Local")
        self.assertFalse(log["ok"])

        bad_token = self.client.post("/shortcut/log/not-a-token").get_json()
        self.assertEqual(bad_token["server"], "Local")

    def test_apple_shortcuts_page_gives_one_key_that_works_for_both_shortcuts(self):
        self._create_logged_in_user(username="shortcut_key_user")

        page = self.client.get("/shortcut/urls").get_data(as_text=True)
        self.assertIn("Apple Shortcuts", page)
        self.assertIn("shortcuts/Log%20Workout.shortcut", page)
        self.assertIn("shortcuts/Get%20Workout.shortcut", page)
        self.assertIn("Workout Logs folder", page)  # where the shortcuts keep workout notes
        key = re.search(r'value="([^"]+)"[^>]*aria-label="Shortcut key"', page).group(1)

        log = self.client.post(f"/shortcut/log/{key}", data={"workout_text": ""}).get_json()
        self.assertEqual(log["error"], "Please enter workout data.")  # key accepted, note was empty
        pick = self.client.get(f"/shortcut/pick/{key}?format=json").get_json()
        self.assertIn("Missing key", pick["error"])  # key accepted, no session named

        for name in ("Log Workout", "Get Workout"):
            download = self.client.get(f"/static/shortcuts/{name}.shortcut")
            self.assertEqual(download.status_code, 200)
            self.assertEqual(download.data[:4], b"AEA1")  # signed Shortcuts file
            download.close()

    def _plan_owner_with_plan(self):
        owner = User(username="plan_owner", email="owner@example.com", role=UserRole.ADMIN, is_verified=True)
        self.session.add(owner)
        self.session.flush()
        owner_id = int(owner.id)
        self.session.add(Plan(user_id=owner_id, text_content="Session 1 - Owner Push\nBench Press - [3, 6-8]"))
        self.session.commit()
        return owner_id

    def test_new_accounts_start_with_a_copy_of_the_admin_rep_ranges(self):
        from models import RepRange, _seed_user_data
        owner = User(username="range_owner", role=UserRole.ADMIN, is_verified=True)
        self.session.add(owner)
        self.session.flush()
        self.session.add(RepRange(user_id=owner.id, text_content="Bench Press: 5-8\nPull-Ups: 6-10"))
        self.session.commit()

        user = User(username="range_newcomer", role=UserRole.USER, is_verified=True)
        self.session.add(user)
        self.session.flush()
        _seed_user_data(self.session, user)
        self.session.commit()
        copy = self.session.query(RepRange).filter_by(user_id=user.id).one()
        self.assertEqual(copy.text_content, "Bench Press: 5-8\nPull-Ups: 6-10")

        # A copy, not a link: the admin's later changes don't rewrite it.
        self.session.query(RepRange).filter_by(user_id=owner.id).one().text_content = "Bench Press: 3-5"
        self.session.commit()
        self.assertEqual(self.session.query(RepRange).filter_by(user_id=user.id).one().text_content,
                         "Bench Press: 5-8\nPull-Ups: 6-10")

    def test_new_accounts_follow_the_admin_plan_and_rep_ranges(self):
        user = User(username="brand_new", role=UserRole.USER, is_verified=True)
        self.session.add(user)
        self.session.commit()
        self.assertTrue(user.follow_admin_plan)
        self.assertTrue(user.follow_admin_exercises)

    def test_another_admin_can_follow_the_owner_plan_everywhere(self):
        self._plan_owner_with_plan()
        friend = self._create_logged_in_user(username="friend_admin", follow_admin=True)
        friend.role = UserRole.ADMIN
        self.session.commit()

        # The switch is there for them, and it's on.
        page = self.client.get("/set_plan").get_data(as_text=True)
        self.assertIn('name="follow_admin_plan" value="0"', page)
        self.assertIn('st-switch is-on', page)
        self.assertIn('name="follow_admin_exercises"', self.client.get("/set_exercises").get_data(as_text=True))

        # And the shortcut gets the owner's sessions, not the built-in plan.
        pick_path = urlsplit(self.client.get("/shortcut/pick").get_json()["url"]).path
        self.assertEqual(self.client.get(f"{pick_path}?list=1").get_json()["sessions"], ["Session 1 - Owner Push"])

    def test_plan_owner_has_no_follow_switch(self):
        owner_id = self._plan_owner_with_plan()
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(owner_id)
            sess["_fresh"] = True
        page = self.client.get("/set_plan").get_data(as_text=True)
        self.assertNotIn('name="follow_admin_plan"', page)
        self.assertIn("Owner Push", page)

    def test_owner_plan_equal_to_the_built_in_plan_is_still_followed(self):
        from list_of_exercise import DEFAULT_PLAN
        owner = User(username="plan_owner", role=UserRole.ADMIN, is_verified=True)
        other_admin = User(username="other_admin", role=UserRole.ADMIN, is_verified=True)
        self.session.add_all([owner, other_admin])
        self.session.flush()
        self.session.add(Plan(user_id=owner.id, text_content=DEFAULT_PLAN))
        self.session.add(Plan(user_id=other_admin.id, text_content="Session 1 - Not This One\nSquat"))
        self.session.commit()
        self._create_logged_in_user(username="follower", follow_admin=True)

        pick_path = urlsplit(self.client.get("/shortcut/pick").get_json()["url"]).path
        sessions = self.client.get(f"{pick_path}?list=1").get_json()["sessions"]
        self.assertEqual(sessions[0], "Session 1 - Chest & Biceps")

    def test_not_following_without_own_plan_gets_the_built_in_plan(self):
        from list_of_exercise import DEFAULT_PLAN, PREVIOUS_DEFAULT_PLAN
        self._plan_owner_with_plan()
        user = self._create_logged_in_user(username="own_way")
        # An untouched copy of the old built-in plan counts as no plan of their own.
        self.session.add(Plan(user_id=user.id, text_content=PREVIOUS_DEFAULT_PLAN))
        self.session.commit()

        pick_path = urlsplit(self.client.get("/shortcut/pick").get_json()["url"]).path
        sessions = self.client.get(f"{pick_path}?list=1").get_json()["sessions"]
        self.assertEqual(len(sessions), 16)
        self.assertEqual(sessions[0], "Session 1 - Chest & Biceps")
        # The editor starts from the built-in plan, ready to change.
        self.assertIn("Hanging Leg Raises", self.client.get("/set_plan").get_data(as_text=True))
        self.assertIn("Session 16", DEFAULT_PLAN)

    def test_shortcut_pick_lists_the_users_own_sessions(self):
        user = self._create_logged_in_user(username="shortcut_list_user")
        self.session.add(Plan(user_id=user.id, text_content="Session 1 - Push\nBench Press - [3, 6-8]\n\nSession 2 - Pull\nPull Ups - [3, 6-8]"))
        self.session.commit()
        pick_path = urlsplit(self.client.get("/shortcut/pick").get_json()["url"]).path

        data = self.client.get(f"{pick_path}?list=1").get_json()
        self.assertTrue(data["ok"], data)
        self.assertEqual(data["server"], "Local")
        self.assertEqual(data["sessions"], ["Session 1 - Push", "Session 2 - Pull"])

        # Each listed name works as the key for fetching that session.
        picked = self.client.get(pick_path, query_string={"format": "json", "key": data["sessions"][1]}).get_json()
        self.assertTrue(picked["ok"], picked)
        self.assertIn("Pull Ups", picked["text"])

    def test_shortcut_key_problems_explain_themselves_to_the_shortcut(self):
        # Empty key (the "paste your key" question was skipped): the app's JSON, not a
        # "not found" web page, so the shortcut shows the message instead of "not reachable".
        for path in ("/shortcut/pick/?list=1", "/shortcut/log/"):
            data = self.client.get(path).get_json()
            self.assertIsNotNone(data, path)
            self.assertEqual(data["server"], "Local")
            self.assertIn("shortcut key is missing", data["error"])
            self.assertIn("Apple Shortcuts", data["error"])

        wrong = self.client.get("/shortcut/pick/not-a-key?list=1").get_json()
        self.assertIn("shortcut key isn't valid", wrong["error"])
        self.assertIn("shortcut key isn't valid", self.client.post("/shortcut/log/not-a-key").get_json()["error"])
        # Old plain-text shortcuts keep their old reply.
        self.assertEqual(self.client.get("/shortcut/pick/not-a-key").get_data(as_text=True), "Invalid shortcut token.")
        # Other missing pages are unchanged.
        self.assertIn("text/html", self.client.get("/no-such-page").content_type)

    def test_shortcut_log_accepts_rich_text_notes(self):
        self._create_logged_in_user(username="shortcut_rich_text_user")
        log_path = urlsplit(self.client.get("/shortcut/log").get_json()["url"]).path

        # Apple Notes rich text: its own line breaks and non-breaking spaces.
        note = "12/01 Chest Day\u2028Bench Press\u00a0100x5\u2028Pec Fly 15 15"
        data = self.client.post(log_path, data={"workout_text": note}).get_json()
        self.assertTrue(data["ok"], data)
        self.assertEqual(data["exercise_count"], 2)

        # The note sent as an HTML file instead of a form field.
        page = b"<div><b>13/01 Back Day</b></div><div>Pull Ups -35x5</div>"
        data = self.client.post(
            log_path,
            data={"workout_text": (io.BytesIO(page), "note.html")},
            content_type="multipart/form-data",
        ).get_json()
        self.assertTrue(data["ok"], data)
        self.assertEqual(data["input_source"], "file")

    def test_custom_retrieve_uses_selected_exercises_in_selection_order(self):
        user = self._create_logged_in_user(username="custom_retrieve_user")
        self.session.add(
            Plan(
                user_id=user.id,
                text_content="Custom Focus 1\nCustom Lift - [4, 6-8]",
            )
        )
        self.session.commit()

        selection_page = self.client.get("/retrieve/custom")
        self.assertEqual(selection_page.status_code, 200)
        selection_html = selection_page.get_data(as_text=True)
        self.assertIn("Custom Lift", selection_html)
        self.assertIn('class="exercise-picker-panel"', selection_html)
        self.assertIn('id="customSortDialog"', selection_html)
        self.assertIn('id="selectedExerciseList"', selection_html)
        self.assertIn('id="reviewExercisesBtn"', selection_html)
        header_position = selection_html.index('custom-exercise-list-header')
        self.assertLess(
            selection_html.index('class="exercise-picker-panel"'),
            header_position,
        )
        self.assertLess(
            header_position,
            selection_html.index('id="selectedSection"'),
        )
        self.assertLess(
            header_position,
            selection_html.index('id="getWorkoutBtn"'),
        )

        response = self.client.post(
            "/retrieve/custom",
            data={
                "exercise": [
                    normalize_exercise_name("Barbell Curl"),
                    normalize_exercise_name("Custom Lift"),
                ],
            },
        )

        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn("Custom Workout", page)
        self.assertIn("Custom Lift - [3, 6-8]", page)
        self.assertIn("2 Exercises", page)
        self.assertLess(page.index("Barbell Curl"), page.index("Custom Lift - [3, 6-8]"))
        self.assertEqual(
            self.session.query(CustomRetrievalEvent)
            .filter_by(user_id=user.id)
            .count(),
            2,
        )

    def test_custom_retrieve_review_restores_a_draft_for_adding_exercises(self):
        user = self._create_logged_in_user(username="custom_retrieve_review_user")
        self.session.add(
            Plan(
                user_id=user.id,
                text_content="Custom Focus 1\nCustom Lift - [4, 6-8]",
            )
        )
        self.session.commit()

        custom_lift_key = normalize_exercise_name("Custom Lift")
        review_start = self.client.post(
            "/retrieve/custom",
            data={
                "flow": "review",
                "exercise": [custom_lift_key],
                "two_set_exercise": [custom_lift_key],
            },
        )

        self.assertEqual(review_start.status_code, 302)
        self.assertIn("/retrieve/custom/review", review_start.headers["Location"])

        review_page = self.client.get("/retrieve/custom/review")
        self.assertEqual(review_page.status_code, 200)
        review_html = review_page.get_data(as_text=True)
        self.assertIn("Review Exercises", review_html)
        self.assertIn("Custom Lift", review_html)
        self.assertIn('id="reviewGetWorkoutBtn"', review_html)

        add_more = self.client.post(
            "/retrieve/custom/review",
            data={
                "review_action": "add_more",
                "exercise": [custom_lift_key],
                "two_set_exercise": [custom_lift_key],
            },
        )
        self.assertEqual(add_more.status_code, 302)
        self.assertIn("/retrieve/custom", add_more.headers["Location"])

        selector_page = self.client.get("/retrieve/custom")
        selector_html = selector_page.get_data(as_text=True)
        self.assertIn(f'let selectedKeys = ["{custom_lift_key}"];', selector_html)
        self.assertIn(f'new Set(["{custom_lift_key}"])', selector_html)

        final_workout = self.client.post(
            "/retrieve/custom",
            data={
                "exercise": [custom_lift_key],
                "two_set_exercise": [custom_lift_key],
            },
        )
        self.assertEqual(final_workout.status_code, 200)
        self.assertIn("Custom Lift - [2, 6-8]", final_workout.get_data(as_text=True))
        with self.client.session_transaction() as browser_session:
            self.assertNotIn("custom_retrieval_draft", browser_session)

    def test_custom_retrieve_two_set_override_replaces_plan_set_target(self):
        user = self._create_logged_in_user(username="custom_retrieve_two_sets_user")
        self.session.add(
            Plan(
                user_id=user.id,
                text_content="Custom Focus 1\nCustom Lift - [4, 6-8]",
            )
        )
        self.session.commit()
        custom_lift_key = normalize_exercise_name("Custom Lift")

        response = self.client.post(
            "/retrieve/custom",
            data={
                "exercise": [custom_lift_key],
                "two_set_exercise": [custom_lift_key],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Custom Lift - [2, 6-8]", response.get_data(as_text=True))

    def test_custom_retrieve_prefers_recent_retrieval_frequency(self):
        user = self._create_logged_in_user(username="custom_retrieve_ranking_user")
        walking_lunge_key = normalize_exercise_name("Walking Dumbbell Lunges")
        self.session.add_all(
            [
                CustomRetrievalEvent(
                    user_id=user.id,
                    exercise_key=walking_lunge_key,
                    retrieved_at=datetime.now() - timedelta(days=1),
                ),
                CustomRetrievalEvent(
                    user_id=user.id,
                    exercise_key=walking_lunge_key,
                    retrieved_at=datetime.now() - timedelta(days=2),
                ),
                CustomRetrievalEvent(
                    user_id=user.id,
                    exercise_key=normalize_exercise_name("Flat Dumbbell Press"),
                    retrieved_at=datetime.now() - timedelta(days=100),
                ),
            ]
        )
        self.session.commit()

        response = self.client.get("/retrieve/custom")

        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertLess(page.index("Walking Dumbbell Lunges"), page.index("Barbell Curl"))

    def test_custom_retrieve_sort_preference_is_saved_per_user(self):
        user = self._create_logged_in_user(username="custom_retrieve_sort_user")

        response = self.client.post(
            "/retrieve/custom/sort-preference",
            data={"sort_mode": "alpha_desc"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"ok": True, "sort_mode": "alpha_desc"})
        preference = self.session.query(CustomRetrievalPreference).filter_by(user_id=user.id).one()
        self.assertEqual(preference.sort_mode, "alpha_desc")

        selection_page = self.client.get("/retrieve/custom")
        self.assertIn(
            'data-sort-mode="alpha_desc"',
            selection_page.get_data(as_text=True),
        )

        invalid_response = self.client.post(
            "/retrieve/custom/sort-preference",
            data={"sort_mode": "unknown"},
        )
        self.assertEqual(invalid_response.status_code, 400)

    def test_custom_retrieve_rejects_empty_selection(self):
        self._create_logged_in_user(username="custom_retrieve_empty_user")

        response = self.client.post("/retrieve/custom")

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/retrieve/custom"))

    def test_settings_shows_shortcut_urls_for_regular_users(self):
        self._create_logged_in_user(username="shortcut_settings_user")

        # Settings is a hub; shortcut URLs live on the Integrations page.
        hub = self.client.get("/settings")
        self.assertEqual(hub.status_code, 200)
        self.assertIn('href="/settings/integrations"', hub.get_data(as_text=True))

        response = self.client.get("/settings/integrations")

        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn("Apple Shortcuts", page)
        self.assertNotIn("Shortcut Retrieve URL", page)

    def test_more_settings_shows_bodyweight_exercise_controls(self):
        self._create_logged_in_user(username="more_settings_user")

        response = self.client.get("/settings/more")

        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn("Bodyweight exercises", page)
        self.assertIn("Crunches A", page)

    def test_bodyweight_log_uses_offsets_for_saved_strength(self):
        user = self._create_logged_in_user(username="bw_offset_user")
        user.bodyweight = 80.0
        parsed = {
            "date": datetime(2026, 9, 6),
            "workout_name": "Core",
            "exercises": [
                {
                    "name": "Crunches A",
                    "exercise_string": "Crunches A\n2.5 1, 14 21",
                    "weights": [2.5, 1.0],
                    "reps": [14, 21],
                    "valid": True,
                }
            ],
        }

        handle_workout_log(self.session, user, parsed)
        self.session.commit()

        log = self.session.query(WorkoutLog).filter_by(user_id=user.id, exercise="Crunches A").one()
        self.assertTrue(log.uses_bodyweight)
        self.assertEqual(log.sets_json["weights"][0], 2.5)
        self.assertGreater(log.estimated_1rm, 120)

    def test_explicit_bw_token_auto_selects_bodyweight_exercise(self):
        user = self._create_logged_in_user(username="bw_auto_user")
        parsed = {
            "date": datetime(2026, 9, 6),
            "workout_name": "Core",
            "exercises": [
                {
                    "name": "Cable Core Raise",
                    "exercise_string": "Cable Core Raise\nBW+3, 10",
                    "weights": [3.0],
                    "reps": [10],
                    "valid": True,
                }
            ],
        }

        handle_workout_log(self.session, user, parsed)
        self.session.commit()

        pref = self.session.query(BodyweightExercisePreference).filter_by(
            user_id=user.id,
            exercise_key=bodyweight_exercise_key("Cable Core Raise"),
        ).one()
        self.assertTrue(pref.is_bodyweight)
        self.assertEqual(pref.source, "auto")

    def test_bodyweight_parser_accepts_uppercase_bw_and_bodyweight(self):
        bw_result = workout_parser(
            "06/09 Core\nCable Core Raise\nBW+3, 10",
            bodyweight=80,
            preserve_bodyweight_offsets=True,
        )
        bodyweight_result = workout_parser(
            "06/09 Core\nCable Core Raise\nbodyweight, 10",
            bodyweight=80,
            preserve_bodyweight_offsets=True,
        )

        self.assertEqual(bw_result["exercises"][0]["weights"], [3.0, 3.0, 3.0])
        self.assertEqual(bodyweight_result["exercises"][0]["weights"], [0.0, 0.0, 0.0])

    def test_deselect_bodyweight_exercise_requires_history_resolution(self):
        user = self._create_logged_in_user(username="bw_deselect_user")
        self.session.add(
            WorkoutLog(
                user_id=user.id,
                date=datetime(2026, 9, 1),
                workout_name="Core",
                exercise="Crunches A",
                exercise_string="Crunches A\nBW+2, 15",
                sets_json={"weights": [2.0], "reps": [15]},
                bodyweight=user.bodyweight,
                uses_bodyweight=True,
                estimated_1rm=123.0,
            )
        )
        self.session.commit()

        response = self.client.post(
            "/settings/more",
            data={
                "form_type": "bodyweight_exercise",
                "exercise_name": "Crunches A",
                "is_bodyweight": "0",
            },
        )

        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn("It already has bodyweight logs", page)
        self.assertTrue(
            self.session.query(WorkoutLog).filter_by(exercise="Crunches A").one().uses_bodyweight
        )

    def test_profile_photo_upload_writes_to_r2_when_configured(self):
        user = self._create_logged_in_user(username="avatar_upload_user")
        image_bytes = BytesIO()
        Image.new("RGB", (32, 32), "red").save(image_bytes, format="PNG")
        image_bytes.seek(0)
        s3 = Mock()

        with patch("workout_tracker.routes.auth.has_r2_profile_image_storage", return_value=True), \
             patch("workout_tracker.routes.auth.get_r2_profile_image_client", return_value=s3), \
             patch("workout_tracker.routes.auth.get_r2_bucket_name", return_value="workout-tracker-avatars"):
            response = self.client.post(
                "/settings",
                data={
                    "form_type": "profile_photo",
                    "profile_image": (image_bytes, "avatar.png"),
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 302)
        s3.put_object.assert_called_once()
        put_kwargs = s3.put_object.call_args.kwargs
        self.assertEqual(put_kwargs["Bucket"], "workout-tracker-avatars")
        self.assertRegex(put_kwargs["Key"], rf"^avatars/user_{user.id}_[0-9a-f]{{8}}\.png$")
        self.assertEqual(put_kwargs["ContentType"], "image/png")
        stored_user = self.session.query(User).filter_by(id=user.id).one()
        self.assertEqual(stored_user.profile_image, put_kwargs["Key"])

    def test_profile_photo_reupload_gets_new_url_and_deletes_old_photo(self):
        user = self._create_logged_in_user(username="avatar_reupload_user")
        self.session.get(User, user.id).profile_image = f"avatars/user_{user.id}.png"
        self.session.commit()
        image_bytes = BytesIO()
        Image.new("RGB", (32, 32), "blue").save(image_bytes, format="PNG")
        image_bytes.seek(0)
        s3 = Mock()

        with patch("workout_tracker.routes.auth.has_r2_profile_image_storage", return_value=True), \
             patch("workout_tracker.routes.auth.get_r2_profile_image_client", return_value=s3), \
             patch("workout_tracker.routes.auth.get_r2_bucket_name", return_value="workout-tracker-avatars"):
            self.client.post(
                "/settings",
                data={
                    "form_type": "profile_photo",
                    "profile_image": (image_bytes, "avatar.png"),
                },
                content_type="multipart/form-data",
            )

        new_key = s3.put_object.call_args.kwargs["Key"]
        self.assertNotEqual(new_key, f"avatars/user_{user.id}.png")
        s3.delete_object.assert_called_once_with(
            Bucket="workout-tracker-avatars",
            Key=f"avatars/user_{user.id}.png",
        )

    def test_r2_client_uses_configured_endpoint_credentials_region_and_bucket(self):
        s3 = object()

        boto3 = Mock()
        boto3.client.return_value = s3

        with patch.dict(
            os.environ,
            {
                "R2_ACCESS_KEY_ID": "test-access-key",
                "R2_SECRET_ACCESS_KEY": "test-secret-key",
                "R2_BUCKET_NAME": "workout-tracker-avatars",
                "R2_ACCOUNT_ID": "c60933f634439b0fb2e6c7762535ba6c",
            },
        ), patch.dict(sys.modules, {"boto3": boto3}):
            client = profile_images.get_r2_profile_image_client()
            bucket = profile_images.get_r2_bucket_name()

        self.assertIs(client, s3)
        self.assertEqual(bucket, "workout-tracker-avatars")
        boto3.client.assert_called_once_with(
            "s3",
            endpoint_url="https://c60933f634439b0fb2e6c7762535ba6c.r2.cloudflarestorage.com",
            aws_access_key_id="test-access-key",
            aws_secret_access_key="test-secret-key",
            region_name="auto",
        )

    def test_remote_profile_image_url_uses_public_r2_bucket(self):
        with self.app.test_request_context(), patch("utils.profile_images.os.path.exists", return_value=False):
            url = profile_images.get_profile_image_url("avatars/user_123.png")

        self.assertEqual(
            url,
            "https://pub-b7699fec85f44832bc1255cae990054b.r2.dev/avatars/user_123.png",
        )

    def test_profile_photo_removal_deletes_from_r2_when_no_local_file_exists(self):
        user = self._create_logged_in_user(username="avatar_remove_user")
        stored_user = self.session.query(User).filter_by(id=user.id).one()
        stored_user.profile_image = "avatars/remote-only.png"
        self.session.commit()
        s3 = Mock()

        with patch("workout_tracker.routes.auth.has_r2_profile_image_storage", return_value=True), \
             patch("workout_tracker.routes.auth.get_r2_profile_image_client", return_value=s3), \
             patch("workout_tracker.routes.auth.get_r2_bucket_name", return_value="workout-tracker-avatars"):
            response = self.client.post(
                "/settings",
                data={"form_type": "remove_photo"},
            )

        self.assertEqual(response.status_code, 302)
        s3.delete_object.assert_called_once_with(
            Bucket="workout-tracker-avatars",
            Key="avatars/remote-only.png",
        )
        updated_user = self.session.query(User).filter_by(id=user.id).one()
        self.assertIsNone(updated_user.profile_image)

    def test_shortcut_urls_page_groups_shortcut_links_for_regular_users(self):
        self._create_logged_in_user(username="shortcut_urls_user")

        response = self.client.get("/shortcut/urls")

        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn("Log URL", page)
        self.assertIn("Retrieve URL", page)
        self.assertIn("/shortcut/log/", page)
        self.assertIn("/shortcut/pick/", page)
        self.assertIn("Session names", page)

    def test_shortcut_urls_page_lists_every_deployment_over_https(self):
        self._create_logged_in_user(username="shortcut_hosts_user")
        self.app.config["DEPLOYMENT_URLS"] = (
            "Railway=https://workoutlogger-production-7f91.up.railway.app,"
            "Render=https://workout-logger.onrender.com"
        )

        # Behind an HTTPS-terminating proxy, like Railway and Render.
        response = self.client.get("/shortcut/urls", headers={"X-Forwarded-Proto": "https"})

        page = response.get_data(as_text=True)
        self.assertIn("https://localhost/shortcut/log/", page)
        self.assertIn("https://workoutlogger-production-7f91.up.railway.app/shortcut/log/", page)
        self.assertIn("https://workout-logger.onrender.com/shortcut/log/", page)
        self.assertIn("https://workout-logger.onrender.com/shortcut/pick/", page)
        self.assertNotIn("http://localhost/shortcut", page)
        # The site being viewed comes first, then the other deployments.
        self.assertLess(page.index(">Local<"), page.index(">Railway<"))
        self.assertLess(page.index(">Railway<"), page.index(">Render<"))

    def test_shortcut_mapping_is_available_to_regular_users(self):
        self._create_logged_in_user(username="shortcut_mapping_user")

        response = self.client.get("/shortcut/mapping")

        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn("Session names", page)
        self.assertNotIn("Access denied", page)

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
        self.assertIn("Days that failed", page)
        self.assertIn("32/13", page)

    def test_bulk_import_missing_year_rolls_forward_chronologically(self):
        inferred = _infer_bulk_import_dates(
            [
                {"day": 30, "month": 12, "year": 2023},
                {"day": 31, "month": 12, "year": None},
                {"day": 1, "month": 1, "year": None},
                {"day": 2, "month": 1, "year": None},
            ],
            today=date(2026, 6, 6),
        )

        self.assertEqual(
            inferred,
            [
                date(2023, 12, 30),
                date(2023, 12, 31),
                date(2024, 1, 1),
                date(2024, 1, 2),
            ],
        )

    def test_bulk_import_preview_shows_detected_date_range(self):
        self._create_logged_in_user(username="bulk_range_user")

        payload = "\n".join(
            [
                "30/12/23 Year End",
                "Flat Dumbbell Press - [8-12]",
                "30, 8",
                "",
                "01/01 New Year",
                "Flat Dumbbell Press - [8-12]",
                "35, 8",
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
        self.assertIn("30-12-2023 – 01-01-2024", page)

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
                            "exercise": row["name"],
                            "best_workout_url": row.get("best_workout_url"),
                            "performance_key": row.get("performance_key"),
                        }
                        for row in kwargs.get("rows", [])
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

    def test_workout_detail_uses_aliases_for_previous_and_best_rails(self):
        user = self._create_logged_in_user(username="row_alias_user")
        self.session.add(
            WorkoutLog(
                user_id=user.id,
                date=datetime(2026, 1, 12, 9, 0, 0),
                workout_name="Back",
                exercise="Neutral-Grip Seated Row",
                exercise_string="Neutral-Grip Seated Row - [8-12]\n50 45, 8 8",
                sets_json={"weights": [50, 45], "reps": [8, 8]},
                bodyweight=user.bodyweight,
                top_weight=50,
                top_reps=8,
                estimated_1rm=63.33,
            )
        )
        self.session.add(
            WorkoutLog(
                user_id=user.id,
                date=datetime(2026, 6, 5, 9, 0, 0),
                workout_name="Back",
                exercise="Seated Neutral-Grip Row",
                exercise_string="Seated Neutral-Grip Row - [8-12]\n50 45, 8 8",
                sets_json={"weights": [50, 45], "reps": [8, 8]},
                bodyweight=user.bodyweight,
                top_weight=50,
                top_reps=8,
                estimated_1rm=63.33,
            )
        )
        self.session.commit()

        response = self.client.get("/workout/2026-06-05")

        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn("Last · best</a><span class=\"date\">12 Jan</span>", page)
        self.assertNotIn("First log", page)
        self.assertNotIn("New baseline", page)

    def test_log_summary_uses_aliases_for_reordered_saved_exercises(self):
        user = self._create_logged_in_user(username="log_summary_alias_user")
        self.session.add(
            WorkoutLog(
                user_id=user.id,
                date=datetime(2026, 3, 22, 9, 0, 0),
                workout_name="Session 2",
                exercise="Rear Delt Machine Fly",
                exercise_string="Rear Delt Machine Fly - [12-20]\n60 53.5 50, 14 19 20",
                sets_json={"weights": [60, 53.5, 50], "reps": [14, 19, 20]},
                bodyweight=user.bodyweight,
                estimated_1rm=88.0,
            )
        )
        self.session.add(
            WorkoutLog(
                user_id=user.id,
                date=datetime(2026, 4, 1, 9, 0, 0),
                workout_name="Session 2",
                exercise="Wrist Flexion - Dumbbell",
                exercise_string="Wrist Flexion - Dumbbell - [12-20]\n15 13.8 12.5, 14 18 21",
                sets_json={"weights": [15, 13.8, 12.5], "reps": [14, 18, 21]},
                bodyweight=user.bodyweight,
                estimated_1rm=22.0,
            )
        )
        self.session.commit()

        parsed = {
            "date": datetime(2026, 6, 10, 9, 0, 0),
            "workout_name": "Session 2 - Shoulders & Forearms",
            "exercises": [
                {
                    "name": "Machine Rear Delt Fly",
                    "exercise_string": "Machine Rear Delt Fly\n60 53.5 50, 14 19 20",
                    "weights": [60, 53.5, 50],
                    "reps": [14, 19, 20],
                    "valid": True,
                },
                {
                    "name": "Dumbbell Wrist Flexion",
                    "exercise_string": "Dumbbell Wrist Flexion\n15 12.5, 16 20",
                    "weights": [15, 12.5],
                    "reps": [16, 20],
                    "valid": True,
                },
            ],
        }

        immediate_summary = handle_workout_log(self.session, user, parsed)
        self.session.commit()

        immediate_by_name = {row["name"]: row for row in immediate_summary}
        self.assertNotEqual(immediate_by_name["Machine Rear Delt Fly"]["old"], "First Log")
        self.assertNotEqual(immediate_by_name["Dumbbell Wrist Flexion"]["old"], "First Log")

        recomputed_summary, _, _ = compute_workout_summary_for_date(
            self.session,
            user,
            datetime(2026, 6, 10),
        )
        recomputed_by_name = {row["name"]: row for row in recomputed_summary}

        self.assertNotEqual(recomputed_by_name["Machine Rear Delt Fly"]["old"], "First Log")
        self.assertNotEqual(recomputed_by_name["Dumbbell Wrist Flexion"]["old"], "First Log")
        self.assertIn("60 x 14, 53.5 x 19, 50 x 20", recomputed_by_name["Machine Rear Delt Fly"]["old"])
        self.assertIn("15 x", recomputed_by_name["Dumbbell Wrist Flexion"]["old"])

    def test_stats_query_alias_loads_specific_exercise_chart(self):
        user = self._create_logged_in_user(username="stats_alias_user")
        self.session.add(
            WorkoutLog(
                user_id=user.id,
                date=datetime(2026, 1, 12, 9, 0, 0),
                workout_name="Back",
                exercise="Neutral-Grip Seated Row",
                exercise_string="Neutral-Grip Seated Row - [8-12]\n50 45, 8 8",
                sets_json={"weights": [50, 45], "reps": [8, 8]},
                bodyweight=user.bodyweight,
                estimated_1rm=63.33,
            )
        )
        self.session.add(
            WorkoutLog(
                user_id=user.id,
                date=datetime(2026, 6, 5, 9, 0, 0),
                workout_name="Back",
                exercise="Seated Neutral-Grip Row",
                exercise_string="Seated Neutral-Grip Row - [8-12]\n50 45, 8 8",
                sets_json={"weights": [50, 45], "reps": [8, 8]},
                bodyweight=user.bodyweight,
                estimated_1rm=63.33,
            )
        )
        self.session.commit()

        response = self.client.get("/stats?exercise=Seated%20Neutral-Grip%20Row")

        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn('"value": "Neutral-Grip Seated Row"', page)
        self.assertIn('"label": "Neutral-Grip Seated Row"', page)

    def test_stats_sort_and_range_preferences_are_saved_and_rendered(self):
        user = self._create_logged_in_user(username="stats_sort_user")
        prefs_tag = '<script type="application/json" id="statsPreferencesData">'

        page = self.client.get("/stats").get_data(as_text=True)
        self.assertIn(prefs_tag + '{"range": "all", "sort_mode": "most_viewed"}</script>', page)

        response = self.client.post("/stats/preferences", data={"sort_mode": "alpha_desc"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"ok": True, "sort_mode": "alpha_desc", "range": "all"})

        # Saving the range keeps the sort mode.
        response = self.client.post("/stats/preferences", data={"range": "365"})
        self.assertEqual(response.get_json(), {"ok": True, "sort_mode": "alpha_desc", "range": "365"})
        preference = self.session.query(StatsPreference).filter_by(user_id=user.id).one()
        self.assertEqual((preference.sort_mode, preference.time_range), ("alpha_desc", "365"))

        page = self.client.get("/stats").get_data(as_text=True)
        self.assertIn(prefs_tag + '{"range": "365", "sort_mode": "alpha_desc"}</script>', page)

        self.assertEqual(self.client.post("/stats/preferences", data={"sort_mode": "unknown"}).status_code, 400)
        self.assertEqual(self.client.post("/stats/preferences", data={"range": "7"}).status_code, 400)
        self.assertEqual(self.client.post("/stats/preferences", data={}).status_code, 400)

    def test_stats_exercise_views_are_counted_once_per_visit(self):
        user = self._create_logged_in_user(username="stats_views_user")
        self.session.add(
            WorkoutLog(
                user_id=user.id,
                date=datetime(2026, 2, 3, 9, 0, 0),
                workout_name="Legs",
                exercise="Deadlift",
                exercise_string="Deadlift - [5]\n100, 5",
                sets_json={"weights": [100], "reps": [5]},
                bodyweight=user.bodyweight,
                estimated_1rm=116.67,
            )
        )
        self.session.commit()

        self.client.get("/stats/data/Deadlift")
        self.client.get("/stats/data/Deadlift")  # same visit: not counted again
        view = self.session.query(StatsExerciseView).filter_by(user_id=user.id).one()
        self.assertEqual(view.view_count, 1)

        view.last_viewed_at = datetime.now() - timedelta(hours=1)
        self.session.commit()
        self.client.get("/stats/data/Deadlift")
        view = self.session.query(StatsExerciseView).filter_by(user_id=user.id).one()
        self.assertEqual(view.view_count, 2)

        page = self.client.get("/stats").get_data(as_text=True)
        self.assertIn('"views": 2', page)

    def test_stats_exercise_options_include_session_counts(self):
        user = self._create_logged_in_user(username="stats_sessions_user")
        for day in (3, 3, 10):
            self.session.add(
                WorkoutLog(
                    user_id=user.id,
                    date=datetime(2026, 2, day, 9, 0, 0),
                    workout_name="Back",
                    exercise="Barbell Row",
                    exercise_string="Barbell Row - [8-12]\n50, 8",
                    sets_json={"weights": [50], "reps": [8]},
                    bodyweight=user.bodyweight,
                    estimated_1rm=63.33,
                )
            )
        self.session.commit()

        page = self.client.get("/stats").get_data(as_text=True)

        # Two logs on the same day count as one session.
        self.assertIn('"last": "2026-02-10"', page)
        self.assertIn('"sessions": 2', page)

    def test_workout_detail_vs_best_ignores_future_logs(self):
        user = self._create_logged_in_user(username="workout_user_future_best")
        exercise = "Flat Dumbbell Press"

        self.session.add(
            WorkoutLog(
                user_id=user.id,
                date=datetime(2026, 1, 1, 9, 0, 0),
                workout_name="Session 1",
                exercise=exercise,
                exercise_string="Flat Dumbbell Press - [8-12]\n80 80 80, 8 8 8",
                sets_json={"weights": [80, 80, 80], "reps": [8, 8, 8]},
                bodyweight=user.bodyweight,
                estimated_1rm=101.33,
            )
        )
        self.session.add(
            WorkoutLog(
                user_id=user.id,
                date=datetime(2026, 1, 10, 9, 0, 0),
                workout_name="Session 2",
                exercise=exercise,
                exercise_string="Flat Dumbbell Press - [8-12]\n90 90 90, 8 8 8",
                sets_json={"weights": [90, 90, 90], "reps": [8, 8, 8]},
                bodyweight=user.bodyweight,
                estimated_1rm=114.0,
            )
        )
        # This future log should not affect the /workout/2026-01-10 "Vs Best" rail.
        self.session.add(
            WorkoutLog(
                user_id=user.id,
                date=datetime(2026, 1, 20, 9, 0, 0),
                workout_name="Session 3",
                exercise=exercise,
                exercise_string="Flat Dumbbell Press - [8-12]\n130 130 130, 5 5 5",
                sets_json={"weights": [130, 130, 130], "reps": [5, 5, 5]},
                bodyweight=user.bodyweight,
                estimated_1rm=151.67,
            )
        )
        self.session.commit()

        def _fake_render(template_name, **kwargs):
            if template_name == "workout_detail.html":
                return {
                    "rows": [
                        {
                            "exercise": row["name"],
                            "best_workout_url": row.get("best_workout_url"),
                            "performance_key": row.get("performance_key"),
                        }
                        for row in kwargs.get("rows", [])
                    ]
                }
            return {"template": template_name}

        with patch("workout_tracker.routes.workouts.render_template", side_effect=_fake_render):
            response = self.client.get("/workout/2026-01-10")

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertIsNotNone(data)
        self.assertTrue(data.get("rows"))

        row = data["rows"][0]
        best_url = row.get("best_workout_url") or ""
        m = re.search(r"/workout/(\d{4}-\d{2}-\d{2})", best_url)
        self.assertIsNotNone(m, msg=f"best_workout_url missing date: {best_url}")
        best_link_date = m.group(1)

        self.assertEqual(best_link_date, "2026-01-01")
        self.assertEqual(row.get("performance_key"), "gold_strength")

    def test_classifier_awards_gold_load_only_when_top_weight_beats_reference(self):
        user = self._create_logged_in_user(username="gold_load_user")
        exercise = "Tie Lift"
        self.session.add(
            WorkoutLog(
                user_id=user.id,
                date=datetime(2026, 1, 1, 9, 0, 0),
                workout_name="Baseline",
                exercise=exercise,
                exercise_string="Tie Lift\n22.5 18 15, 10 20 30",
                sets_json={"weights": [22.5, 18, 15], "reps": [10, 20, 30]},
                bodyweight=user.bodyweight,
                estimated_1rm=30.0,
            )
        )
        current = WorkoutLog(
            user_id=user.id,
            date=datetime(2026, 1, 10, 9, 0, 0),
            workout_name="Current",
            exercise=exercise,
            exercise_string="Tie Lift\n25 22.5 18, 6 10 20",
            sets_json={"weights": [25, 22.5, 18], "reps": [6, 10, 20]},
            bodyweight=user.bodyweight,
            estimated_1rm=30.0,
        )
        self.session.add(current)
        self.session.commit()

        perf = classify_exercise_performance(
            self.session,
            user.id,
            exercise,
            current.sets_json,
            current_log_id=current.id,
            current_exercise_string=current.exercise_string,
        )

        self.assertEqual(perf["key"], "gold_load")

    def test_classifier_awards_silver_load_when_second_weight_beats_reference(self):
        user = self._create_logged_in_user(username="silver_load_user")
        exercise = "Tie Lift"
        self.session.add(
            WorkoutLog(
                user_id=user.id,
                date=datetime(2026, 1, 1, 9, 0, 0),
                workout_name="Baseline",
                exercise=exercise,
                exercise_string="Tie Lift\n25 18 15, 6 20 30",
                sets_json={"weights": [25, 18, 15], "reps": [6, 20, 30]},
                bodyweight=user.bodyweight,
                estimated_1rm=30.0,
            )
        )
        current = WorkoutLog(
            user_id=user.id,
            date=datetime(2026, 1, 10, 9, 0, 0),
            workout_name="Current",
            exercise=exercise,
            exercise_string="Tie Lift\n25 22.5 18, 6 10 20",
            sets_json={"weights": [25, 22.5, 18], "reps": [6, 10, 20]},
            bodyweight=user.bodyweight,
            estimated_1rm=30.0,
        )
        self.session.add(current)
        self.session.commit()

        perf = classify_exercise_performance(
            self.session,
            user.id,
            exercise,
            current.sets_json,
            current_log_id=current.id,
            current_exercise_string=current.exercise_string,
        )

        self.assertEqual(perf["key"], "silver_load")

    def test_classifier_is_consistent_when_tied_scores_and_loads_do_not_beat_reference(self):
        user = self._create_logged_in_user(username="consistent_load_user")
        exercise = "Tie Lift"
        self.session.add(
            WorkoutLog(
                user_id=user.id,
                date=datetime(2026, 1, 1, 9, 0, 0),
                workout_name="Baseline",
                exercise=exercise,
                exercise_string="Tie Lift\n25 22.5 18, 6 10 20",
                sets_json={"weights": [25, 22.5, 18], "reps": [6, 10, 20]},
                bodyweight=user.bodyweight,
                estimated_1rm=30.0,
            )
        )
        current = WorkoutLog(
            user_id=user.id,
            date=datetime(2026, 1, 10, 9, 0, 0),
            workout_name="Current",
            exercise=exercise,
            exercise_string="Tie Lift\n25 18 15, 6 20 30",
            sets_json={"weights": [25, 18, 15], "reps": [6, 20, 30]},
            bodyweight=user.bodyweight,
            estimated_1rm=30.0,
        )
        self.session.add(current)
        self.session.commit()

        perf = classify_exercise_performance(
            self.session,
            user.id,
            exercise,
            current.sets_json,
            current_log_id=current.id,
            current_exercise_string=current.exercise_string,
        )

        self.assertEqual(perf["key"], "consistent")

    def test_classifier_reference_prefers_full_set_logs_like_best_log_lookup(self):
        user = self._create_logged_in_user(username="full_set_reference_user")
        exercise = "Reference Lift"
        short_day = WorkoutLog(
            user_id=user.id,
            date=datetime(2026, 1, 1, 9, 0, 0),
            workout_name="Short",
            exercise=exercise,
            exercise_string="Reference Lift\n40 40 40 40, 10 10 10 10",
            sets_json={"weights": [40, 40, 40, 40], "reps": [10, 10, 10, 10]},
            bodyweight=user.bodyweight,
        )
        full_day = WorkoutLog(
            user_id=user.id,
            date=datetime(2026, 1, 5, 9, 0, 0),
            workout_name="Full",
            exercise=exercise,
            exercise_string="Reference Lift\n30 30 30 30 30, 10 10 10 10 10",
            sets_json={"weights": [30, 30, 30, 30, 30], "reps": [10, 10, 10, 10, 10]},
            bodyweight=user.bodyweight,
        )
        current = WorkoutLog(
            user_id=user.id,
            date=datetime(2026, 1, 10, 9, 0, 0),
            workout_name="Current",
            exercise=exercise,
            exercise_string="Reference Lift\n35 35 35 35 35, 10 10 10 10 10",
            sets_json={"weights": [35, 35, 35, 35, 35], "reps": [10, 10, 10, 10, 10]},
            bodyweight=user.bodyweight,
        )
        self.session.add_all([short_day, full_day, current])
        self.session.commit()

        day_start = datetime(2026, 1, 10)
        perf = classify_exercise_performance(
            self.session,
            user.id,
            exercise,
            current.sets_json,
            target_sets=5,
            current_log_id=current.id,
            current_exercise_string=current.exercise_string,
            historical_before_dt=day_start,
        )
        best_log = get_best_log_for_exercise_before_date(
            self.session,
            user.id,
            exercise,
            target_sets=5,
            workout_day_start_dt=day_start,
        )

        # The 4-set day must not become the medal baseline while "Vs Best" links the 5-set day.
        self.assertEqual(best_log.id, full_day.id)
        self.assertEqual(perf["reference_log_id"], full_day.id)
        self.assertEqual(perf["key"], "gold_strength")

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
                            "exercise": row["name"],
                            "performance_key": row.get("performance_key"),
                        }
                        for row in kwargs.get("rows", [])
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

    def test_declared_set_count_expands_shorthand_before_pr_update(self):
        user = self._create_logged_in_user(username="declared_set_pr_user")
        exercise = "Flat Dumbbell Press"
        baseline_date = datetime(2026, 1, 1, 9, 0, 0)
        baseline_sets = {"weights": [100, 95, 90], "reps": [5, 5, 5]}
        baseline_best_string = "Flat Dumbbell Press - [3, 6-8]\n100 95 90, 5 5 5"

        baseline_log = WorkoutLog(
            user_id=user.id,
            date=baseline_date,
            workout_name="Session 1",
            exercise=exercise,
            exercise_string=baseline_best_string,
            sets_json=baseline_sets,
            bodyweight=user.bodyweight,
            estimated_1rm=116.67,
        )
        self.session.add(baseline_log)
        self.session.flush()
        self.session.add(
            Lift(
                user_id=user.id,
                exercise=exercise,
                best_log_id=baseline_log.id,
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
        self.assertIsNotNone(lift.best_log)
        self.assertEqual(lift.best_log.sets_json, {"weights": [130.0, 130.0, 130.0], "reps": [5, 5, 5]})
        self.assertEqual(lift.best_log.exercise_string, "Flat Dumbbell Press - [3, 6-8]\n130 130, 5 5")
        self.assertEqual(lift.best_log.date, datetime(2026, 1, 10, 9, 0, 0))

    def test_declared_set_count_expands_smaller_history_for_comparison(self):
        user = self._create_logged_in_user(username="expanded_baseline_user")
        exercise = "Flat Dumbbell Press"

        # Historical shorthand expands to three sets, so it remains comparable.
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
                            "exercise": row["name"],
                            "performance_key": row.get("performance_key"),
                            "performance_label": row.get("performance_label"),
                        }
                        for row in kwargs.get("rows", [])
                    ]
                }
            return {"template": template_name}

        with patch("workout_tracker.routes.workouts.render_template", side_effect=_fake_render):
            response = self.client.get("/workout/2026-01-10")

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data.get("rows"))
        row = data["rows"][0]
        self.assertEqual(row.get("performance_key"), "significantly_off")
        self.assertEqual(row.get("performance_label"), "Below best")


if __name__ == "__main__":
    unittest.main()
