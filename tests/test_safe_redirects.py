import unittest

from utils.validators import is_safe_redirect_url

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


if __name__ == "__main__":
    unittest.main()
