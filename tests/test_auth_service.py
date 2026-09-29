"""AuthService on a real (in-memory) database: sign-up, sign-in, one-time codes, passwords."""
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import bcrypt
from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker
from sqlalchemy.pool import StaticPool

from models import Base, EmailVerification, User
from services.auth import AuthenticationError, AuthService


class AuthServiceTestCase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine)
        self.session = scoped_session(self.factory)
        self._patchers = [
            patch("services.auth.Session", self.session),
            patch("services.auth.session_factory", self.factory),
        ]
        for p in self._patchers:
            p.start()

    def tearDown(self):
        for p in reversed(self._patchers):
            p.stop()
        self.session.remove()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _user(self, username="alice"):
        return self.session.query(User).filter_by(username=username).one()

    def _register(self, username="Alice", email="Alice@Example.com", password="secret123"):
        user, code = AuthService.register_user(username, email, password)
        return user.id, code


class TestRegistration(AuthServiceTestCase):
    def test_new_account_is_unverified_with_a_six_digit_code(self):
        user_id, code = self._register()
        user = self._user()
        self.assertEqual((user.id, user.username, user.email), (user_id, "alice", "alice@example.com"))
        self.assertFalse(user.is_verified)
        self.assertRegex(code, r"^\d{6}$")
        self.assertNotEqual(user.password_hash, "secret123")

    def test_taken_names_and_weak_passwords_are_refused(self):
        self._register()
        with self.assertRaisesRegex(AuthenticationError, "Username already taken"):
            AuthService.register_user("ALICE", "other@example.com", "secret123")
        with self.assertRaisesRegex(AuthenticationError, "Email already registered"):
            AuthService.register_user("bob", "alice@example.com", "secret123")
        for weak in ("short1", "lettersonly", "12345678"):
            with self.assertRaises(AuthenticationError, msg=weak):
                AuthService.register_user("carol", "carol@example.com", weak)
        self.assertEqual(self.session.query(User).count(), 1)


class TestSignIn(AuthServiceTestCase):
    def test_email_code_then_password_sign_in(self):
        user_id, code = self._register()
        with self.assertRaisesRegex(AuthenticationError, "verify your email"):
            AuthService.authenticate_user("alice", "secret123")

        self.assertFalse(AuthService.verify_email(user_id, "000000" if code != "000000" else "111111"))
        self.assertTrue(AuthService.verify_email(user_id, code))

        # Username or email, in any case.
        self.assertEqual(AuthService.authenticate_user("alice", "secret123").id, user_id)
        self.assertEqual(AuthService.authenticate_user("ALICE@example.com", "secret123").id, user_id)
        self.assertIsNone(AuthService.authenticate_user("alice", "wrong-pass1"))
        self.assertIsNone(AuthService.authenticate_user("nobody", "secret123"))

    def test_an_expired_email_code_is_refused(self):
        user_id, code = self._register()
        self.session.query(EmailVerification).update({"expires_at": datetime.now() - timedelta(minutes=1)})
        self.session.commit()
        with self.assertRaisesRegex(AuthenticationError, "expired"):
            AuthService.verify_email(user_id, code)


class TestOneTimeCodes(AuthServiceTestCase):
    def setUp(self):
        super().setUp()
        self.user_id, code = self._register()
        AuthService.verify_email(self.user_id, code)

    def test_login_code_works_once_and_only_for_login(self):
        with self.assertRaisesRegex(AuthenticationError, "Account not found"):
            AuthService.request_login_otp("nobody")
        payload = AuthService.request_login_otp("ALICE")
        code = payload["otp_code"]
        self.assertEqual((payload["id"], payload["email"]), (self.user_id, "alice@example.com"))

        self.assertFalse(AuthService.verify_otp(self.user_id, code, "change_password"))
        wrong = "123456" if code != "123456" else "654321"
        self.assertFalse(AuthService.verify_otp(self.user_id, wrong, "login_otp"))
        self.assertTrue(AuthService.verify_otp(self.user_id, code, "login_otp"))
        self.assertFalse(AuthService.verify_otp(self.user_id, code, "login_otp"))

    def test_a_new_code_replaces_the_old_one(self):
        first = AuthService.request_login_otp("alice")["otp_code"]
        second = AuthService.request_login_otp("alice")["otp_code"]
        if first != second:
            self.assertFalse(AuthService.verify_otp(self.user_id, first, "login_otp"))
        self.assertTrue(AuthService.verify_otp(self.user_id, second, "login_otp"))

    def test_an_expired_code_is_refused(self):
        code = AuthService.request_login_otp("alice")["otp_code"]
        self.session.query(EmailVerification).filter_by(purpose="login_otp").update(
            {"expires_at": datetime.now() - timedelta(seconds=1)}
        )
        self.session.commit()
        with self.assertRaisesRegex(AuthenticationError, "expired"):
            AuthService.verify_otp(self.user_id, code, "login_otp")


class TestPasswords(AuthServiceTestCase):
    def setUp(self):
        super().setUp()
        self.user_id, code = self._register()
        AuthService.verify_email(self.user_id, code)

    def test_change_password_needs_the_current_one_and_a_strong_new_one(self):
        with self.assertRaisesRegex(AuthenticationError, "Current password is incorrect"):
            AuthService.change_password(self.user_id, "wrong-pass1", "newsecret1")
        for weak in ("short1", "lettersonly", "12345678"):
            with self.assertRaises(AuthenticationError, msg=weak):
                AuthService.change_password(self.user_id, "secret123", weak)
        self.assertTrue(AuthService.change_password(self.user_id, "secret123", "newsecret1"))
        self.assertIsNone(AuthService.authenticate_user("alice", "secret123"))
        self.assertEqual(AuthService.authenticate_user("alice", "newsecret1").id, self.user_id)

    def test_set_password_follows_the_sign_up_rules(self):
        with self.assertRaises(AuthenticationError):
            AuthService.set_password(self.user_id, "lettersonly")
        self.assertTrue(AuthService.set_password(self.user_id, "another1pass"))
        self.assertEqual(AuthService.authenticate_user("alice", "another1pass").id, self.user_id)
        self.assertFalse(AuthService.set_password(999, "another1pass"))

    def test_long_passwords_work(self):
        # bcrypt 5 refuses more than 72 bytes; sign-up allows 128 characters.
        long_password = "a1" * 60
        self.assertTrue(AuthService.set_password(self.user_id, long_password))
        self.assertEqual(AuthService.authenticate_user("alice", long_password).id, self.user_id)
        accented = "éa1" * 25  # 75 characters, 100 bytes
        user, _ = AuthService.register_user("dora", "dora@example.com", accented)
        self.assertTrue(AuthService.verify_password(accented, user.password_hash))

    def test_accounts_hashed_by_older_bcrypt_still_sign_in(self):
        # Older bcrypt hashed only the first 72 bytes of a long password, without complaint.
        long_password = "b2" * 50
        old_hash = bcrypt.hashpw(long_password.encode()[:72], bcrypt.gensalt()).decode()
        self.assertTrue(AuthService.verify_password(long_password, old_hash))
        self.assertFalse(AuthService.verify_password("b2" * 35, old_hash))


if __name__ == "__main__":
    unittest.main()
