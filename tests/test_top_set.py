"""The top set is the heaviest weight with the most reps done at it."""
import unittest

from services.helpers import top_set


class TestTopSet(unittest.TestCase):
    def test_a_tie_on_weight_takes_the_most_reps(self):
        self.assertEqual(top_set([60, 60], [12, 16]), (60.0, 16))
        self.assertEqual(top_set([60, 55, 60], [16, 20, 12]), (60.0, 16))

    def test_heaviest_weight_wins_over_more_reps(self):
        self.assertEqual(top_set([55, 60], [20, 8]), (60.0, 8))

    def test_no_usable_sets(self):
        self.assertEqual(top_set([], []), (None, None))
        self.assertEqual(top_set([None], [5]), (None, None))
