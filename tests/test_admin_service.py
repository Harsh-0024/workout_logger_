"""AdminService on a real (in-memory) database: the parts that delete or merge people's data."""
import unittest
from datetime import datetime
from unittest.mock import patch

from sqlalchemy import create_engine, event
from sqlalchemy.orm import scoped_session, sessionmaker
from sqlalchemy.pool import StaticPool

from models import Base, Lift, Plan, RepRange, TimedExercisePreference, User, UserRole, WorkoutLog
from services.admin import AdminError, AdminService


class AdminServiceTestCase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

        @event.listens_for(self.engine, "connect")
        def _foreign_keys(dbapi_connection, _record):
            # As in production (Postgres), rows that belong to a user go with them.
            dbapi_connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(self.engine)
        self.session = scoped_session(sessionmaker(bind=self.engine))
        self._patcher = patch("services.admin.Session", self.session)
        self._patcher.start()
        self.admin = self._add_user("boss", "boss@example.com", role=UserRole.ADMIN)

    def tearDown(self):
        self._patcher.stop()
        self.session.remove()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _add_user(self, username, email, role=UserRole.USER, updated_at=None):
        user = User(username=username, email=email, role=role, is_verified=True,
                    created_at=datetime(2026, 1, 1), updated_at=updated_at or datetime(2026, 1, 1))
        self.session.add(user)
        self.session.commit()
        return user.id

    def _add_log(self, user_id, day, exercise="Squat", weight=100.0):
        log = WorkoutLog(user_id=user_id, date=datetime(2026, 9, day, 18), workout_name="Legs", exercise=exercise,
                         top_weight=weight, top_reps=5, estimated_1rm=weight * 1.17)
        self.session.add(log)
        self.session.commit()
        return log.id

    def _count(self, model, user_id):
        return self.session.query(model).filter_by(user_id=user_id).count()


class TestDeleteUser(AdminServiceTestCase):
    def test_deletes_the_person_and_everything_that_is_theirs_only(self):
        victim = self._add_user("gone", "gone@example.com")
        keeper = self._add_user("stays", "stays@example.com")
        best = self._add_log(victim, 1)
        self._add_log(keeper, 1)
        self.session.add_all([
            Lift(user_id=victim, exercise="Squat", best_log_id=best),
            Plan(user_id=victim, text_content="Day 1"),
            RepRange(user_id=victim, text_content="Squat: 5-8"),
            TimedExercisePreference(user_id=victim, exercise_key="plank", is_timed=True),
        ])
        self.session.commit()

        info = AdminService.delete_user(self.admin, victim, "spam")
        self.assertEqual((info["username"], info["email"], info["deletion_reason"]), ("gone", "gone@example.com", "spam"))
        self.session.expire_all()
        self.assertIsNone(self.session.get(User, victim))
        for model in (WorkoutLog, Lift, Plan, RepRange, TimedExercisePreference):
            self.assertEqual(self._count(model, victim), 0, model.__name__)
        self.assertEqual(self._count(WorkoutLog, keeper), 1)

    def test_only_an_admin_can_delete_and_never_an_admin(self):
        user = self._add_user("plain", "plain@example.com")
        other_admin = self._add_user("boss2", "boss2@example.com", role=UserRole.ADMIN)
        with self.assertRaisesRegex(AdminError, "Unauthorized"):
            AdminService.delete_user(user, other_admin, "no")
        with self.assertRaisesRegex(AdminError, "own account"):
            AdminService.delete_user(self.admin, self.admin, "no")
        with self.assertRaisesRegex(AdminError, "other admin"):
            AdminService.delete_user(self.admin, other_admin, "no")
        with self.assertRaisesRegex(AdminError, "not found"):
            AdminService.delete_user(self.admin, 9999, "no")
        self.assertEqual(self.session.query(User).count(), 3)


class TestCleanupDuplicates(AdminServiceTestCase):
    def test_merges_accounts_sharing_an_email_into_the_one_with_workouts(self):
        empty = self._add_user("alice_new", "Alice@Example.com", updated_at=datetime(2026, 9, 1))
        main = self._add_user("alice", "alice@example.com", updated_at=datetime(2026, 1, 1))
        self._add_log(main, 1)
        self._add_log(main, 3)
        self.session.add(Plan(user_id=empty, text_content="Only plan"))
        self.session.commit()

        summary = AdminService.cleanup_duplicate_users(self.admin)
        self.assertEqual(summary, {"groups": 1, "kept": 1, "deleted": 1, "logs_moved": 0})
        self.session.expire_all()
        self.assertIsNone(self.session.get(User, empty))
        self.assertEqual(self._count(WorkoutLog, main), 2)
        # The kept account had no plan, so it takes the other one's.
        self.assertEqual(self.session.query(Plan).filter_by(user_id=main).one().text_content, "Only plan")

    def test_workouts_on_the_dropped_account_are_moved_not_lost(self):
        a = self._add_user("bob", "bob@example.com")
        b = self._add_user("bob2", "BOB@example.com")
        self._add_log(a, 1)
        self._add_log(a, 2)
        self._add_log(b, 5, weight=120.0)

        summary = AdminService.cleanup_duplicate_users(self.admin)
        self.assertEqual((summary["deleted"], summary["logs_moved"]), (1, 1))
        self.session.expire_all()
        self.assertEqual(self.session.query(WorkoutLog).count(), 3)
        self.assertEqual(self._count(WorkoutLog, a), 3)

    def test_nothing_happens_without_duplicates_or_for_non_admins(self):
        self._add_user("carol", "carol@example.com")
        self.assertEqual(AdminService.cleanup_duplicate_users(self.admin)["groups"], 0)
        plain = self._add_user("dave", "dave@example.com")
        with self.assertRaisesRegex(AdminError, "Unauthorized"):
            AdminService.cleanup_duplicate_users(plain)


class TestPromote(AdminServiceTestCase):
    def test_promote(self):
        user = self._add_user("erin", "erin@example.com")
        self.assertTrue(AdminService.promote_to_admin(self.admin, user))
        self.session.expire_all()
        self.assertTrue(self.session.get(User, user).is_admin())
        with self.assertRaisesRegex(AdminError, "already an admin"):
            AdminService.promote_to_admin(self.admin, user)
        plain = self._add_user("frank", "frank@example.com")
        with self.assertRaisesRegex(AdminError, "Unauthorized"):
            AdminService.promote_to_admin(plain, plain)


if __name__ == "__main__":
    unittest.main()
