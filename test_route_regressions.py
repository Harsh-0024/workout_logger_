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
        self.assertIn("Custom Lift - [4, 6-8]", page)
        self.assertIn("2 Exercises", page)
        self.assertLess(page.index("Barbell Curl"), page.index("Custom Lift - [4, 6-8]"))
        self.assertEqual(
            self.session.query(CustomRetrievalEvent)
            .filter_by(user_id=user.id)
            .count(),
            2,
        )

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

        response = self.client.get("/settings")

        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn("Shortcut URLs", page)
        self.assertNotIn("Shortcut Retrieve URL", page)

    def test_more_settings_shows_bodyweight_exercise_controls(self):
        self._create_logged_in_user(username="more_settings_user")

        response = self.client.get("/settings/more")

        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn("Bodyweight Exercises", page)
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
        self.assertIn("previous bodyweight history", page)
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
        self.assertEqual(put_kwargs["Key"], f"avatars/user_{user.id}.png")
        self.assertEqual(put_kwargs["ContentType"], "image/png")
        stored_user = self.session.query(User).filter_by(id=user.id).one()
        self.assertEqual(stored_user.profile_image, f"avatars/user_{user.id}.png")

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
        self.assertIn("Shortcut Session Mapping", page)

    def test_shortcut_mapping_is_available_to_regular_users(self):
        self._create_logged_in_user(username="shortcut_mapping_user")

        response = self.client.get("/shortcut/mapping")

        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn("Shortcut Session Mapping", page)
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
        self.assertIn("Failed days", page)
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
        self.assertIn("Detected date range: 30-12-2023 to 01-01-2024", page)

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
        self.assertIn("Vs Previous (12-01-26)", page)
        self.assertIn("Vs Best (12-01-26)", page)
        self.assertNotIn("No prior", page)
        self.assertNotIn("No baseline", page)

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
        self.assertEqual(row.get("performance_key"), "significantly_off")
        self.assertEqual(row.get("performance_label"), "↓ Significantly Off")


if __name__ == "__main__":
    unittest.main()
