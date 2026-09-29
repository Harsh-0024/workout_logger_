"""The tests never run against the real environment.

config.py loads a .env from any parent folder, so without this a test run could pick up real
database, mail and API credentials. Loaded by pytest before any test imports config.
"""
import os
import sys
import tempfile
import types

sys.modules["dotenv"] = types.SimpleNamespace(load_dotenv=lambda *args, **kwargs: False,
                                              find_dotenv=lambda *args, **kwargs: "")

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(prefix="workout-tests-"), "tests.db")
for _key in (
    "MAIL_USERNAME", "MAIL_PASSWORD", "MAIL_DEFAULT_SENDER",
    "BREVO_API_KEY", "BREVO_SENDER_EMAIL",
    "GEMINI_API_KEY",
    "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY",
    "ADMIN_PASSWORD", "ADMIN_EMAILS",
):
    os.environ[_key] = ""
