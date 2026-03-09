"""
Tests for timed exercise changes:
- timed_set_score / get_timed_set_stats
- best_workout_timed_score
- WorkoutQualityScorer.calculate_timed_workout_score
- _has_time_hint_in_exercise_string  (no >30 heuristic)
- _has_time_history                  (only explicit hints, NOT >30 reps)
- _is_timed_log from stats.py        (explicit [N-Ns] only)
- _get_peak_1rm_for_log              (routes timed vs rep-based correctly)
"""
import math
import sys
import os
import unittest
from unittest.mock import MagicMock

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------------------
# Imports — all packages are installed, import directly
# ---------------------------------------------------------------------------
from services.helpers import timed_set_score, get_timed_set_stats, get_set_stats
from services.best_scoring import best_workout_timed_score, best_workout_strength_score
from services.workout_quality import WorkoutQualityScorer
from services.logging import _has_time_hint_in_exercise_string, _has_time_history
from services.stats import _is_timed_log, _get_peak_1rm_for_log
from models import WorkoutLog


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_log(exercise_string="", sets_json=None, top_weight=None, top_reps=None,
              estimated_1rm=None, bodyweight=None):
    """Create a minimal WorkoutLog-like mock (avoids DB session)."""
    log = MagicMock(spec=WorkoutLog)
    log.exercise_string = exercise_string
    log.sets_json = sets_json or {}
    log.top_weight = top_weight
    log.top_reps = top_reps
    log.estimated_1rm = estimated_1rm
    log.bodyweight = bodyweight
    log.exercise = ""
    return log


def _make_db_session(logs):
    """Return a mock db_session whose .query().filter().all() returns logs."""
    db = MagicMock()
    q = MagicMock()
    q.filter.return_value = q
    q.all.return_value = logs
    db.query.return_value = q
    return db


# ===========================================================================
# 1. timed_set_score
# ===========================================================================
class TestTimedSetScore(unittest.TestCase):

    def test_basic_formula(self):
        score = timed_set_score(20.0, 30)
        self.assertAlmostEqual(score, 20.0 * math.sqrt(30), places=6)

    def test_zero_weight_returns_zero(self):
        self.assertEqual(timed_set_score(0.0, 30), 0.0)

    def test_zero_seconds_returns_zero(self):
        self.assertEqual(timed_set_score(20.0, 0), 0.0)

    def test_negative_weight_returns_zero(self):
        self.assertEqual(timed_set_score(-5.0, 30), 0.0)

    def test_negative_seconds_returns_zero(self):
        self.assertEqual(timed_set_score(20.0, -10), 0.0)

    def test_heavier_load_scores_higher(self):
        self.assertGreater(timed_set_score(40.0, 30), timed_set_score(10.0, 30))

    def test_longer_duration_scores_higher(self):
        self.assertGreater(timed_set_score(20.0, 60), timed_set_score(20.0, 15))

    def test_sqrt_diminishing_returns(self):
        s1 = timed_set_score(20.0, 30)
        s2 = timed_set_score(20.0, 120)   # 4× time → 2× score (sqrt)
        self.assertAlmostEqual(s2, s1 * 2, places=4)
        self.assertLess(s2, s1 * 4)        # NOT linear

    def test_bad_types_return_zero(self):
        self.assertEqual(timed_set_score("bad", 30), 0.0)
        self.assertEqual(timed_set_score(20.0, "bad"), 0.0)


# ===========================================================================
# 2. get_timed_set_stats
# ===========================================================================
class TestGetTimedSetStats(unittest.TestCase):

    def test_empty_input_returns_zeros(self):
        self.assertEqual(get_timed_set_stats({}), (0.0, 0.0, 0.0))
        self.assertEqual(get_timed_set_stats(None), (0.0, 0.0, 0.0))

    def test_single_set_values(self):
        sets = {"weights": [20.0], "reps": [30]}
        peak, total, work = get_timed_set_stats(sets)
        expected = timed_set_score(20.0, 30)
        self.assertAlmostEqual(peak, expected)
        self.assertAlmostEqual(total, expected)
        self.assertAlmostEqual(work, 20.0 * 30)

    def test_peak_is_max_set_score(self):
        sets = {"weights": [20.0, 30.0], "reps": [30, 15]}
        peak, total, work = get_timed_set_stats(sets)
        s1 = timed_set_score(20.0, 30)
        s2 = timed_set_score(30.0, 15)
        self.assertAlmostEqual(peak, max(s1, s2))
        self.assertAlmostEqual(total, s1 + s2)
        self.assertAlmostEqual(work, 20 * 30 + 30 * 15)

    def test_total_work_is_weight_times_seconds(self):
        sets = {"weights": [25.0, 25.0, 25.0], "reps": [40, 35, 30]}
        _, _, work = get_timed_set_stats(sets)
        self.assertAlmostEqual(work, 25 * 40 + 25 * 35 + 25 * 30)

    def test_mismatched_lengths_padded_safely(self):
        sets = {"weights": [20.0], "reps": [30, 25, 20]}
        peak, _, _ = get_timed_set_stats(sets)
        self.assertGreater(peak, 0)

    def test_not_same_as_epley_1rm(self):
        """For 60 seconds timed score ≠ weight*(1+60/30)."""
        sets = {"weights": [20.0], "reps": [60]}
        peak, _, _ = get_timed_set_stats(sets)
        epley = 20.0 * (1 + 60 / 30)           # 60.0
        sqrt_score = timed_set_score(20.0, 60)  # 20*sqrt(60) ≈ 154.9
        self.assertAlmostEqual(peak, sqrt_score, places=4)
        self.assertNotAlmostEqual(peak, epley, places=2)


# ===========================================================================
# 3. best_workout_timed_score
# ===========================================================================
class TestBestWorkoutTimedScore(unittest.TestCase):

    def test_empty_returns_zero(self):
        self.assertEqual(best_workout_timed_score({})["score"], 0.0)

    def test_single_set_no_bonus(self):
        sets = {"weights": [20.0], "reps": [30]}
        result = best_workout_timed_score(sets)
        expected_peak = timed_set_score(20.0, 30)
        self.assertAlmostEqual(result["peak_timed"], expected_peak)
        self.assertAlmostEqual(result["score"], expected_peak)

    def test_three_equal_sets_bonus(self):
        sets = {"weights": [20.0, 20.0, 20.0], "reps": [30, 30, 30]}
        result = best_workout_timed_score(sets, top_n=3)
        per = timed_set_score(20.0, 30)
        expected = per + 0.25 * (per * 3 - per)  # peak + 25% of surplus
        self.assertAlmostEqual(result["score"], expected, places=4)

    def test_differs_from_strength_score_for_long_holds(self):
        sets = {"weights": [20.0], "reps": [60]}
        self.assertNotAlmostEqual(
            best_workout_timed_score(sets)["score"],
            best_workout_strength_score(sets)["score"],
            places=1
        )

    def test_set_count_correct(self):
        sets = {"weights": [20.0, 22.0], "reps": [30, 25]}
        self.assertEqual(best_workout_timed_score(sets)["set_count"], 2.0)


# ===========================================================================
# 4. WorkoutQualityScorer.calculate_timed_workout_score
# ===========================================================================
class TestTimedQualityScorer(unittest.TestCase):

    def test_empty_returns_zero_quality(self):
        result = WorkoutQualityScorer.calculate_timed_workout_score({})
        self.assertEqual(result["quality_index"], 0)
        self.assertEqual(result["peak_1rm"], 0)

    def test_peak_1rm_equals_timed_score(self):
        sets = {"weights": [20.0], "reps": [30]}
        result = WorkoutQualityScorer.calculate_timed_workout_score(sets)
        self.assertAlmostEqual(result["peak_1rm"], timed_set_score(20.0, 30))
        self.assertAlmostEqual(result["peak_timed_score"], timed_set_score(20.0, 30))

    def test_total_volume_is_weight_times_seconds(self):
        sets = {"weights": [20.0, 20.0], "reps": [30, 40]}
        result = WorkoutQualityScorer.calculate_timed_workout_score(sets)
        self.assertAlmostEqual(result["total_volume"], 20 * 30 + 20 * 40)
        self.assertAlmostEqual(result["total_work"],   20 * 30 + 20 * 40)

    def test_perfect_duration_adherence(self):
        sets = {"weights": [20.0, 20.0], "reps": [30, 35]}
        result = WorkoutQualityScorer.calculate_timed_workout_score(sets, (20, 40))
        self.assertAlmostEqual(result["duration_adherence"], 1.0)

    def test_under_range_penalised(self):
        sets = {"weights": [20.0], "reps": [5]}
        result = WorkoutQualityScorer.calculate_timed_workout_score(sets, (30, 60))
        self.assertLess(result["duration_adherence"], 1.0)

    def test_over_range_penalised(self):
        sets = {"weights": [20.0], "reps": [120]}
        result = WorkoutQualityScorer.calculate_timed_workout_score(sets, (20, 40))
        self.assertLess(result["duration_adherence"], 1.0)

    def test_quality_index_bounded(self):
        sets = {"weights": [20.0, 22.0, 20.0], "reps": [30, 28, 32]}
        result = WorkoutQualityScorer.calculate_timed_workout_score(sets, (20, 40))
        self.assertGreaterEqual(result["quality_index"], 0.0)
        self.assertLessEqual(result["quality_index"], 1.0)

    def test_consistent_sets_beat_inconsistent(self):
        consistent   = {"weights": [20.0, 20.0, 20.0], "reps": [30, 30, 30]}
        inconsistent = {"weights": [20.0, 20.0, 20.0], "reps": [5, 30, 90]}
        r_con = WorkoutQualityScorer.calculate_timed_workout_score(consistent)
        r_inc = WorkoutQualityScorer.calculate_timed_workout_score(inconsistent)
        self.assertGreater(r_con["quality_index"], r_inc["quality_index"])

    def test_compat_keys_present_for_chart(self):
        """peak_1rm / total_volume / effective_volume must exist for chart code."""
        result = WorkoutQualityScorer.calculate_timed_workout_score(
            {"weights": [20.0], "reps": [30]}
        )
        for key in ("peak_1rm", "total_volume", "effective_volume"):
            self.assertIn(key, result)


# ===========================================================================
# 5. _has_time_hint_in_exercise_string
# ===========================================================================
class TestHasTimeHint(unittest.TestCase):

    def test_detects_standard_hint(self):
        self.assertTrue(_has_time_hint_in_exercise_string("Farmer's Walk [20-60s]"))

    def test_detects_uppercase_s(self):
        self.assertTrue(_has_time_hint_in_exercise_string("Plank [30-60S]"))

    def test_plain_exercise_not_detected(self):
        self.assertFalse(_has_time_hint_in_exercise_string("Bench Press"))

    def test_rep_range_without_s_not_detected(self):
        self.assertFalse(_has_time_hint_in_exercise_string("Bench Press [8-12]"))

    def test_empty_and_none(self):
        self.assertFalse(_has_time_hint_in_exercise_string(""))
        self.assertFalse(_has_time_hint_in_exercise_string(None))


# ===========================================================================
# 6. _has_time_history — critical: >30 reps must NOT trigger timed
# ===========================================================================
class TestHasTimeHistory(unittest.TestCase):

    def _log(self, exercise_string, reps):
        log = _make_log(exercise_string=exercise_string, sets_json={"weights": [0.0], "reps": reps})
        log.exercise = exercise_string
        return log

    def test_31_reps_NOT_timed(self):
        """Core bug fix: 31 reps (> old threshold of 30) must NOT trigger timed."""
        db = _make_db_session([self._log("Band Pull Apart", [31])])
        self.assertFalse(_has_time_history(db, 1, "Band Pull Apart"))

    def test_45_reps_NOT_timed(self):
        db = _make_db_session([self._log("Calf Raises", [45])])
        self.assertFalse(_has_time_history(db, 1, "Calf Raises"))

    def test_100_reps_NOT_timed(self):
        db = _make_db_session([self._log("Jump Rope", [100])])
        self.assertFalse(_has_time_history(db, 1, "Jump Rope"))

    def test_explicit_hint_returns_true(self):
        db = _make_db_session([self._log("Dead Hang [30-60s]", [45])])
        self.assertTrue(_has_time_history(db, 1, "Dead Hang"))

    def test_no_logs_returns_false(self):
        db = _make_db_session([])
        self.assertFalse(_has_time_history(db, 1, "New Exercise"))

    def test_plain_log_no_hint_returns_false(self):
        db = _make_db_session([self._log("Lat Pulldown", [12])])
        self.assertFalse(_has_time_history(db, 1, "Lat Pulldown"))


# ===========================================================================
# 7. _is_timed_log from stats.py
# ===========================================================================
class TestIsTimedLog(unittest.TestCase):

    def test_explicit_hint_detected(self):
        log = _make_log("Farmer's Walk [20-60s], 20, 30, 30")
        self.assertTrue(_is_timed_log(log))

    def test_no_hint_not_timed(self):
        log = _make_log("Bench Press")
        self.assertFalse(_is_timed_log(log))

    def test_rep_range_without_s_not_timed(self):
        log = _make_log("Bench Press [8-12]")
        self.assertFalse(_is_timed_log(log))

    def test_high_reps_no_hint_not_timed(self):
        log = _make_log("Calf Raises", sets_json={"weights": [0.0], "reps": [50]})
        self.assertFalse(_is_timed_log(log))

    def test_uppercase_s_detected(self):
        log = _make_log("Dead Hang [30-60S]")
        self.assertTrue(_is_timed_log(log))

    def test_empty_string_not_timed(self):
        log = _make_log("")
        self.assertFalse(_is_timed_log(log))


# ===========================================================================
# 8. _get_peak_1rm_for_log  — routes timed vs rep-based correctly
# ===========================================================================
class TestGetPeak1rmForLog(unittest.TestCase):

    def test_rep_based_uses_1rm_formula(self):
        """Normal lift → Epley / WorkoutQualityScorer estimate."""
        log = _make_log(
            exercise_string="Bench Press",
            sets_json={"weights": [80.0], "reps": [5]},
        )
        peak = _get_peak_1rm_for_log(log)
        expected = WorkoutQualityScorer.estimate_1rm(80.0, 5)
        self.assertAlmostEqual(peak, expected, places=2)

    def test_timed_uses_sqrt_score(self):
        """Timed exercise → weight * sqrt(seconds)."""
        log = _make_log(
            exercise_string="Farmer's Walk [20-60s], 20",
            sets_json={"weights": [20.0], "reps": [30]},
        )
        peak = _get_peak_1rm_for_log(log)
        expected = timed_set_score(20.0, 30)
        self.assertAlmostEqual(peak, expected, places=4)

    def test_timed_score_differs_from_1rm_for_long_holds(self):
        """Timed and rep-based scores must diverge for 60s holds."""
        timed_log = _make_log(
            exercise_string="Dead Hang [30-60s]",
            sets_json={"weights": [0.0], "reps": [60]},
        )
        rep_log = _make_log(
            exercise_string="Pull Ups",
            sets_json={"weights": [0.0], "reps": [60]},
        )
        peak_timed = _get_peak_1rm_for_log(timed_log)
        # timed → 0 * sqrt(60) = 0 (zero bodyweight); test with nonzero weight
        timed_log2 = _make_log(
            exercise_string="Farmer's Walk [30-60s]",
            sets_json={"weights": [20.0], "reps": [60]},
        )
        rep_log2 = _make_log(
            exercise_string="Overhead Press",
            sets_json={"weights": [20.0], "reps": [60]},
        )
        p_timed = _get_peak_1rm_for_log(timed_log2)
        p_rep   = _get_peak_1rm_for_log(rep_log2)
        self.assertAlmostEqual(p_timed, timed_set_score(20.0, 60), places=4)
        self.assertNotAlmostEqual(p_timed, p_rep, places=1)

    def test_fallback_to_stored_1rm_when_no_sets(self):
        """If no sets_json, falls back to log.estimated_1rm."""
        log = _make_log(exercise_string="Bench Press", sets_json={}, estimated_1rm=120.0)
        peak = _get_peak_1rm_for_log(log)
        self.assertAlmostEqual(peak, 120.0, places=2)


# ===========================================================================
# 9. Duration adherence helper
# ===========================================================================
class TestDurationAdherence(unittest.TestCase):

    def test_in_range_perfect(self):
        self.assertAlmostEqual(
            WorkoutQualityScorer._duration_adherence_score([30, 35, 40], (20, 45)), 1.0
        )

    def test_under_range_penalised(self):
        score = WorkoutQualityScorer._duration_adherence_score([5], (30, 60))
        self.assertLess(score, 1.0)
        self.assertGreaterEqual(score, 0.0)

    def test_over_range_penalised(self):
        score = WorkoutQualityScorer._duration_adherence_score([120], (20, 40))
        self.assertLess(score, 1.0)
        self.assertGreaterEqual(score, 0.0)

    def test_no_target_returns_one(self):
        self.assertEqual(WorkoutQualityScorer._duration_adherence_score([30, 35], None), 1.0)

    def test_boundary_values_perfect(self):
        self.assertAlmostEqual(
            WorkoutQualityScorer._duration_adherence_score([20, 60], (20, 60)), 1.0
        )


# ===========================================================================
# 10. Work consistency helper
# ===========================================================================
class TestWorkConsistency(unittest.TestCase):

    def test_single_set_is_perfect(self):
        self.assertEqual(WorkoutQualityScorer._work_consistency([600]), 1.0)

    def test_identical_sets_perfect(self):
        self.assertAlmostEqual(WorkoutQualityScorer._work_consistency([600, 600, 600]), 1.0)

    def test_variable_sets_lower_than_consistent(self):
        c = WorkoutQualityScorer._work_consistency([600, 600, 600])
        v = WorkoutQualityScorer._work_consistency([100, 600, 1200])
        self.assertGreater(c, v)

    def test_output_bounded_0_to_1(self):
        score = WorkoutQualityScorer._work_consistency([100, 500, 1000])
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)


# ===========================================================================
# 11. Integration: improvement detection
# ===========================================================================
class TestImprovementDetection(unittest.TestCase):

    def test_longer_hold_is_improvement(self):
        old_peak, _, _ = get_timed_set_stats({"weights": [20.0], "reps": [30]})
        new_peak, _, _ = get_timed_set_stats({"weights": [20.0], "reps": [45]})
        self.assertGreater(new_peak, old_peak)

    def test_heavier_load_same_duration_is_improvement(self):
        old_peak, _, _ = get_timed_set_stats({"weights": [20.0], "reps": [30]})
        new_peak, _, _ = get_timed_set_stats({"weights": [25.0], "reps": [30]})
        self.assertGreater(new_peak, old_peak)

    def test_more_sets_raises_session_score(self):
        one_set   = best_workout_timed_score({"weights": [20.0], "reps": [30]})["score"]
        three_set = best_workout_timed_score(
            {"weights": [20.0, 20.0, 20.0], "reps": [30, 30, 30]}
        )["score"]
        self.assertGreater(three_set, one_set)

    def test_rep_based_improvement_unaffected(self):
        """Verify normal 1RM path is not broken by timed changes."""
        old_score = best_workout_strength_score({"weights": [80.0], "reps": [5]})["score"]
        new_score = best_workout_strength_score({"weights": [85.0], "reps": [5]})["score"]
        self.assertGreater(new_score, old_score)


if __name__ == "__main__":
    unittest.main(verbosity=2)

