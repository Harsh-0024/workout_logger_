"""The admin AI coach: its data tools on a real database, and the agent loop with Gemini faked."""
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker
from sqlalchemy.pool import StaticPool

from config import Config
from models import Base, RepRange, User, UserRole, WorkoutLog
from services import ai_coach, coach_tools
from utils.dates import utc_now
from workout_tracker import create_app


def _log(user, day, exercise, weight, reps, e1rm, name="Workout", sets=3):
    return WorkoutLog(
        user_id=user.id, date=day, workout_name=name, exercise=exercise,
        sets_json={"weights": [weight] * sets, "reps": [reps] * sets},
        top_weight=weight, top_reps=reps, estimated_1rm=e1rm,
    )


class _CoachDb(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.db = scoped_session(sessionmaker(bind=self.engine))
        self.user = User(username="coachee", full_name="Sam Lifter", role=UserRole.ADMIN, is_verified=True)
        self.other = User(username="someone", role=UserRole.USER, is_verified=True)
        self.db.add_all([self.user, self.other])
        self.db.commit()
        # The tools count days in UTC; a local "today" breaks these tests after local midnight.
        self.today = utc_now().replace(hour=12, minute=0, second=0, microsecond=0)
        days = lambda n: self.today - timedelta(days=n)
        rows = [
            _log(self.user, days(1), "Back Squat", 120, 5, 140, "Legs"),
            _log(self.user, days(1), "Leg Press", 250, 10, 330, "Legs"),
            _log(self.user, days(3), "Flat Barbell Press", 85, 5, 99, "Push"),
            _log(self.user, days(8), "Back Squat", 115, 5, 134, "Legs"),
            _log(self.user, days(15), "Back Squat", 110, 5, 128, "Legs"),
            _log(self.user, days(40), "Back Squat", 100, 5, 117, "Legs"),
            # Bench stuck for months: best was long ago.
            _log(self.user, days(120), "Flat Barbell Press", 90, 5, 105, "Push"),
            _log(self.user, days(60), "Flat Barbell Press", 85, 5, 99, "Push"),
            _log(self.user, days(30), "Flat Barbell Press", 85, 5, 99, "Push"),
            # Another user's heavier squat must never show up.
            _log(self.other, days(2), "Back Squat", 200, 5, 233, "Legs"),
        ]
        self.db.add_all(rows)
        self.db.commit()
        self.user_id, self.other_id = self.user.id, self.other.id
        ai_coach._rate_hits.clear()
        ai_coach._bad_models.clear()

    def tearDown(self):
        self.db.remove()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def iso(self, n):
        return (self.today - timedelta(days=n)).date().isoformat()


class TestCoachTools(_CoachDb):
    def test_exercise_names_resolve_by_word_order_and_spelling(self):
        self.assertEqual(coach_tools.resolve_exercise(self.db, self.user, "squat back"), ["Back Squat"])
        self.assertEqual(coach_tools.resolve_exercise(self.db, self.user, "bak squat"), ["Back Squat"])
        with self.assertRaises(coach_tools.ToolError):
            coach_tools.resolve_exercise(self.db, self.user, "zercher carry")
        with self.assertRaisesRegex(coach_tools.ToolError, "Flat Barbell Press"):
            coach_tools.resolve_exercise(self.db, self.user, "bench press")

    def test_history_and_records_are_only_this_users(self):
        history = coach_tools.get_exercise_history(self.db, self.user, "Back Squat", limit=2)
        self.assertEqual(history["total_sessions"], 4)
        self.assertEqual([s["date"] for s in history["sessions_newest_first"]], [self.iso(1), self.iso(8)])
        self.assertEqual(history["sessions_newest_first"][0]["sets"], "120 x 5, 120 x 5, 120 x 5")

        pr = coach_tools.get_personal_records(self.db, self.user, "back squat")
        self.assertEqual(pr["best_estimated_1rm"]["e1rm_kg"], 140)
        ranked = coach_tools.get_personal_records(self.db, self.user)["records_by_estimated_1rm"]
        self.assertEqual(ranked[0]["exercise"], "Leg Press")
        self.assertNotIn(233, [r["e1rm_kg"] for r in ranked])

    def test_timed_holds_are_not_ranked_as_lifts(self):
        # 20 kg x 90 s scores like a huge "e1RM"; it is a hold, not a lift.
        self.db.add(_log(self.user, self.today - timedelta(days=2), "Plank", 20, 90, 190, "Core"))
        self.db.commit()
        ranked = coach_tools.get_personal_records(self.db, self.user)["records_by_estimated_1rm"]
        self.assertNotIn("Plank", [r["exercise"] for r in ranked])

        history = coach_tools.get_exercise_history(self.db, self.user, "plank")
        self.assertTrue(history["timed_hold"])
        self.assertEqual(history["sessions_newest_first"][0]["top_seconds"], 90)
        self.assertNotIn("e1rm_kg", history["sessions_newest_first"][0])

    def test_long_sets_dont_rank_as_strength_records(self):
        # 60 kg x 20 "estimates" 100 kg, more than the real 5-rep bench; it mustn't outrank it.
        self.db.add(_log(self.user, self.today - timedelta(days=2), "Cable Row", 60, 20, 100, "Pull"))
        self.db.commit()
        ranked = coach_tools.get_personal_records(self.db, self.user)["records_by_estimated_1rm"]
        self.assertNotIn("Cable Row", [r["exercise"] for r in ranked])
        row = coach_tools.get_exercise_history(self.db, self.user, "cable row")["sessions_newest_first"][0]
        self.assertTrue(row["e1rm_unreliable"])

    def test_trend_spots_progress_and_a_stall(self):
        squat = coach_tools.get_progress_trend(self.db, self.user, "Back Squat", days=90)
        self.assertGreater(squat["trend_kg_per_week"], 0)
        self.assertEqual(squat["all_time_best"]["date"], self.iso(1))
        self.assertFalse(squat["looks_stalled"])

        bench = coach_tools.get_progress_trend(self.db, self.user, "Flat Barbell Press", days=180)
        self.assertEqual(bench["all_time_best"]["date"], self.iso(120))
        self.assertTrue(bench["looks_stalled"])

    def test_summary_workouts_and_comparison(self):
        summary = coach_tools.get_training_summary(self.db, self.user, days=28)
        self.assertEqual(summary["workout_days"], 4)
        self.assertEqual(summary["days_since_last_workout"], 1)
        self.assertIn("Legs", summary["weekly_sets_by_muscle_group"])
        self.assertIn("Back", summary["muscle_groups_not_trained"])

        workouts = coach_tools.get_workouts(self.db, self.user, start_date=self.iso(1), end_date=self.iso(1))
        day = workouts["workouts_newest_first"]
        self.assertEqual(len(day), 1)
        self.assertEqual(day[0]["title"], "Legs")
        self.assertEqual({e["exercise"] for e in day[0]["exercises"]}, {"Back Squat", "Leg Press"})

        cmp = coach_tools.compare_periods(self.db, self.user, period_days=14, exercise="back squat")
        self.assertEqual(cmp["current"]["workout_days"], 2)
        self.assertEqual(cmp["previous"]["workout_days"], 1)

    def test_next_session_uses_double_progression(self):
        self.user.follow_admin_exercises = False
        self.db.add(RepRange(user_id=self.user.id, text_content="Back Squat: 3, 3-5\nFlat Barbell Press: 3, 6-8"))
        self.db.commit()
        days = lambda n: self.today - timedelta(days=n)

        # Squat: 120 x 5 on every set = top of 3-5 -> add weight, restart at 3.
        squat = coach_tools.suggest_next_session(self.db, self.user, "back squat")
        self.assertEqual(squat["target"], {"weight_kg": 122.5, "reps": [3, 3, 3]})
        self.assertEqual(squat["rep_range"]["source"], "your rep ranges")

        # Bench: 85 x 5 twice in a row, under 6 -> back off ~10%.
        bench = coach_tools.suggest_next_session(self.db, self.user, "flat barbell press")
        self.assertEqual(bench["target"]["weight_kg"], 77.5)

        # Inside the range: same weight, one more rep where there's room.
        self.db.add(WorkoutLog(user_id=self.user.id, date=days(0), exercise="Flat Barbell Press",
                               sets_json={"weights": [80, 80, 80], "reps": [8, 7, 6]}, top_weight=80, top_reps=8,
                               estimated_1rm=99))
        self.db.commit()
        bench = coach_tools.suggest_next_session(self.db, self.user, "flat barbell press")
        self.assertEqual(bench["target"], {"weight_kg": 80.0, "reps": [8, 8, 7]})

        # No range set -> the default, said so.
        press = coach_tools.suggest_next_session(self.db, self.user, "leg press")
        self.assertIn("default", press["rep_range"]["source"])
        self.assertEqual(press["target"], {"weight_kg": 252.5, "reps": [6, 6, 6]})

    def test_run_tool_reports_problems_to_the_model(self):
        self.assertIn("error", coach_tools.run_tool(self.db, self.user, "drop_tables", {}))
        self.assertIn("error", coach_tools.run_tool(self.db, self.user, "get_workouts", {"start_date": "yesterday"}))
        # Unknown arguments are dropped rather than crashing the tool.
        out = coach_tools.run_tool(self.db, self.user, "list_exercises", {"query": "squat", "user_id": self.other.id})
        self.assertEqual([e["exercise"] for e in out["exercises"]], ["Back Squat"])
        names = {d["name"] for d in coach_tools.function_declarations()}
        self.assertEqual(names, set(coach_tools.TOOLS))


def _reply(*parts):
    return {"candidates": [{"content": {"role": "model", "parts": list(parts)}, "finishReason": "STOP"}]}


def _http_error(status):
    resp = requests.Response()
    resp.status_code = status
    return requests.HTTPError(response=resp)


class TestCoachLoop(_CoachDb):
    def ask(self, text, **kwargs):
        kwargs.setdefault("api_keys", [{"id": 7, "api_key": "k"}])
        return ai_coach.run_coach(self.db, self.user, messages=[{"role": "user", "text": text}], **kwargs)

    def test_model_calls_tools_then_answers(self):
        sent = []

        def fake_post(api_key, model, payload):
            sent.append(payload)
            if len(sent) == 1:
                return _reply({"functionCall": {"name": "get_progress_trend", "args": {"exercise": "squat back"}, "id": "call_1"},
                               "thoughtSignature": "sig"})
            return _reply({"text": f"Your squat is up. Best **140 kg** on {self.iso(1)}."})

        with patch.object(ai_coach, "_post", side_effect=fake_post):
            out = self.ask("How's my squat?", context={"page": "stats"})

        self.assertTrue(out["ok"])
        self.assertEqual(out["source"], "ai")
        self.assertEqual(out["key_id"], 7)
        self.assertEqual([s["tool"] for s in out["steps"]], ["get_progress_trend"])
        self.assertEqual(out["links"], [{"label": self.iso(1), "url": f"/workout/{self.iso(1)}"}])
        second = sent[1]["contents"]
        # The model's own turn goes back untouched (thought signature included), then the tool result.
        self.assertEqual(second[1]["parts"][0]["thoughtSignature"], "sig")
        result = second[2]["parts"][0]["functionResponse"]
        self.assertEqual(result["name"], "get_progress_trend")
        self.assertEqual(result["id"], "call_1")
        self.assertEqual(result["response"]["exercise"], "Back Squat")
        self.assertIn("Sam", sent[0]["systemInstruction"]["parts"][0]["text"])
        self.assertIn("page 'stats'", sent[0]["systemInstruction"]["parts"][0]["text"])

    def test_retired_model_is_skipped_for_the_next(self):
        models = []

        def fake_post(api_key, model, payload):
            models.append(model)
            if model == ai_coach.MODEL_CANDIDATES[0]:
                raise _http_error(404)
            return _reply({"text": "Fine."})

        with patch.object(ai_coach, "_post", side_effect=fake_post):
            self.assertTrue(self.ask("hi")["ok"])
            self.assertTrue(self.ask("hi again")["ok"])
        self.assertEqual(models, [ai_coach.MODEL_CANDIDATES[0], ai_coach.MODEL_CANDIDATES[1], ai_coach.MODEL_CANDIDATES[1]])

    def test_rate_limit_mid_answer_moves_to_another_model(self):
        first, second = ai_coach.MODEL_CANDIDATES[:2]
        models = []

        def fake_post(api_key, model, payload):
            models.append(model)
            if len(models) == 1:
                return _reply({"functionCall": {"name": "list_exercises", "args": {}}})
            if model == first:
                raise _http_error(429)
            return _reply({"text": "You log squats most."})

        with patch.object(ai_coach, "_post", side_effect=fake_post):
            out = self.ask("What do I train most?")
        self.assertEqual(out["source"], "ai")
        self.assertEqual(out["model"], second)
        self.assertEqual(models, [first, first, second])

    def test_stray_fragments_are_dropped_or_asked_again(self):
        self.assertEqual(ai_coach._answer_text([{"text": "oon\n"}, {"text": "Your squat is up."}]), "Your squat is up.")
        self.assertEqual(ai_coach._answer_text([{"text": "Your squat is "}, {"text": "up."}]), "Your squat is up.")
        self.assertEqual(ai_coach._answer_text([{"text": "oz"}]), "")
        self.assertEqual(ai_coach._answer_text([{"text": "Fine."}]), "Fine.")
        replies = iter([_reply({"text": "0"}), _reply({"text": "Squats, mostly."})])
        with patch.object(ai_coach, "_post", side_effect=lambda *a: next(replies)):
            self.assertEqual(self.ask("What do I train most?")["answer"], "Squats, mostly.")

    def test_each_question_logs_its_cost(self):
        first = ai_coach.MODEL_CANDIDATES[0]

        def fake_post(api_key, model, payload):
            if model == first:
                raise _http_error(429)
            return {**_reply({"text": "Fine."}), "usageMetadata": {"totalTokenCount": 321}}

        with patch.object(ai_coach, "_post", side_effect=fake_post), \
                self.assertLogs("workout_tracker", level="INFO") as logs:
            self.ask("hi")
        line = [l for l in logs.output if "AI coach user=" in l][-1]
        self.assertIn(f"outcome=ai:{ai_coach.MODEL_CANDIDATES[1]}", line)
        self.assertIn("calls=2 tokens=321", line)
        self.assertIn(f"failed={first}:429", line)

    def test_an_empty_answer_is_asked_again_on_another_model(self):
        models = []

        def fake_post(api_key, model, payload):
            models.append(model)
            if len(models) == 1:
                return _reply()
            return _reply({"text": "Squats, mostly."})

        with patch.object(ai_coach, "_post", side_effect=fake_post):
            out = self.ask("What do I train most?")
        self.assertEqual(out["answer"], "Squats, mostly.")
        self.assertEqual(models, list(ai_coach.MODEL_CANDIDATES[:2]))

    def test_falls_back_to_rules_when_gemini_is_down_or_missing(self):
        with patch.object(ai_coach, "_post", side_effect=_http_error(503)):
            out = self.ask("when did I last hit 120kg back squat")
        self.assertTrue(out["ok"])
        self.assertEqual(out["source"], "rules")
        self.assertIn(self.iso(1), out["answer"])

        with patch.object(ai_coach, "_post") as never:
            out = self.ask("when did I last hit 120kg back squat", api_keys=[])
            never.assert_not_called()
        self.assertEqual(out["source"], "rules")
        self.assertIn("No Gemini key", out["note"])

        out = self.ask("Write me a poem", api_keys=[])
        self.assertFalse(out["ok"])
        self.assertIn("No Gemini key", out["error"])

    def test_history_is_cleaned(self):
        contents = ai_coach._clean_history([
            {"role": "assistant", "text": "hello"},  # can't lead
            {"role": "user", "text": "a"}, {"role": "user", "text": "b"},
            {"role": "assistant", "text": "c"}, {"role": "user", "text": "x" * 5000}, "junk",
        ])
        self.assertEqual([c["role"] for c in contents], ["user", "model", "user"])
        self.assertEqual(contents[0]["parts"][0]["text"], "a\n\nb")
        self.assertEqual(len(contents[-1]["parts"][0]["text"]), ai_coach.MAX_MESSAGE_CHARS)

    def test_rate_limit(self):
        for i in range(ai_coach.RATE_LIMIT):
            self.assertTrue(ai_coach.allow_request(1, now=1000 + i))
        self.assertFalse(ai_coach.allow_request(1, now=1100))
        self.assertTrue(ai_coach.allow_request(1, now=1000 + ai_coach.RATE_WINDOW_S + 50))

    def test_suggestions_use_the_users_lifts(self):
        tips = ai_coach.suggestions(self.db, self.user)
        self.assertIn("How is my Back Squat progressing?", tips)


class _TestConfig(Config):
    TESTING = True
    SECRET_KEY = "test-secret-key"
    ENABLE_CSRF = False
    WTF_CSRF_ENABLED = False


class TestCoachRoutes(_CoachDb):
    def setUp(self):
        super().setUp()
        self._patchers = [
            patch("models.Session", self.db),
            patch("workout_tracker.Session", self.db),
            patch("workout_tracker.routes.auth.Session", self.db),
            patch("workout_tracker.routes.workouts.Session", self.db),
            patch("workout_tracker.routes.stats.Session", self.db),
            patch("workout_tracker.routes.plans.Session", self.db),
        ]
        for p in self._patchers:
            p.start()
        self.client = create_app(config_object=_TestConfig, init_db=False).test_client()

    def tearDown(self):
        for p in self._patchers:
            p.stop()
        super().tearDown()

    def login(self, user_id):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(user_id)

    def test_only_admins_can_use_the_coach(self):
        self.login(self.other_id)
        self.assertEqual(self.client.post("/api/ai-coach", json={"messages": []}).status_code, 403)
        self.assertEqual(self.client.get("/api/ai-coach/suggestions").status_code, 403)
        page = self.client.get("/", follow_redirects=True).get_data(as_text=True)
        self.assertNotIn("aiAssistantModal", page)

    def test_admin_chat_round_trip(self):
        self.login(self.user_id)
        with patch.object(ai_coach, "_post", return_value=_reply({"text": "Train legs."})):
            res = self.client.post("/api/ai-coach", json={
                "messages": [{"role": "user", "text": "What now?"}],
                "context": {"page": "/", "exercise": "Back Squat", "evil": "x"},
            })
        # No key configured anywhere → the rules engine, which can't answer this one.
        self.assertEqual(res.status_code, 400)
        self.assertIn("No Gemini key", res.get_json()["error"])

        with patch.object(ai_coach, "_keys", return_value=[{"id": None, "api_key": "k", "source": "env"}]), \
                patch.object(ai_coach, "_post", return_value=_reply({"text": "Train legs."})):
            res = self.client.post("/api/ai-coach", json={"messages": [{"role": "user", "text": "What now?"}]})
        body = res.get_json()
        self.assertEqual(res.status_code, 200)
        self.assertEqual(body["answer"], "Train legs.")
        self.assertNotIn("key_id", body)

        tips = self.client.get("/api/ai-coach/suggestions").get_json()
        self.assertTrue(tips["ok"])
        self.assertFalse(tips["ai_enabled"])
        self.assertIn("aiAssistantModal", self.client.get("/", follow_redirects=True).get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
