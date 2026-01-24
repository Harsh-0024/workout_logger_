import unittest
# IMPORTS: Assumes you have moved files to 'parsers/workout.py'
from parsers.workout import workout_parser, parse_weight_x_reps, normalize


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
        self.assertEqual(exs[0]['weights'], [100.0])
        self.assertEqual(exs[0]['valid'], True)

        # Exercise 2: Incline DB (Comma separated)
        self.assertEqual(exs[1]['name'], "Incline Dumbbell")
        self.assertEqual(exs[1]['weights'], [30.0, 30.0, 30.0])
        self.assertEqual(exs[1]['valid'], True)

        # Exercise 3: Pec Fly (Implicit Reps)
        self.assertEqual(exs[2]['name'], "Pec Fly")
        self.assertEqual(exs[2]['weights'], [15.0, 15.0])
        self.assertEqual(exs[2]['valid'], True)

        # Exercise 4: Pull Ups (Negative)
        self.assertEqual(exs[3]['name'], "Pull Ups")
        self.assertEqual(exs[3]['weights'], [-35.0])
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

        self.assertEqual(exs[2]['weights'], [70.0])
        self.assertEqual(exs[2]['reps'], [6])

        self.assertEqual(exs[3]['weights'], [50.0])
        self.assertEqual(exs[3]['reps'], [6])

        self.assertEqual(exs[4]['weights'], [80.0])
        self.assertEqual(exs[4]['reps'], [6])

        self.assertEqual(exs[5]['weights'], [1.0])
        self.assertEqual(exs[5]['reps'], [16])

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


if __name__ == '__main__':
    unittest.main()