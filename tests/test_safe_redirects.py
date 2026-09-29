import unittest

from utils.errors import ValidationError
from utils.validators import is_safe_redirect_url, parse_bodyweight

HOST = "http://localhost/"


class TestIsSafeRedirectUrl(unittest.TestCase):
    def test_paths_on_this_site_are_allowed(self):
        for target in ("/stats", "/workout/2026-09-30", "/log?x=1#top", "http://localhost/stats"):
            self.assertTrue(is_safe_redirect_url(target, HOST), target)

    def test_other_sites_are_refused(self):
        for target in (
            "",
            None,
            "//evil.com",
            "/\\evil.com",
            "\\\\evil.com",
            "https://evil.com/login",
            "javascript:alert(1)",
            "/\tevil.com",
            "/\r\nSet-Cookie:x",
        ):
            self.assertFalse(is_safe_redirect_url(target, HOST), repr(target))



class TestParseBodyweight(unittest.TestCase):
    def test_sensible_weights(self):
        self.assertEqual(parse_bodyweight("78.5"), 78.5)
        self.assertEqual(parse_bodyweight(" 80 "), 80.0)
        self.assertIsNone(parse_bodyweight(""))
        self.assertIsNone(parse_bodyweight(None))

    def test_nonsense_is_refused(self):
        for raw in ("nan", "inf", "-inf", "1e5", "780.5", "0", "-70", "0.5", "seventy"):
            with self.assertRaises(ValidationError, msg=raw):
                parse_bodyweight(raw)


if __name__ == "__main__":
    unittest.main()
