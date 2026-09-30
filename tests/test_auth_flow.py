"""Signing up, confirming the email, signing in and out, and signing in with a code, through the pages.

Email is never sent: EmailService.send_otp_email is replaced and the codes are read from its calls.
"""
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker
from sqlalchemy.pool import StaticPool

from config import Config
from models import Base, User
from workout_tracker import create_app


class _FlowConfig(Config):
    TESTING = True
    SECRET_KEY = "test-secret-key"
    ENABLE_CSRF = False
    WTF_CSRF_ENABLED = False
    ENABLE_RATE_LIMITING = False


class TestAuthFlow(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        factory = sessionmaker(bind=self.engine)
        self.session = scoped_session(factory)
        self.sent = []

        def fake_send(_service, email, username, otp_code, purpose):
            self.sent.append({"email": email, "code": otp_code, "purpose": purpose})
            return True

        self._patchers = [
            patch("models.Session", self.session),
            patch("workout_tracker.Session", self.session),
            patch("workout_tracker.routes.auth.Session", self.session),
            patch("workout_tracker.routes.workouts.Session", self.session),
            patch("workout_tracker.routes.stats.Session", self.session),
            patch("workout_tracker.routes.plans.Session", self.session),
            patch("services.auth.Session", self.session),
            patch("services.auth.session_factory", factory),
            patch("services.email_service.EmailService.send_otp_email", autospec=True, side_effect=fake_send),
        ]
        for p in self._patchers:
            p.start()
        self.app = create_app(config_object=_FlowConfig, init_db=False)
        self.client = self.app.test_client()

    def tearDown(self):
        for p in reversed(self._patchers):
            p.stop()
        self.session.remove()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _path(self, response):
        return urlsplit(response.headers.get("Location", "")).path

    def _sign_up(self, username="alice", email="alice@example.com", password="secret123"):
        response = self.client.post("/register", data={"username": username, "email": email, "password": password})
        self.assertEqual(self._path(response), "/verify-email")
        code = self.sent[-1]
        self.assertEqual((code["email"], code["purpose"]), (email, "verify_email"))
        response = self.client.post("/verify-email", data={"verification_code": code["code"]})
        self.assertEqual(self._path(response), f"/{username}")
        return response

    def test_sign_up_confirm_sign_out_and_back_in(self):
        self._sign_up()
        self.assertEqual(self.client.get("/log").status_code, 200)
        self.assertTrue(self.session.query(User).filter_by(username="alice").one().is_verified)

        self.assertEqual(self._path(self.client.get("/logout")), "/login")
        self.assertEqual(self._path(self.client.get("/log")), "/login")

        page = self.client.post("/login", data={"username_or_email": "alice", "password": "wrong-pass1"})
        self.assertEqual(page.status_code, 200)
        self.assertIn("Invalid username/email or password.", page.get_data(as_text=True))

        # Signed in by email, sent on to where they were going, but only within the site.
        response = self.client.post("/login?next=/stats", data={"username_or_email": "Alice@Example.com", "password": "secret123"})
        self.assertEqual(self._path(response), "/stats")
        self.client.get("/logout")
        response = self.client.post("/login?next=//evil.com/x", data={"username_or_email": "alice", "password": "secret123"})
        self.assertEqual(self._path(response), "/alice")

    def test_a_wrong_email_code_does_not_confirm_the_account(self):
        self.client.post("/register", data={"username": "bob", "email": "bob@example.com", "password": "secret123"})
        right = self.sent[-1]["code"]
        wrong = "000000" if right != "000000" else "111111"
        page = self.client.post("/verify-email", data={"verification_code": wrong}).get_data(as_text=True)
        self.assertFalse(self.session.query(User).filter_by(username="bob").one().is_verified)
        self.assertEqual(self._path(self.client.get("/log")), "/login")
        self.assertNotIn(right, page)

    def test_sign_up_problems_are_shown_on_the_form(self):
        self._sign_up()
        self.client.get("/logout")
        for data, message in (
            ({"username": "alice", "email": "new@example.com", "password": "secret123"}, "Username already taken"),
            ({"username": "carol", "email": "alice@example.com", "password": "secret123"}, "Email already registered"),
            ({"username": "carol", "email": "carol@example.com", "password": "lettersonly"}, "Password must contain at least one number"),
            ({"username": "carol", "email": "carol@example.com", "password": "secret123", "confirm_password": "secret124"},
             "Passwords don&#39;t match."),
        ):
            page = self.client.post("/register", data=data).get_data(as_text=True)
            self.assertIn(message, page)
            self.assertNotIn("Registration failed", page)
        self.assertEqual(self.session.query(User).count(), 1)

    def test_usernames_that_are_page_addresses_are_not_available(self):
        # Home is /<username>: someone called "log" or "stats" never reached theirs.
        for name in ("log", "stats", "Settings", "workouts", "sw.js", "admin"):
            page = self.client.post("/register", data={"username": name, "email": f"{name}@example.com",
                                                        "password": "secret123"}).get_data(as_text=True)
            self.assertIn("That username isn&#39;t available", page, name)
        self.assertEqual(self.session.query(User).count(), 0)

        self._sign_up()
        response = self.client.post("/settings", data={
            "form_type": "profile", "full_name": "", "username": "stats",
            "email": "alice@example.com", "current_password": "secret123",
        }, follow_redirects=True)
        self.assertIn("That username isn&#39;t available", response.get_data(as_text=True))
        self.session.expire_all()
        self.assertEqual(self.session.query(User).one().username, "alice")

    def test_sign_in_with_a_code(self):
        self._sign_up()
        self.client.get("/logout")

        page = self.client.post("/login/otp", data={"username_or_email": "nobody"}).get_data(as_text=True)
        self.assertIn("Account not found", page)

        response = self.client.post("/login/otp", data={"username_or_email": "alice"})
        self.assertEqual(self._path(response), "/login/otp/verify")
        code = self.sent[-1]
        self.assertEqual(code["purpose"], "login_otp")

        wrong = "000000" if code["code"] != "000000" else "111111"
        page = self.client.post("/login/otp/verify", data={"otp_code": wrong}).get_data(as_text=True)
        self.assertIn("Invalid code", page)
        self.assertEqual(self._path(self.client.get("/log")), "/login")

        response = self.client.post("/login/otp/verify", data={"otp_code": code["code"]})
        self.assertEqual(self._path(response), "/settings/account")
        self.assertEqual(self.client.get("/log").status_code, 200)

        # The code is spent.
        self.client.get("/logout")
        self.client.post("/login/otp", data={"username_or_email": "alice"})
        response = self.client.post("/login/otp/verify", data={"otp_code": code["code"]})
        if self.sent[-1]["code"] != code["code"]:
            self.assertNotEqual(self._path(response), "/settings/account")

    def test_a_mistyped_new_email_is_caught_before_sending_codes(self):
        self._sign_up()
        sent_before = len(self.sent)
        page = self.client.post("/settings", data={
            "form_type": "profile", "full_name": "", "username": "alice",
            "email": "alice@@example", "current_password": "secret123",
        }, follow_redirects=True).get_data(as_text=True)
        self.assertIn("Invalid email format", page)
        self.assertEqual(len(self.sent), sent_before)

    def test_changing_email_needs_both_codes_and_a_typo_spends_neither(self):
        self._sign_up()
        response = self.client.post("/settings", data={
            "form_type": "profile", "full_name": "", "username": "alice",
            "email": "alice.new@example.com", "current_password": "secret123",
        })
        self.assertEqual(self._path(response), "/settings/email/verify-otp")
        codes = {c["purpose"]: c["code"] for c in self.sent[-2:]}
        old, new = codes["change_email_old"], codes["change_email_new"]
        wrong = "000000" if new != "000000" else "111111"

        page = self.client.post("/settings/email/verify-otp", data={"otp_code_old": old, "otp_code_new": wrong},
                                follow_redirects=True).get_data(as_text=True)
        self.assertIn("Invalid codes", page)
        self.session.expire_all()
        self.assertEqual(self.session.query(User).filter_by(username="alice").one().email, "alice@example.com")

        # The right pair still works: the typo didn't use up the old address's code.
        response = self.client.post("/settings/email/verify-otp", data={"otp_code_old": old, "otp_code_new": new})
        self.assertEqual(self._path(response), "/settings")
        self.session.expire_all()
        self.assertEqual(self.session.query(User).filter_by(username="alice").one().email, "alice.new@example.com")


if __name__ == "__main__":
    unittest.main()
