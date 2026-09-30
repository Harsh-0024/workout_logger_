"""The file-based email queue: account-deletion emails go through it, with retries."""
import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from services.email_queue import EmailQueue


class TestEmailQueue(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="email-queue-")
        self.queue = EmailQueue(queue_dir=self.dir, max_retries=3)

    def _files(self):
        return sorted(os.listdir(self.dir))

    def _data(self, email_id):
        with open(os.path.join(self.dir, f"{email_id}.json")) as f:
            return json.load(f)

    def _make_due(self, email_id):
        data = self._data(email_id)
        data["next_retry"] = datetime.now().isoformat()
        with open(os.path.join(self.dir, f"{email_id}.json"), "w") as f:
            json.dump(data, f)

    def _deletion(self):
        return self.queue.enqueue("account_deletion", email="a@example.com", username="alice",
                                  admin_message="Spam account", admin_username="admin")

    def test_a_sent_email_leaves_the_queue(self):
        email_id = self._deletion()
        service = MagicMock()
        service.send_account_deletion_email.return_value = True
        self.queue.process_queue(service)
        service.send_account_deletion_email.assert_called_once_with(
            email="a@example.com", username="alice", admin_message="Spam account", admin_username="admin")
        self.assertEqual(self._files(), [])
        self.assertNotIn(email_id, [e["id"] for e in self.queue.get_pending_emails()])

    def test_a_failed_email_waits_longer_each_time_then_gives_up(self):
        email_id = self._deletion()
        service = MagicMock()
        service.send_account_deletion_email.return_value = False

        self.queue.process_queue(service)
        first = self._data(email_id)
        self.assertEqual(first["retry_count"], 1)
        wait = datetime.fromisoformat(first["next_retry"]) - datetime.now()
        self.assertGreater(wait, timedelta(minutes=9))  # 300 s x 2
        # Not due yet: nothing is tried.
        self.queue.process_queue(service)
        self.assertEqual(service.send_account_deletion_email.call_count, 1)

        # Due again: the second failure waits longer, the third gives up and drops it.
        self._make_due(email_id)
        self.queue.process_queue(service)
        second = self._data(email_id)
        self.assertEqual(second["retry_count"], 2)
        self.assertGreater(datetime.fromisoformat(second["next_retry"]) - datetime.now(), timedelta(minutes=19))
        self._make_due(email_id)
        self.queue.process_queue(service)
        self.assertEqual(self._files(), [])
        self.assertEqual(service.send_account_deletion_email.call_count, 3)

    def test_an_error_while_sending_counts_as_a_failure(self):
        email_id = self._deletion()
        service = MagicMock()
        service.send_account_deletion_email.side_effect = RuntimeError("mail server down")
        self.queue.process_queue(service)
        self.assertEqual(self._data(email_id)["retry_count"], 1)

    def test_unknown_types_and_broken_files_do_not_stop_the_rest(self):
        self.queue.enqueue("mystery", email="x@example.com")
        with open(os.path.join(self.dir, "broken.json"), "w") as f:
            f.write("{not json")
        time.sleep(0.002)  # distinct ids
        good = self.queue.enqueue("otp", email="b@example.com", username="bob", otp_code="123456", purpose="login_otp")
        service = MagicMock()
        service.send_otp_email.return_value = True
        self.queue.process_queue(service)
        service.send_otp_email.assert_called_once_with(
            email="b@example.com", username="bob", otp_code="123456", purpose="login_otp")
        self.assertNotIn(f"{good}.json", self._files())
        self.assertIn("broken.json", self._files())

    def test_old_files_are_cleaned_up(self):
        email_id = self._deletion()
        path = os.path.join(self.dir, f"{email_id}.json")
        old = time.time() - 8 * 24 * 3600
        os.utime(path, (old, old))
        fresh = self.queue.enqueue("otp", email="c@example.com", username="c", otp_code="1")
        self.queue.cleanup_old_files(max_age_days=7)
        self.assertEqual(self._files(), [f"{fresh}.json"])


if __name__ == "__main__":
    unittest.main()
