import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base, User, UserRole, WorkoutLog
from services.logging import handle_workout_log, set_timed_exercise_preference


class TestTimedPreferencePrompt(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        SessionLocal = sessionmaker(bind=self.engine)
        self.db = SessionLocal()
        self.user = User(
            username="timed_user",
            role=UserRole.USER,
            is_verified=True,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        self.db.add(self.user)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _parsed_payload(self, workout_date):
        return {
            "date": workout_date,
            "workout_name": "Timed Fallback Check",
            "exercises": [
                {
                    "name": "Plank",
                    "weights": [1.0],
                    "reps": [60],
                    "exercise_string": "Plank\n1, 60",
                    "valid": True,
                },
                {
                    "name": "Dead Hang",
                    "weights": [1.0],
                    "reps": [45],
                    "exercise_string": "Dead Hang\n1, 45",
                    "valid": True,
                },
            ],
        }

    def test_plank_and_dead_hang_prompt_first_then_obey_user_preference(self):
        first_day = datetime.now().replace(microsecond=0)
        first_summary = handle_workout_log(self.db, self.user, self._parsed_payload(first_day))
        self.db.commit()

        first_rows = {row["name"]: row for row in first_summary}
        self.assertIn("Plank", first_rows)
        self.assertIn("Dead Hang", first_rows)
        self.assertTrue(first_rows["Plank"]["timed_prompt_needed"])
        self.assertTrue(first_rows["Dead Hang"]["timed_prompt_needed"])
        self.assertTrue(first_rows["Plank"]["is_timed"])
        self.assertTrue(first_rows["Dead Hang"]["is_timed"])

        set_timed_exercise_preference(self.db, self.user.id, "Plank", False)
        set_timed_exercise_preference(self.db, self.user.id, "Dead Hang", True)
        self.db.commit()

        second_day = first_day + timedelta(days=1)
        second_summary = handle_workout_log(self.db, self.user, self._parsed_payload(second_day))
        self.db.commit()

        second_rows = {row["name"]: row for row in second_summary}
        self.assertFalse(second_rows["Plank"]["timed_prompt_needed"])
        self.assertFalse(second_rows["Dead Hang"]["timed_prompt_needed"])
        self.assertFalse(second_rows["Plank"]["is_timed"])
        self.assertTrue(second_rows["Dead Hang"]["is_timed"])

    def test_parsed_bodyweight_updates_user_and_saved_logs(self):
        payload = {
            "date": datetime.now().replace(microsecond=0),
            "workout_name": "Bodyweight Update",
            "bodyweight": 72.5,
            "bodyweight_unit": "kg",
            "exercises": [
                {
                    "name": "Dips",
                    "weights": [32.5],
                    "reps": [8],
                    "exercise_string": "Dips - [8-12]\nBw-40, 8",
                    "valid": True,
                },
            ],
        }

        handle_workout_log(self.db, self.user, payload)
        self.db.commit()

        log = self.db.query(WorkoutLog).filter_by(user_id=self.user.id, exercise="Dips").one()
        self.assertEqual(self.user.bodyweight, 72.5)
        self.assertEqual(log.bodyweight, 72.5)


if __name__ == "__main__":
    unittest.main()
