import unittest

from services.rep_ranges import canonical_rep_text, format_rep_value, parse_rep_entries


class TestRepRanges(unittest.TestCase):
    def test_values_are_written_one_way(self):
        self.assertEqual(format_rep_value("6 - 10"), "6–10")
        self.assertEqual(format_rep_value("6 to 10"), "6–10")
        self.assertEqual(format_rep_value("10-6"), "6–10")
        self.assertEqual(format_rep_value("12"), "12")
        self.assertEqual(format_rep_value("3,6-8"), "3, 6–8")
        self.assertEqual(format_rep_value("3x6-8"), "3, 6–8")
        self.assertEqual(format_rep_value("30-60 sec"), "30–60s")
        self.assertEqual(format_rep_value("AMRAP"), "AMRAP")

    def test_reads_every_common_way_of_writing_a_list(self):
        text = (
            "bench press 6-10\n"
            "Incline Dumbbell Press\n8 to 12\n"
            "Wrist Flexion - Dumbbell: 12-20\n"
            "Farmer's Walk = 20-60s\n"
            "Dips\t3x6-12\n"
            "Leg Curl:\n10-15\n"
            "Plank\n"
        )
        self.assertEqual(parse_rep_entries(text), [
            ("Bench Press", "6–10"),
            ("Incline Dumbbell Press", "8–12"),
            ("Wrist Flexion - Dumbbell", "12–20"),
            ("Farmer's Walk", "20–60s"),
            ("Dips", "3, 6–12"),
            ("Leg Curl", "10–15"),
            ("Plank", ""),
        ])

    def test_stored_text_has_one_line_per_exercise(self):
        text = "Bench Press: 6-10\nrow 8-12\n6-10\nbench press: 5-8\nPlank"
        # A repeat keeps its first place and spelling and takes the last range; a stray range
        # or a name with no range is dropped.
        self.assertEqual(canonical_rep_text(text), "Bench Press: 5–8\nRow: 8–12")

    def test_existing_stored_text_reads_back_unchanged(self):
        stored = "Flat Barbell Press: 5–8\nDips: 3, 6–12\nForearm Roller: 30–60s"
        self.assertEqual(canonical_rep_text(stored), stored)


if __name__ == "__main__":
    unittest.main()
