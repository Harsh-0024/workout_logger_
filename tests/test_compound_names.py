"""A word written as one or two is the same exercise ("Push Down" / "Pushdown"), but only when some
name already writes it as one: words are never invented."""
import unittest

from services.compound_report import new_merges_for_names
from services.exercise_matching import same_exercise


class TestSameExercise(unittest.TestCase):
    def test_a_word_written_as_one_or_two(self):
        for a, b in [
            ("Rod Tricep Push-Down", "Rod Triceps Pushdown"),
            ("Tricep Rod Push Down", "Triceps Rod Pushdown"),
            ("Pull Up", "Pullups"),
            ("Push Ups", "Pushups"),
            ("Dead Lift", "Deadlift"),
        ]:
            self.assertTrue(same_exercise(a, b), (a, b))

    def test_nothing_else_is_merged(self):
        for a, b in [
            ("Rod Push Down", "Rope Pushdown"),     # another word differs
            ("Rod Pushdown", "Push"),               # a part isn't the whole
            ("Seated Cable Row", "Seatedcable"),    # a word is missing
            ("Lat Pull Down", "Lat Pull"),
        ]:
            self.assertFalse(same_exercise(a, b), (a, b))

    def test_only_two_neighbouring_words_join(self):
        self.assertFalse(same_exercise("Seated Cable Row", "Seatedcablerow"))


class TestReport(unittest.TestCase):
    def test_lists_only_what_would_newly_merge(self):
        merges = new_merges_for_names([
            "Rod Triceps Pushdown", "Tricep Rod Push-Down", "Triceps Rod Pushdown",  # last two: already one
            "Pull Up", "Pullups", "Bench Press", "Rope Pushdown",
        ])
        self.assertEqual(sorted(merges), [["Pull Up", "Pullups"],
                                          ["Rod Triceps Pushdown", "Tricep Rod Push-Down", "Triceps Rod Pushdown"]])


if __name__ == "__main__":
    unittest.main()
