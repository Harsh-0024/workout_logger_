import unittest

from parsers.workout import workout_parser
from utils.rich_text import to_plain_text

PLAIN = "12/01 Chest Day\nBench Press 100x5\nIncline Dumbbell 30 30 30, 10 10 8"


class TestRichText(unittest.TestCase):
    def assertParsesLikePlain(self, rich):
        expected = workout_parser(PLAIN)["exercises"]
        got = workout_parser(to_plain_text(rich))["exercises"]
        self.assertEqual(
            [(e["name"], e["weights"], e["reps"]) for e in got],
            [(e["name"], e["weights"], e["reps"]) for e in expected],
        )

    def test_plain_text_is_unchanged(self):
        self.assertEqual(to_plain_text(PLAIN), PLAIN)
        self.assertEqual(to_plain_text("12/01 Chest & Biceps"), "12/01 Chest & Biceps")

    def test_apple_notes_line_separators_and_spaces(self):
        self.assertParsesLikePlain(PLAIN.replace("\n", " ").replace(" ", " ", 2))
        self.assertParsesLikePlain(PLAIN.replace("\n", "\r\n") + "￼​")

    def test_bullets_and_checklists(self):
        lines = PLAIN.split("\n")
        self.assertParsesLikePlain("\n".join([lines[0]] + [f"\t•\t{line}" for line in lines[1:]]))
        self.assertParsesLikePlain("\n".join([lines[0]] + [f"☐ {line}" for line in lines[1:]]))

    def test_html(self):
        html = "".join(f"<div><b>{line}</b></div>" for line in PLAIN.split("\n"))
        self.assertParsesLikePlain("<html><head><style>b{}</style></head><body>" + html + "</body></html>")
        self.assertParsesLikePlain(PLAIN.replace("\n", "<br>").replace(" ", "&nbsp;", 1))

    def test_rtf(self):
        bs = "\\"
        rtf = (
            "{" + bs + "rtf1" + bs + "ansi{" + bs + "fonttbl" + bs + "f0 Helvetica;}"
            "{" + bs + "colortbl;" + bs + "red0" + bs + "green0" + bs + "blue0;}"
            + bs + "f0" + bs + "b " + PLAIN.replace("\n", bs + "par\n") + "}"
        )
        self.assertParsesLikePlain(rtf)
        self.assertParsesLikePlain(rtf.encode("utf-8"))
        self.assertEqual(to_plain_text("{" + bs + "rtf1 a " + bs + "u8211" + bs + "'96 b}"), "a – b")


if __name__ == "__main__":
    unittest.main()
