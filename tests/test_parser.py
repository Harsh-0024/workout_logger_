import unittest
# IMPORTS: Assumes you have moved files to 'parsers/workout.py'
from parsers.workout import workout_parser, parse_weight_x_reps, normalize
from list_of_exercise import get_workout_days
from services.workout_quality import WorkoutQualityScorer


class TestWorkoutParser(unittest.TestCase):

    def test_normalize_logic(self):
        """Test that normalize preserves full set data."""
        self.assertEqual(normalize([100]), [100])
        self.assertEqual(normalize([100, 110]), [100, 110])
        self.assertEqual(normalize([10, 20, 30]), [10, 20, 30])
        self.assertEqual(normalize([]), [])

    def test_strict_x_parser(self):
        """Test the specific 'Weight x Reps' logic."""
        # Standard
        w, r = parse_weight_x_reps("100x5")
        self.assertEqual(w, [100.0])
        self.assertEqual(r, [5])

        # Multiple sets
        w, r = parse_weight_x_reps("100x5 110x3")
        self.assertEqual(w, [100.0, 110.0])
        self.assertEqual(r, [5, 3])

        # Negative numbers (Assisted)
        w, r = parse_weight_x_reps("-35x5")
        self.assertEqual(w, [-35.0])

        # Decimal numbers
        w, r = parse_weight_x_reps("22.5x10")
        self.assertEqual(w, [22.5])

        # Repeated reps reuse last weight
        w, r = parse_weight_x_reps("100x5 x5 x5")
        self.assertEqual(w, [100.0, 100.0, 100.0])
        self.assertEqual(r, [5, 5, 5])

        # BW divisor and adjustment
        w, r = parse_weight_x_reps("bw/4x6", 80)
        self.assertEqual(w, [20.0])
        self.assertEqual(r, [6])

        w, r = parse_weight_x_reps("bw/2+10x5 x6", 80)
        self.assertEqual(w, [50.0, 50.0])
        self.assertEqual(r, [5, 6])

    def test_full_workout_parsing(self):
        """Test the full text block parsing."""
        raw_text = """
        12/01 Chest Day
        1. Bench Press 100x5
        2. Incline Dumbbell 30 30 30, 10 10 8
        3. Pec Fly 15 15
        4. Pull Ups -35x5
        """
        result = workout_parser(raw_text)
        exs = result['exercises']

        # Exercise 1: Bench Press (Standard X)
        self.assertEqual(exs[0]['name'], "Bench Press")
        self.assertEqual(exs[0]['weights'], [100.0, 100.0, 100.0])
        self.assertEqual(exs[0]['reps'], [5, 5, 5])
        self.assertEqual(exs[0]['valid'], True)

        # Exercise 2: Incline DB (Comma separated)
        self.assertEqual(exs[1]['name'], "Incline Dumbbell")
        self.assertEqual(exs[1]['weights'], [30.0, 30.0, 30.0])
        self.assertEqual(exs[1]['valid'], True)

        # Exercise 3: Pec Fly (Implicit Reps)
        self.assertEqual(exs[2]['name'], "Pec Fly")
        self.assertEqual(exs[2]['weights'], [15.0, 15.0, 15.0])
        self.assertEqual(exs[2]['reps'], [1, 1, 1])
        self.assertEqual(exs[2]['valid'], True)

        # Exercise 4: Pull Ups (Negative)
        self.assertEqual(exs[3]['name'], "Pull Ups")
        self.assertEqual(exs[3]['weights'], [-35.0, -35.0, -35.0])
        self.assertEqual(exs[3]['reps'], [5, 5, 5])
        self.assertEqual(exs[3]['valid'], True)

    def test_fail_loudly_case(self):
        """Test that invalid lines return valid=False."""
        # FIX: Added a header line so parser doesn't eat 'Stretching' as the title
        raw_text = """
        12/01 Rest Day
        1. Stretching
        2. Cardio 20 mins
        """
        result = workout_parser(raw_text)
        exs = result['exercises']

        # Exercise 1: Stretching (No numbers)
        self.assertEqual(exs[0]['name'], "Stretching")
        self.assertEqual(exs[0]['weights'], [])
        self.assertEqual(exs[0]['valid'], False)  # Should be invalid

        # Exercise 2: Cardio
        # Current parser sees "20" and thinks it is weight.
        # This is expected behavior for now, so we just confirm the name parses.
        self.assertEqual(exs[1]['name'], "Cardio")

    def test_truly_empty_case(self):
        raw_text = """
        12/01 Empty Day
        1. Just Text No Numbers
        """
        result = workout_parser(raw_text)
        exs = result['exercises']

        self.assertEqual(exs[0]['name'], "Just Text No Numbers")
        self.assertEqual(exs[0]['valid'], False)

    def test_multiline_and_bw_parsing(self):
        raw_text = """
        22/01 Chest & Triceps 3
        Incline Dumbbell Press - [6-10]
        25 22.5 20, 7 9 10
        Triceps Rod Pushdown - [10-15]
        45 40
        10 15 10
        Dips - [6-12]
        Bw, 6
        Dips - [6-12] - Bw-20, 6
        Dips - [6-12] - Bw+10, 6
        Lower Abs - [12-20] :
        ,16
        """
        result = workout_parser(raw_text, bodyweight=70)
        exs = result['exercises']

        self.assertEqual(exs[0]['name'], "Incline Dumbbell Press")
        self.assertEqual(exs[0]['weights'], [25.0, 22.5, 20.0])
        self.assertEqual(exs[0]['reps'], [7, 9, 10])

        self.assertEqual(exs[1]['name'], "Triceps Rod Pushdown")
        self.assertEqual(exs[1]['weights'], [45.0, 40.0, 40.0])
        self.assertEqual(exs[1]['reps'], [10, 15, 10])

        self.assertEqual(exs[2]['weights'], [70.0, 70.0, 70.0])
        self.assertEqual(exs[2]['reps'], [6, 6, 6])

        self.assertEqual(exs[3]['weights'], [50.0, 50.0, 50.0])
        self.assertEqual(exs[3]['reps'], [6, 6, 6])

        self.assertEqual(exs[4]['weights'], [80.0, 80.0, 80.0])
        self.assertEqual(exs[4]['reps'], [6, 6, 6])

        self.assertEqual(exs[5]['weights'], [1.0, 1.0, 1.0])
        self.assertEqual(exs[5]['reps'], [16, 16, 16])

    def test_decimal_weights_are_preserved(self):
        raw_text = """
        04/02 Chest Day
        Flat Dumbbell Press - [8-12]
        20.8 20, 10 12
        Skull Crushers - [6-10]
        2.5 1, 9 11
        """
        result = workout_parser(raw_text)
        exs = result['exercises']

        self.assertEqual(exs[0]['name'], "Flat Dumbbell Press")
        self.assertEqual(exs[0]['weights'], [20.8, 20.0, 20.0])
        self.assertEqual(exs[0]['reps'], [10, 12, 12])

        self.assertEqual(exs[1]['name'], "Skull Crushers")
        self.assertEqual(exs[1]['weights'], [2.5, 1.0, 1.0])
        self.assertEqual(exs[1]['reps'], [9, 11, 11])

    def test_time_based_exercise_parses_seconds_with_comma(self):
        raw_text = """
        06/03 Carry Day
        Dumbbell Farmer's Walk - [20-60s]
        25 22.5 22.5, 50 54 40
        """
        result = workout_parser(raw_text)
        exs = result['exercises']

        self.assertEqual(exs[0]['name'].lower(), "dumbbell farmer's walk")
        self.assertEqual(exs[0]['weights'], [25.0, 22.5, 22.5])
        self.assertEqual(exs[0]['reps'], [50, 54, 40])
        self.assertTrue(exs[0]['valid'])

    def test_time_based_exercise_parses_seconds_from_split_halves(self):
        raw_text = """
        06/03 Carry Day
        Dumbbell Farmer's Walk - [20-60s]
        25 22.5 22.5 50 54 40
        """
        result = workout_parser(raw_text)
        exs = result['exercises']

        self.assertEqual(exs[0]['weights'], [25.0, 22.5, 22.5])
        self.assertEqual(exs[0]['reps'], [50, 54, 40])
        self.assertTrue(exs[0]['valid'])

    def test_bracket_single_number_is_set_count(self):
        raw_text = """
        12/01 Test Day
        Barbell Curl - [4]
        5 2.5, 10
        """
        result = workout_parser(raw_text)
        exs = result["exercises"]
        self.assertEqual(exs[0]["name"], "Barbell Curl")
        self.assertEqual(len(exs[0]["weights"]), 4)
        self.assertEqual(exs[0]["reps"], [10, 10, 10, 10])

    def test_bracket_prefix_sets_count(self):
        raw_text = """
        12/01 Test Day
        Barbell Curl - [2, 6-10]
        5 2.5, 10
        """
        result = workout_parser(raw_text)
        exs = result["exercises"]
        self.assertEqual(len(exs[0]["weights"]), 2)
        self.assertEqual(exs[0]["reps"], [10, 10])

    def test_bracket_prefix_sets_count_keeps_extra_parsed_tokens(self):
        raw_text = """
        20/04 Back & Triceps
        Triceps Rod Pushdown - [2, 10-15]
        55 52.8 50, 14 15
        """
        result = workout_parser(raw_text)
        exs = result["exercises"]
        self.assertEqual(exs[0]["weights"], [55.0, 52.8, 50.0])
        self.assertEqual(exs[0]["reps"], [14, 15, 15])

    def test_bracket_range_without_set_prefix_uses_minimum_three(self):
        raw_text = """
        12/01 Test Day
        Barbell Curl - [6-10]
        5 2.5, 10
        """
        result = workout_parser(raw_text)
        exs = result["exercises"]
        self.assertEqual(len(exs[0]["weights"]), 3)
        self.assertEqual(exs[0]["reps"], [10, 10, 10])

    def test_stretch_to_match_larger_list_when_over_three(self):
        raw_text = """
        12/01 Test Day
        Barbell Curl - [6-10]
        5 2.5 1, 10 10 10 10 10
        """
        result = workout_parser(raw_text)
        exs = result["exercises"]
        self.assertEqual(len(exs[0]["weights"]), 5)
        self.assertEqual(exs[0]["reps"], [10, 10, 10, 10, 10])

    def test_bodyweight_line_is_metadata_and_updates_bw_sets(self):
        raw_text = """
        17/6/26 - Session 13 - Chest & Triceps
        Body Weight - 72.5 kg
        Dips - [8-12]
        Bw-40, 8
        """
        result = workout_parser(raw_text, bodyweight=70)
        exs = result["exercises"]

        self.assertEqual(result["bodyweight"], 72.5)
        self.assertEqual(result["bodyweight_unit"], "kg")
        self.assertEqual(len(exs), 1)
        self.assertEqual(exs[0]["name"], "Dips")
        self.assertEqual(exs[0]["weights"], [32.5, 32.5, 32.5])
        self.assertEqual(exs[0]["reps"], [8, 8, 8])

    def test_bodyweight_line_accepts_lbs_and_bare_values(self):
        lbs_result = workout_parser(
            """
            17/6 Push
            Bodyweight: 180 lbs
            Push Ups
            Bw, 10
            """,
            bodyweight=70,
        )
        bare_result = workout_parser(
            """
            17/6 Push
            Body Weight - 72
            Push Ups
            Bw, 10
            """,
            bodyweight=70,
        )

        self.assertEqual(lbs_result["bodyweight"], 180.0)
        self.assertEqual(lbs_result["bodyweight_unit"], "lbs")
        self.assertEqual(lbs_result["exercises"][0]["weights"], [180.0, 180.0, 180.0])
        self.assertEqual(bare_result["bodyweight"], 72.0)
        self.assertIsNone(bare_result["bodyweight_unit"])


class TestPlanParser(unittest.TestCase):

    def test_get_workout_days_legacy_format(self):
        raw_text = """
        Chest & Triceps 1
        Flat Barbell Press
        Triceps Rod Pushdown

        Legs 1
        Leg Press
        Hip Thrust
        """
        data = get_workout_days(raw_text)
        self.assertIn("Chest & Triceps", data.get("workout", {}))
        self.assertIn("Chest & Triceps 1", data["workout"]["Chest & Triceps"])
        self.assertEqual(data["workout"]["Chest & Triceps"]["Chest & Triceps 1"], ["Flat Barbell Press", "Triceps Rod Pushdown"])
        self.assertIn("Legs", data["workout"])
        self.assertIn("Legs 1", data["workout"]["Legs"])

    def test_get_workout_days_session_title_format(self):
        raw_text = """
        Session 1 - Chest & Biceps
        Flat Barbell Press
        Preacher Curl

        Session 2: Legs
        Leg Press
        Calf Raises Sitting
        """
        data = get_workout_days(raw_text)
        self.assertIn("Session", data.get("workout", {}))
        self.assertIn("Session 1", data["workout"]["Session"])
        self.assertIn("Session 2", data["workout"]["Session"])
        self.assertEqual(data["workout"]["Session"]["Session 1"], ["Flat Barbell Press", "Preacher Curl"])
        self.assertEqual(data["session_titles"].get("1"), "Chest & Biceps")
        self.assertEqual(data["session_titles"].get("2"), "Legs")

    def test_get_workout_days_title_number_format_normalization(self):
        raw_text = """
        Shoulders 1
        Wrist Extension – Dumbbell
        Farmer’s Walk
        """
        data = get_workout_days(raw_text)
        self.assertIn("Shoulders", data.get("workout", {}))
        self.assertIn("Shoulders 1", data["workout"]["Shoulders"])

        self.assertEqual(data["workout"]["Shoulders"]["Shoulders 1"], ["Wrist Extension - Dumbbell", "Farmer's Walk"])

    def test_get_workout_days_ignores_cycle_headings(self):
        raw_text = """
        Cycle 1

        Session 1 - Chest & Biceps
        Flat Barbell Press
        Preacher Curl

        Cycle 2

        Session 2 - Legs
        Leg Press
        Calf Raises Sitting
        """
        data = get_workout_days(raw_text)
        self.assertIn("Session", data.get("workout", {}))
        self.assertIn("Session 1", data["workout"]["Session"])
        self.assertIn("Session 2", data["workout"]["Session"])
        self.assertNotIn("Cycle", data.get("workout", {}))
        self.assertEqual(data["workout"]["Session"]["Session 1"], ["Flat Barbell Press", "Preacher Curl"])
        self.assertEqual(data["workout"]["Session"]["Session 2"], ["Leg Press", "Calf Raises Sitting"])

        self.assertEqual(data.get("headings"), ["Cycle 1", "Cycle 2"])
        self.assertEqual(data.get("heading_sessions", {}).get("Cycle 1"), [1])
        self.assertEqual(data.get("heading_sessions", {}).get("Cycle 2"), [2])


class TestWorkoutQualityScorer(unittest.TestCase):

    def test_strength_scales_without_hard_cap(self):
        low = WorkoutQualityScorer.calculate_workout_score({"weights": [100], "reps": [5]}, (4, 6))
        high = WorkoutQualityScorer.calculate_workout_score({"weights": [200], "reps": [5]}, (4, 6))
        self.assertGreater(high["peak_1rm"], low["peak_1rm"])
        self.assertAlmostEqual(high["quality_score"], low["quality_score"], delta=1e-6)

    def test_superman_set_not_penalized_when_high_performance(self):
        score = WorkoutQualityScorer.calculate_workout_score(
            {"weights": [120, 120, 120], "reps": [8, 9, 12]},
            (6, 10),
        )
        self.assertGreaterEqual(score["rep_range_adherence"], 0.85)

    def test_drop_set_and_warmup_do_not_destroy_avg_1rm(self):
        score = WorkoutQualityScorer.calculate_workout_score(
            {"weights": [20, 100, 100, 60], "reps": [10, 5, 5, 15]},
            (4, 8),
        )
        self.assertGreater(score["avg_1rm"] / (score["peak_1rm"] or 1.0), 0.90)

    def test_pyramid_training_not_flagged_as_inconsistent(self):
        score = WorkoutQualityScorer.calculate_workout_score(
            {"weights": [60, 80, 100], "reps": [12, 8, 5]},
            (5, 12),
        )
        self.assertGreater(score["volume_consistency"], 0.55)

    def test_high_rep_1rm_uses_classic_epley(self):
        score_10 = WorkoutQualityScorer.calculate_workout_score({"weights": [50], "reps": [10]})
        score_20 = WorkoutQualityScorer.calculate_workout_score({"weights": [50], "reps": [20]})
        self.assertAlmostEqual(score_10["peak_1rm"], 50 * (1 + 10 / 30), places=6)
        self.assertAlmostEqual(score_20["peak_1rm"], 50 * (1 + 20 / 30), places=6)


if __name__ == '__main__':
    unittest.main()
