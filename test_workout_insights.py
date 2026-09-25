import unittest
from datetime import date

from services import workout_insights as insights


class TestTrend(unittest.TestCase):
    def test_compares_with_average_of_last_three_sessions(self):
        # 100 -> 90 -> 95 style oscillation: vs last looks like progress, vs recent is steady.
        previous = [
            {"date": date(2026, 1, 3), "score": 90.0},
            {"date": date(2026, 1, 2), "score": 100.0},
            {"date": date(2026, 1, 1), "score": 95.0},
            {"date": date(2025, 12, 1), "score": 10.0},  # outside the window
        ]
        result = insights.trend(95.0, previous)
        self.assertEqual(result["direction"], "steady")
        self.assertEqual(result["text"], "→ Steady")
        self.assertEqual(result["count"], 3)
        self.assertEqual(result["dates"], [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)])

    def test_steady_band_is_symmetric(self):
        up = insights.trend(101.5, [{"date": date(2026, 1, 1), "score": 100.0}])
        down = insights.trend(98.5, [{"date": date(2026, 1, 1), "score": 100.0}])
        flat = insights.trend(100.5, [{"date": date(2026, 1, 1), "score": 100.0}])
        self.assertEqual((up["direction"], up["text"]), ("up", "↑ 1.5%"))
        self.assertEqual((down["direction"], down["text"]), ("down", "↓ 1.5%"))
        self.assertEqual(flat["direction"], "steady")

    def test_no_history_means_no_trend(self):
        self.assertIsNone(insights.trend(100.0, []))


class TestRankedComparison(unittest.TestCase):
    def test_sets_are_matched_by_strength_not_order(self):
        # Same three sets, done in a different order: every rank is "same".
        today = insights.ranked_sets(
            {"weights": [40, 50, 45], "reps": [10, 8, 9]},
            {"weights": [40, 50, 45], "reps": [10, 8, 9]},
            uses_bodyweight=False,
            is_timed=False,
        )
        last = insights.ranked_sets(
            {"weights": [50, 45, 40], "reps": [8, 9, 10]},
            {"weights": [50, 45, 40], "reps": [8, 9, 10]},
            uses_bodyweight=False,
            is_timed=False,
        )
        rows = insights.compare_columns(today, last)
        self.assertEqual([r["rank"] for r in rows], ["1st", "2nd", "3rd"])
        self.assertTrue(all(r["last"]["pct_text"] == "same" for r in rows))
        self.assertTrue(all(r["best"] is None for r in rows))
        self.assertEqual(rows[0]["today"], "50×8")

    def test_bodyweight_sets_are_labelled_with_added_load(self):
        ranked = insights.ranked_sets(
            {"weights": [0, 2.5], "reps": [12, 10]},
            {"weights": [70, 72.5], "reps": [12, 10]},
            uses_bodyweight=True,
            is_timed=False,
        )
        self.assertEqual({r["label"] for r in ranked}, {"BW×12", "BW+2.5×10"})

    def test_extra_set_shows_dash_on_missing_side(self):
        today = insights.ranked_sets({"weights": [50, 50], "reps": [8, 8]}, {"weights": [50, 50], "reps": [8, 8]}, uses_bodyweight=False, is_timed=False)
        last = insights.ranked_sets({"weights": [50], "reps": [8]}, {"weights": [50], "reps": [8]}, uses_bodyweight=False, is_timed=False)
        rows = insights.compare_columns(today, last, last)
        self.assertEqual(rows[1]["last"]["label"], "—")
        self.assertEqual(rows[1]["best"]["label"], "—")
        self.assertEqual(rows[0]["best"]["pct_text"], "same")


class TestBestSummary(unittest.TestCase):
    def test_medal_chip_shows_deciding_set_percentage(self):
        chip = insights.best_summary(
            {"key": "silver_strength", "diff_index": 1, "diff_pct": 3.04}, has_history=True
        )
        self.assertEqual((chip["emoji"], chip["chip"]), ("🥈", "+3%"))
        self.assertIn("2nd set beat it by 3%", chip["explain"])

    def test_load_medal_mentions_weight(self):
        chip = insights.best_summary({"key": "gold_load", "diff_index": 0, "diff_pct": 5.0}, has_history=True)
        self.assertEqual((chip["emoji"], chip["chip"]), ("🥇", "+5%"))
        self.assertIn("more weight", chip["explain"])

    def test_below_best_is_calm_single_label(self):
        for key in ("slightly_off", "moderately_off", "significantly_off"):
            chip = insights.best_summary({"key": key, "diff_index": 0, "diff_pct": -6.2}, has_history=True)
            self.assertEqual(chip["kind"], "below")
            self.assertEqual(chip["chip"], "−6.2%")

    def test_first_log_vs_new_baseline(self):
        self.assertEqual(insights.best_summary({"key": "first_log"}, has_history=False)["chip"], "✦ First")
        self.assertEqual(insights.best_summary({"key": "first_log"}, has_history=True)["chip"], "✦ New")


class TestFormatting(unittest.TestCase):
    def test_format_pct(self):
        self.assertEqual(insights.format_pct(4.83), "+4.8%")
        self.assertEqual(insights.format_pct(-12.4), "−12%")
        self.assertEqual(insights.format_pct(5.0), "+5%")

    def test_timed_sets(self):
        self.assertEqual(
            insights.format_sets_line({"weights": [30, 27.5], "reps": [36, 40]}, uses_bodyweight=False, is_timed=True),
            "30×36s · 27.5×40s",
        )

    def test_sparkline_needs_two_points(self):
        self.assertIsNone(insights.sparkline([{"score": 10.0}]))
        spark = insights.sparkline([{"score": 10.0}, {"score": 12.0, "is_today": True}], average=11.0)
        self.assertEqual(len(spark["dots"]), 2)
        self.assertTrue(spark["dots"][1]["is_today"])
        self.assertIsNotNone(spark["average_y"])


if __name__ == "__main__":
    unittest.main()
