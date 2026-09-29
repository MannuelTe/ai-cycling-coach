"""Offline tests. No API key or network needed: python3 -m unittest"""
import os, tempfile, unittest
from pathlib import Path
from unittest import mock

import check_plan
import intervals_common
import roster
from best_efforts import best_window
from intervals_common import blank_steps


class LactateCurve(unittest.TestCase):
    CURVE = [(200, 130), (300, 160), (400, 185)]

    def test_interpolates_between_points(self):
        self.assertAlmostEqual(check_plan.predicted_hr(250, self.CURVE), 145)

    def test_clamps_above_top_point(self):
        self.assertEqual(check_plan.predicted_hr(500, self.CURVE), 185)

    def test_below_first_point_scales_from_rest(self):
        hr = check_plan.predicted_hr(100, self.CURVE)
        self.assertTrue(60 < hr < 130)

    def test_no_curve_means_no_prediction(self):
        self.assertIsNone(check_plan.predicted_hr(250, []))

    def test_missing_csv_returns_empty(self):
        self.assertEqual(check_plan.load_curve("/nonexistent/lactate_curve.csv"), [])

    def test_csv_is_read_and_sorted(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "curve.csv"
            p.write_text("watts,hr\n300,160\n200,130\n")
            self.assertEqual(check_plan.load_curve(p), [(200, 130), (300, 160)])


class BestEfforts(unittest.TestCase):
    def test_finds_the_hardest_window(self):
        watts = [100] * 60 + [400] * 30 + [100] * 60
        hr = [120] * 60 + [170] * 30 + [120] * 60
        w, h, start = best_window(watts, hr, 30)
        self.assertEqual((w, h, start), (400, 170, 60))

    def test_window_longer_than_ride(self):
        self.assertIsNone(best_window([200] * 10, [], 60))

    def test_gaps_count_as_zero_watts_and_hr_is_optional(self):
        w, h, _ = best_window([300, None, 300, 300], None, 2)
        self.assertEqual(w, 300)
        self.assertIsNone(h)


class Duplicates(unittest.TestCase):
    def test_flags_same_duration_and_load(self):
        a = {"moving_time": 3600, "icu_training_load": 60}
        b = {"moving_time": 3630, "icu_training_load": 62}
        self.assertEqual(len(check_plan.possible_duplicates([a, b])), 1)

    def test_ignores_different_rides(self):
        a = {"moving_time": 3600, "icu_training_load": 60}
        b = {"moving_time": 5400, "icu_training_load": 90}
        self.assertEqual(check_plan.possible_duplicates([a, b]), [])


class WorkoutSteps(unittest.TestCase):
    def test_blank_step_is_caught_inside_repeats(self):
        event = {"workout_doc": {"steps": [
            {"duration": 600, "power": {"value": 200}},
            {"reps": 3, "steps": [{"duration": 60, "power": {"value": 400}},
                                  {"duration": 60}]},
        ]}}
        self.assertEqual(len(blank_steps(event)), 1)

    def test_all_steps_have_targets(self):
        event = {"workout_doc": {"steps": [{"duration": 600, "power": {"value": 200}}]}}
        self.assertEqual(blank_steps(event), [])


class CoachMode(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.roster = Path(d.name) / "roster.csv"
        self.roster.write_text("name,athlete_id\nanna,i111\nben,i222\n")
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(intervals_common, "ROSTER", self.roster).start()
        mock.patch.dict(os.environ, {"INTERVALS_ATHLETE_ID": "i999"}).start()
        os.environ.pop("ATHLETE", None)

    def test_without_athlete_everything_is_single_athlete(self):
        self.assertEqual(intervals_common.athlete_id(), "i999")
        self.assertEqual(intervals_common.athlete_dir().name, "athlete")

    def test_athlete_picks_id_and_folder_from_roster(self):
        os.environ["ATHLETE"] = "ben"
        self.assertEqual(intervals_common.athlete_id(), "i222")
        self.assertEqual(intervals_common.athlete_dir().parts[-2:], ("athletes", "ben"))

    def test_unknown_athlete_stops(self):
        os.environ["ATHLETE"] = "carla"
        with self.assertRaises(SystemExit):
            intervals_common.athlete_id()

    def test_every_roster_id_is_redacted(self):
        text = intervals_common._redact("/athlete/i111/events and /athlete/i222 and i999")
        self.assertNotIn("i111", text)
        self.assertNotIn("i222", text)
        self.assertNotIn("i999", text)


class AthleteNotes(unittest.TestCase):
    def test_rpe_feel_and_description(self):
        a = {"icu_rpe": 7, "feel": 2, "description": "legs heavy,\n  windy"}
        self.assertEqual(check_plan.athlete_notes(a), 'RPE 7/10  feel good  "legs heavy, windy"')

    def test_nothing_written(self):
        self.assertEqual(check_plan.athlete_notes({}), "")

    def test_checkin_notes_keep_only_checkins(self):
        notes = [{"name": "Check-in", "start_date_local": "2026-10-01T00:00:00",
                  "description": "legs ok,\n slept badly"},
                 {"name": "check-in ", "start_date_local": "2026-10-04T00:00:00"},
                 {"name": "Holiday", "start_date_local": "2026-10-02T00:00:00"}]
        self.assertEqual(check_plan.checkin_notes(notes),
                         {"2026-10-01": "legs ok, slept badly", "2026-10-04": ""})


class Roster(unittest.TestCase):
    def test_template_counts_as_blank(self):
        self.assertTrue(roster.is_blank_profile("## Who\n- Name / location:\n- Goals:\n"))

    def test_filled_profile_is_not_blank(self):
        self.assertFalse(roster.is_blank_profile("## Who\n- Name / location: Anna, Zurich\n"))

    def test_shipped_template_is_blank(self):
        text = (intervals_common.ROOT / "athlete" / "ATHLETE.md").read_text()
        self.assertTrue(roster.is_blank_profile(text))


if __name__ == "__main__":
    unittest.main()
