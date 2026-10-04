import unittest

from services.rep_ranges import canonical_rep_text, format_rep_value, lookup_rep_target, parse_rep_entries, rep_range_names


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
        # is dropped, and a name with no range is kept without one.
        self.assertEqual(canonical_rep_text(text), "Bench Press: 5–8\nRow: 8–12\nPlank:")

    def test_words_standing_in_for_a_range_mean_no_range(self):
        text = "Chest Dips: (blank)\nBarbell Squat: n/a\nSeated Leg Curl: -\nPlank: TBD\nPush-Ups: Max reps"
        self.assertEqual(
            canonical_rep_text(text),
            "Chest Dips:\nBarbell Squat:\nSeated Leg Curl:\nPlank:\nPush-Ups: Max reps",
        )
        # A blank later in the list doesn't wipe a range given earlier.
        self.assertEqual(canonical_rep_text("Dips: 8-12\nDips: (blank)"), "Dips: 8–12")

    def test_existing_stored_text_reads_back_unchanged(self):
        stored = "Flat Barbell Press: 5–8\nDips: 3, 6–12\nForearm Roller: 30–60s"
        self.assertEqual(canonical_rep_text(stored), stored)


class TestGymTags(unittest.TestCase):
    """A bracket tag names the gym or machine ("Preacher Curl (Wellness)"): the rep range comes
    from the base name unless the tagged name has its own."""

    def test_base_names_for_tags_as_they_are_written(self):
        self.assertEqual(rep_range_names("Preacher Curl (Wellness)"), ["Preacher Curl (Wellness)", "Preacher Curl"])
        self.assertEqual(rep_range_names("Hip Thrust - (wellness)"), ["Hip Thrust - (wellness)", "Hip Thrust"])
        self.assertEqual(rep_range_names("Preacher Curl\t (Wellness)"), ["Preacher Curl (Wellness)", "Preacher Curl"])
        self.assertEqual(rep_range_names("Wrist Flexion - Machine (YFC)"), ["Wrist Flexion - Machine (YFC)", "Wrist Flexion - Machine"])
        self.assertEqual(rep_range_names("Preacher Curl"), ["Preacher Curl"])
        self.assertEqual(rep_range_names("(Wellness)"), ["(Wellness)"])

    def test_a_tagged_name_uses_the_base_range_unless_it_has_its_own(self):
        ranges = {"preacher curl": "8–12", "machine lateral raise": "12–20", "machine lateral raise (old school)": "15–25"}
        sets = {"preacher curl": 2}
        self.assertEqual(lookup_rep_target("Preacher Curl (YFC)", ranges, sets), ("8–12", 2))
        self.assertEqual(lookup_rep_target("preacher curl (wellness)", ranges, sets), ("8–12", 2))
        self.assertEqual(lookup_rep_target("Machine Lateral Raise (Old School)", ranges, sets), ("15–25", None))
        self.assertEqual(lookup_rep_target("Machine Lateral Raise (Fit Class)", ranges, sets), ("12–20", None))
        self.assertEqual(lookup_rep_target("Leg Press (Wellness)", ranges, sets), ("", None))


if __name__ == "__main__":
    unittest.main()
