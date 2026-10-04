"""Custom workout's "often trained" rule: learned from each person's own pace."""
import unittest
from datetime import date, timedelta

from services.retrieve import often_trained_keys

START = date(2026, 3, 2)  # a Monday


def weekly(weeks, sessions, start=START, days=(0, 1, 2, 3, 4)):
    """One muscle a day: each session once a week, for `weeks` weeks."""
    out = []
    for w in range(weeks):
        for offset, session in zip(days, sessions):
            out.append((start + timedelta(weeks=w, days=offset), set(session)))
    return out


SPLIT = [{"bench", "fly"}, {"row", "pulldown"}, {"press", "raise"}, {"curl", "pushdown"}, {"squat", "leg curl"}]


class TestOftenTrained(unittest.TestCase):
    def test_too_little_history_folds_nothing(self):
        workouts = [(START, {"bench", "row"}), (START + timedelta(days=2), {"bench", "squat"})]
        self.assertIsNone(often_trained_keys(workouts))

    def test_a_weekly_routine_is_often_and_a_dropped_exercise_is_not(self):
        sessions = [set(s) for s in SPLIT]
        workouts = weekly(6, sessions)
        sessions[0] = {"bench"}                       # fly dropped, five weeks before the end
        workouts += weekly(5, sessions, start=START + timedelta(weeks=6))
        often = often_trained_keys(workouts)
        self.assertEqual(often, {"bench", "row", "pulldown", "press", "raise", "curl", "pushdown", "squat", "leg curl"})

    def test_once_is_a_one_off_but_a_first_time_this_week_is_new(self):
        workouts = weekly(8, SPLIT)
        workouts[10][1].add("upright row")            # once, weeks ago
        workouts[-2][1].add("hip thrust")             # first time, yesterday
        often = often_trained_keys(workouts)
        self.assertNotIn("upright row", often)
        self.assertIn("hip thrust", often)

    def test_a_long_break_does_not_fold_the_routine(self):
        before = weekly(8, SPLIT)
        back = [(before[-1][0] + timedelta(weeks=9), set(SPLIT[0]))]   # one workout after 2 months off
        often = often_trained_keys(before + back)
        self.assertTrue({"bench", "row", "press", "curl", "squat"} <= often)

    def test_the_window_follows_each_persons_pace(self):
        # Every exercise once in 3 weeks: the pace is 21 days, so the window is 63 days.
        slow = [(START + timedelta(days=7 * i), {f"ex{i % 3}"}) for i in range(15)]
        self.assertEqual(often_trained_keys(slow), {"ex0", "ex1", "ex2"})
        # At a weekly pace the window is 4 weeks, so "ex9", last done 9 weeks ago, folds.
        fast = weekly(10, SPLIT)
        fast[1][1].add("ex9"); fast[3][1].add("ex9")
        self.assertNotIn("ex9", often_trained_keys(fast))

    def test_the_plan_counts_when_it_is_followed(self):
        workouts = weekly(8, SPLIT)
        followed = {"bench", "row", "press", "curl", "wrist roller"}       # 4 of 5 trained
        self.assertIn("wrist roller", often_trained_keys(workouts, followed))
        ignored = {"bench", "dips", "lunge", "shrug", "wrist roller"}     # 1 of 5 trained
        self.assertNotIn("wrist roller", often_trained_keys(workouts, ignored))


if __name__ == "__main__":
    unittest.main()
