"""Offline tests. No API key or network needed: python3 -m unittest"""
import tempfile, unittest
from pathlib import Path

import datetime

import check_plan
import weather
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


class Weather(unittest.TestCase):
    LOC = {"place": "Zurich", "lat": "47.37", "lon": "8.54", "trainer": "yes", "road": "yes",
           "gravel": "no", "work_days": "Mon-Fri", "work_hours": "08:30-17:30"}
    MON = datetime.date(2026, 11, 2)
    SAT = datetime.date(2026, 11, 7)

    def sun(self, day):
        return (datetime.datetime.combine(day, datetime.time(7, 15)),
                datetime.datetime.combine(day, datetime.time(17, 0)))

    def test_location_by_date_range(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "locations.csv"
            p.write_text("from,to,place,lat,lon,trainer,road,gravel,work_days,work_hours\n"
                         "2026-10-23,2026-10-30,Zurich,47.37,8.54,no,yes,no,Mon-Fri,08:30-17:30\n"
                         "2026-10-31,2026-12-21,Singapore,1.35,103.82,yes,yes,no,,\n")
            locs = weather.load_locations(p)
        self.assertEqual(weather.location_on(datetime.date(2026, 10, 30), locs)["place"], "Zurich")
        self.assertEqual(weather.location_on(datetime.date(2026, 10, 31), locs)["place"], "Singapore")
        self.assertIsNone(weather.location_on(datetime.date(2027, 1, 5), locs))

    def test_missing_locations_file(self):
        self.assertEqual(weather.load_locations("/nonexistent/locations.csv"), [])

    def test_available_equipment(self):
        self.assertEqual(weather.available(self.LOC), ["trainer", "road"])

    def test_work_days(self):
        self.assertEqual(weather.parse_work_days("Mon-Fri"), {0, 1, 2, 3, 4})
        self.assertEqual(weather.parse_work_days("mon, Wed,thu"), {0, 2, 3})
        self.assertEqual(weather.parse_work_days("none"), set())
        self.assertIsNone(weather.parse_work_days(""))

    def test_daylight_around_work(self):
        win = weather.daylight_windows(self.MON, *self.sun(self.MON), self.LOC)
        self.assertEqual(win, [("before work", 75), ("after work", 0)])

    def test_daylight_on_day_off(self):
        win = weather.daylight_windows(self.SAT, *self.sun(self.SAT), self.LOC)
        self.assertEqual(win, [("all day", 585)])

    def test_unknown_work_schedule(self):
        loc = dict(self.LOC, work_hours="")
        self.assertIsNone(weather.daylight_windows(self.MON, *self.sun(self.MON), loc))
        self.assertIn("ask when they work", weather.describe(self.MON, loc, self.day()))

    def day(self, **kw):
        sr, ss = self.sun(self.MON)
        w = {"t_min": 6, "t_max": 12, "rain_mm": 0, "rain_prob": 10, "wind": 10, "gusts": 20,
             "sunrise": sr, "sunset": ss}
        w.update(kw)
        return w

    def test_flags(self):
        self.assertEqual(weather.flags(self.day()), [])
        self.assertEqual(weather.flags(self.day(rain_prob=70)), ["WET"])
        self.assertEqual(weather.flags(self.day(rain_mm=3)), ["WET"])
        self.assertEqual(weather.flags(self.day(t_min=1)), ["ICE RISK"])
        self.assertEqual(weather.flags(self.day(gusts=50)), ["WINDY"])

    def test_dark_flag_when_ride_does_not_fit_daylight(self):
        win = [("before work", 75), ("after work", 0)]
        self.assertIn("DARK: lights or trainer", weather.flags(self.day(), 90, win))
        self.assertEqual(weather.flags(self.day(), 60, win), [])

    def test_indoor_session_is_not_flagged(self):
        line = weather.describe(self.MON, self.LOC, self.day(rain_prob=90), 90, indoor=True)
        self.assertNotIn("**", line)

    def test_no_location_asks(self):
        self.assertIn("ask where", weather.describe(self.MON, None, None))


if __name__ == "__main__":
    unittest.main()
