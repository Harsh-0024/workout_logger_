import importlib
import unittest
from unittest.mock import patch

from config import Config


class TestHealth(unittest.TestCase):
    def test_health_names_the_kind_of_database_error_only(self):
        with patch.object(Config, "SECRET_KEY", "test-secret"):
            app_module = importlib.import_module("app")
        app = app_module.app
        app.config["DB_INIT_LAST_ERROR"] = (
            'OperationalError: connection to server at "ep-secret.db.example" (10.1.2.3), port 5432 failed: '
            'password authentication failed for user "owner"'
        )
        try:
            body = app.test_client().get("/health").get_json()
        finally:
            app.config.pop("DB_INIT_LAST_ERROR", None)
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["db_init_last_error"], "OperationalError")
        self.assertNotIn("ep-secret", str(body))
        self.assertIsNone(app.test_client().get("/health").get_json()["db_init_last_error"])


if __name__ == "__main__":
    unittest.main()
