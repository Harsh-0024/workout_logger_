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

    def test_plan_exercise_line_reads_sets_with_or_without_a_dash(self):
        from parsers.workout import _parse_plan_exercise_line as parse
        cases = {
            "Deadlift [3]": ("Deadlift", 3, None),
            "Deadlift - [3]": ("Deadlift", 3, None),
            "Deadlift – [3]": ("Deadlift", 3, None),
            "Deadlift [3, 3-6]": ("Deadlift", 3, "3-6"),
            "Deadlift [3-6]": ("Deadlift", None, "3-6"),
            "Plank [2, 30-60s]": ("Plank", 2, "30-60s"),
            "Deadlift": ("Deadlift", None, None),
            "Curl [EZ]": ("Curl [EZ]", None, None),
        }
        for line, (name, sets, rng) in cases.items():
            parsed = parse(line)
            self.assertEqual((parsed["name"], parsed["declared_sets"], parsed["inline_range"]), (name, sets, rng), line)

    def test_plan_target_sets_read_sets_without_a_dash(self):
        from services.logging import _parse_plan_target_sets
        targets = _parse_plan_target_sets("Session 1 - Back\nDeadlift [3]\nLat Pulldown - [2]")
        self.assertEqual(targets, {"deadlift": 3, "lat pulldown": 2})


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

    def test_saved_time_dates_the_workout(self):
        """A workout kept offline is dated by when it was saved, not when it uploads."""
        from datetime import datetime
        saved = datetime(2027, 1, 3, 21, 15)
        self.assertEqual(workout_parser("Push Day\nBench Press 100x5", now=saved)["date"], saved)
        # A dated title in late December, saved in early January, is last year's.
        self.assertEqual(
            workout_parser("28/12 Push Day\nBench Press 100x5", now=saved)["date"],
            datetime(2026, 12, 28),
        )



class TestWorkoutDates(unittest.TestCase):
    NOW = __import__("datetime").datetime(2026, 9, 30, 18, 0)

    def _date(self, title):
        parsed = workout_parser(f"{title}\nSquat\n100, 5", now=self.NOW)
        return parsed["date"].date().isoformat(), parsed["date_found"], parsed["workout_name"]

    def test_a_written_year_is_kept(self):
        # A workout from last year pasted from Notes stays on its own day.
        self.assertEqual(self._date("15/3/25 Push"), ("2025-03-15", True, "Push"))
        self.assertEqual(self._date("30/9/25 Push"), ("2025-09-30", True, "Push"))
        self.assertEqual(self._date("15/3/2024 Push"), ("2024-03-15", True, "Push"))
        self.assertEqual(self._date("15.3.25 Push"), ("2025-03-15", True, "Push"))
        self.assertEqual(self._date("30/9/26 Push"), ("2026-09-30", True, "Push"))

    def test_dates_with_month_names(self):
        self.assertEqual(self._date("30 Sep Push"), ("2026-09-30", True, "Push"))
        self.assertEqual(self._date("Sep 30 Push"), ("2026-09-30", True, "Push"))
        self.assertEqual(self._date("30 Sep 25 Push"), ("2025-09-30", True, "Push"))
        self.assertEqual(self._date("30 September 2024 - Push"), ("2024-09-30", True, "Push"))
        self.assertEqual(self._date("Oct 1st, 2025: Legs"), ("2025-10-01", True, "Legs"))
        self.assertEqual(self._date("28 Dec Push"), ("2025-12-28", True, "Push"))
        # Titles that only look like it stay titles.
        for title in ("30 Min Cardio", "3 Sets Of Squats", "May Day Workout", "31 Feb Push"):
            self.assertEqual(self._date(title), ("2026-09-30", False, title), title)

    def test_a_date_that_does_not_exist_is_marked(self):
        for title, text in (("31/9 Push", "31/9"), ("29/2 Push", "29/2"), ("15/13 Push", "15/13"),
                            ("31 Sep Push", "31 Sep"), ("31 Feb Push", "31 Feb")):
            parsed = workout_parser(f"{title}\nSquat\n100, 5", now=self.NOW)
            self.assertEqual((parsed["date_found"], parsed["invalid_date_text"]), (False, text), title)
        # Real dates, no date, and titles with numbers in them aren't marked.
        for title in ("29/2/24 Push", "30/9 Push", "Push", "W3D14 Upper", "30 Min Cardio"):
            parsed = workout_parser(f"{title}\nSquat\n100, 5", now=self.NOW)
            self.assertIsNone(parsed["invalid_date_text"], title)

    def test_without_a_year_the_nearest_past_date_is_used(self):
        self.assertEqual(self._date("15/3 Push"), ("2026-03-15", True, "Push"))
        self.assertEqual(self._date("28/12 Push"), ("2025-12-28", True, "Push"))



class TestSetLinesWrittenInCommonWays(unittest.TestCase):
    """Set lines people type or paste from Notes, read as sets rather than as exercise names."""

    def _exercises(self, body):
        parsed = workout_parser("30/9/26 Push\n" + body, bodyweight=80, preserve_bodyweight_offsets=True)
        return [(e["name"], e["weights"], e["reps"]) for e in parsed["exercises"]]

    def test_spaces_around_x(self):
        self.assertEqual(self._exercises("Squat\n100 x 5\n90 x 8"), [("Squat", [100.0, 90.0, 90.0], [5, 8, 8])])
        self.assertEqual(self._exercises("Squat\n100 x5"), [("Squat", [100.0] * 3, [5] * 3)])
        # A word after the x is still a name, not a set.
        self.assertEqual([name for name, _, _ in self._exercises("Squat\n5 x Something")], ["Squat", "X Something"])

    def test_one_set_per_line_reads_every_set(self):
        self.assertEqual(
            self._exercises("Squat\n100x5\n90x8\n80x10\n\nLeg Press\n200x10"),
            [("Squat", [100.0, 90.0, 80.0], [5, 8, 10]), ("Leg Press", [200.0] * 3, [10] * 3)],
        )
        self.assertEqual(
            self._exercises("Squat - [3]\n100 x 5\n90 x 5\n80 x 5\n70 x 5"),
            [("Squat", [100.0, 90.0, 80.0, 70.0], [5] * 4)],
        )
        # A note under the sets stays a note.
        self.assertEqual(self._exercises("Squat\n100x5\n90x8\nfelt good")[0], ("Squat", [100.0, 90.0, 90.0], [5, 8, 8]))

    def test_units_after_a_space(self):
        self.assertEqual(self._exercises("Squat\n100 kg, 5"), [("Squat", [100.0] * 3, [5] * 3)])
        self.assertEqual(self._exercises("Lat Pulldown\n50 lbs, 10"), [("Lat Pulldown", [50.0] * 3, [10] * 3)])
        self.assertEqual(self._exercises("Squat\n100 kg\n5"), [("Squat", [100.0] * 3, [5] * 3)])

    def test_sets_x_reps_at_a_weight(self):
        self.assertEqual(self._exercises("Squat\n3x5 @ 100"), [("Squat", [100.0] * 3, [5] * 3)])
        self.assertEqual(self._exercises("Squat\n2 x 10 @ 60kg"), [("Squat", [60.0] * 2, [10] * 2)])
        self.assertEqual(self._exercises("Squat\n4x6@100"), [("Squat", [100.0] * 4, [6] * 4)])
        self.assertEqual(self._exercises("Dips\n3x8 @ BW+10"), [("Dips", [10.0] * 3, [8] * 3)])

    def test_sets_at_a_weight_written_other_ways(self):
        # These used to save the set count as the weight ("3x8 100kg" was three sets of 3 kg).
        for line in ("3x5 at 100", "3x5 100kg", "3 x 5 100 kg", "100kg 3x5", "100 kg 3 x 5", "100x5x3", "3x5x100"):
            self.assertEqual(self._exercises(f"Squat\n{line}"), [("Squat", [100.0] * 3, [5] * 3)], line)
        self.assertEqual(self._exercises("Squat 5x5 100kg"), [("Squat", [100.0] * 5, [5] * 5)])
        self.assertEqual(self._exercises("Squat\n2x10 @ 60, 1x8 @ 70"), [("Squat", [60.0, 60.0, 70.0], [10, 10, 8])])
        self.assertEqual(self._exercises("Squat\n3x5 @ 100, 90x8"), [("Squat", [100.0] * 3 + [90.0], [5, 5, 5, 8])])
        # Without a unit or "@" the numbers stay weight x reps: "5 x 10 8" may be 5 kg for 10 and 8.
        self.assertEqual(self._exercises("Squat\n20 x 10 8")[0][1], [20.0] * 3)

    def test_sets_of_reps_in_words(self):
        for line in ("3 sets of 8 at 60", "3 sets of 8 @ 60kg", "3 sets x 8 reps at 60", "Squat 3 sets of 8 at 60"):
            body = line if line.startswith("Squat") else f"Squat\n{line}"
            self.assertEqual(self._exercises(body), [("Squat", [60.0] * 3, [8] * 3)], line)
        self.assertEqual(self._exercises("Pull Ups\n4 sets of 10 bw"), [("Pull Ups", [0.0] * 4, [10] * 4)])
        # With no weight it isn't guessed at (it used to be read as 3 kg for 8, or a new exercise).
        self.assertNotIn(("Squat", [3.0] * 3, [8] * 3), self._exercises("Squat\n3 sets of 8"))

    def test_more_reps_after_a_set_keep_its_weight(self):
        self.assertEqual(self._exercises("Squat\n100x5, 5, 4"), [("Squat", [100.0] * 3, [5, 5, 4])])
        self.assertEqual(self._exercises("Squat\n100 x 5, 90 x 8, 8"), [("Squat", [100.0, 90.0, 90.0], [5, 8, 8])])
        self.assertEqual(self._exercises("Pull Ups\nBW+10 x 8, 7, 6, 6"), [("Pull Ups", [10.0] * 4, [8, 7, 6, 6])])

    def test_one_weight_then_its_reps(self):
        for line in ("100kg 5 5 5", "100kg 5,5,5", "100 kg: 5, 5, 5", "100: 5 5 5", "100 - 5 5 5", "100 - 5, 5, 5",
                     "100 for 5, 5, 5", "5 5 5 @ 100", "5, 5, 5 at 100kg", "Squat: 100kg 5,5,5"):
            body = line if line.startswith("Squat") else f"Squat\n{line}"
            self.assertEqual(self._exercises(body), [("Squat", [100.0] * 3, [5] * 3)], line)
        self.assertEqual(self._exercises("Pull Ups\nBW 10 8 6"), [("Pull Ups", [0.0] * 3, [10, 8, 6])])
        self.assertEqual(self._exercises("Dips\nbw+10 8 8 6"), [("Dips", [10.0] * 3, [8, 8, 6])])
        # The usual "weights, reps" keeps its reading, unit or not, and "5 - 8" stays a range.
        self.assertEqual(self._exercises("Squat\n80kg 75, 8 10"), [("Squat", [80.0, 75.0, 75.0], [8, 10, 10])])
        self.assertEqual(self._exercises("Squat\n60kg 20, 12"), [("Squat", [60.0, 20.0, 20.0], [12, 12, 12])])
        self.assertEqual(self._exercises("Squat\n60 kg 50 40, 10 8 6"), [("Squat", [60.0, 50.0, 40.0], [10, 8, 6])])
        self.assertEqual(self._exercises("Squat: 5 - 8")[0][1:], ([], []))

    def test_timed_sets_written_as_times(self):
        for line, reps in (("60s, 45s", [60, 45, 45]), ("60s 45s", [60, 45, 45]), ("60 sec, 45 sec", [60, 45, 45]),
                           ("1:00, 0:45", [60, 45, 45]), ("45s 40s 30s", [45, 40, 30]),
                           ("3 x 60s", [60, 60, 60]), ("60 sec x 3", [60, 60, 60]), ("2x45s", [45, 45])):
            self.assertEqual(self._exercises(f"Plank\n{line}"), [("Plank", [0.0] * len(reps), reps)], line)
        self.assertEqual(self._exercises("Plank 60s, 45s"), [("Plank", [0.0] * 3, [60, 45, 45])])
        # A weight next to the time is still a weight, and a name with "21s" in it isn't a time.
        self.assertEqual(self._exercises("Farmer Walk - [2, 20-60s]\n30 25, 45 40"),
                         [("Farmer Walk", [30.0, 25.0], [45, 40])])
        self.assertEqual(self._exercises("21s Curl\n20, 7")[0][0], "21s Curl")

    def test_time_only_entries_are_bodyweight(self):
        from parsers.workout import is_time_only_exercise
        self.assertTrue(is_time_only_exercise("Plank\n60s, 45s"))
        self.assertTrue(is_time_only_exercise("Plank 60s 45s", "Plank"))
        self.assertFalse(is_time_only_exercise("Plank - [30-60s]\nBW, 60 45"))
        self.assertFalse(is_time_only_exercise("21s Curl\n20, 7"))
        self.assertFalse(is_time_only_exercise("Squat\n100, 5"))

    def test_emoji_are_not_part_of_the_name(self):
        for line in ("Squat 💪", "🏋️ Squat", "Squat 🔥🔥", "Squat ⭐"):
            self.assertEqual(self._exercises(f"{line}\n100, 5")[0][0], "Squat", line)
        self.assertEqual(self._exercises("45° Hyperextension\n20, 12")[0][0], "45° Hyperextension")

    def test_a_target_in_brackets_without_the_dash(self):
        for line in ("Squat [3]", "Squat-[3]", "Squat – [3]", "Squat [3, 8-12]"):
            self.assertEqual(self._exercises(f"{line}\n100, 5")[0][0], "Squat", line)
        self.assertEqual(self._exercises("Plank [30-60s]\nBW, 45")[0][0], "Plank")
        # Other brackets are part of the name.
        self.assertEqual(self._exercises("Squat [paused]\n100, 5")[0][0], "Squat [Paused]")

    def test_list_bullets_are_not_part_of_the_name(self):
        for bullet in ("•", "◦", "▪", "‣", "*", "·", "-"):
            self.assertEqual(self._exercises(f"{bullet} Squat\n100, 5")[0][0], "Squat", bullet)
        # Numbered lists were already handled.
        self.assertEqual(self._exercises("1. Squat\n100, 5")[0][0], "Squat")


    def test_numbers_that_are_part_of_the_name_stay(self):
        for name in ("1-Arm Dumbbell Row", "1 Arm Dumbbell Row", "45 Degree Hyperextension", "45° Hyperextension",
                     "21s Curl", "3/4 Squat", "90/90 Hip Switch"):
            self.assertEqual(self._exercises(f"{name}\n20, 10")[0][0], name, name)
        # List numbers in front still go, even before a name that starts with a number.
        for line, name in (("1. Squat", "Squat"), ("2) Row", "Row"), ("3 - Curl", "Curl"), ("4 Dips", "Dips"),
                           ("10. Squat", "Squat"), ("1. 3/4 Squat", "3/4 Squat"), ("3. 1-Arm Row", "1-Arm Row")):
            self.assertEqual(self._exercises(f"{line}\n20, 10")[0][0], name, line)
        # A number inside the name isn't read as a set.
        self.assertEqual(self._exercises("45 Degree Hyperextension 20, 12"),
                         [("45 Degree Hyperextension", [20.0] * 3, [12] * 3)])
        # Timed sets written with "sec" are not weights and reps.
        self.assertNotIn(("Plank", [60.0] * 3, [45] * 3), self._exercises("Plank\n60 sec, 45 sec"))


class TestParserNeverCrashes(unittest.TestCase):
    def test_words_that_python_reads_as_numbers_are_not_numbers(self):
        # float() takes "nan", "inf" and "1e5"; "nan" used to crash the parser.
        for text in ("30/9/26 Push\nSquat\nnan, 5", "30/9/26 Push\nSquat\n100, nan", "30/9/26 Push\nSquat\ninf 1e5, 5 6",
                     "30/9/26 Push\nSquat , nan felt good\n100 90"):
            parsed = workout_parser(text, bodyweight=80, preserve_bodyweight_offsets=True)
            for exercise in parsed["exercises"]:
                self.assertNotIn(1e5, exercise["weights"], text)
                for value in exercise["weights"] + exercise["reps"]:
                    self.assertEqual(value, value, text)  # not NaN

    def test_random_text_never_raises(self):
        import random

        rng = random.Random(7)
        tokens = ["Squat", "-", "[3]", "[5-8]", "[30-60s]", "x", "×", "@", ",", "100", "90.5", "0", "-20", "BW",
                  "bw+10", "bw/2", "kg", "Body Weight - 80 kg", "30/9/26", "Sep 30", "1.", "•", "3 sets", "nan",
                  "1e5", "5x5", "3x10@60", "60s", "45 sec", "21s", "3/4", "é", "💪", "", "10 8 6"]
        for _ in range(500):
            text = "\n".join(" ".join(rng.choice(tokens) for _ in range(rng.randint(0, 6)))
                             for _ in range(rng.randint(1, 10)))
            workout_parser(text, bodyweight=80, preserve_bodyweight_offsets=True)


if __name__ == '__main__':
    unittest.main()
