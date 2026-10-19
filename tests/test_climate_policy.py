"""Offline tests: python -m unittest -v test_climate_policy.py"""

import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from climate_policy import FanState, evaluate, schedule_active

TZ = ZoneInfo("America/Indiana/Indianapolis")


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, 18, hour, minute, tzinfo=TZ)


class ClimatePolicyTests(unittest.TestCase):
    def test_start_boundary(self):
        self.assertFalse(schedule_active(at(21, 59)))
        self.assertTrue(schedule_active(at(22, 0)))

    def test_end_boundary(self):
        self.assertTrue(schedule_active(at(7, 29)))
        self.assertFalse(schedule_active(at(7, 30)))

    def test_after_midnight(self):
        self.assertTrue(schedule_active(at(0, 1)))

    def test_timezone_conversion(self):
        self.assertTrue(schedule_active(datetime(2026, 10, 19, 3, 0, tzinfo=timezone.utc)))

    def test_naive_time_rejected(self):
        with self.assertRaises(ValueError):
            schedule_active(datetime(2026, 10, 18, 23, 0))

    def test_start_cooling_above_restart(self):
        d = evaluate(when=at(23), bedroom_f=65, outdoor_f=41, previous=FanState())
        self.assertEqual(d.desired, FanState("intake", 3))

    def test_stay_off_below_restart(self):
        d = evaluate(when=at(23), bedroom_f=63, outdoor_f=41, previous=FanState())
        self.assertEqual(d.desired, FanState())

    def test_keep_running_at_61(self):
        d = evaluate(when=at(23), bedroom_f=61, outdoor_f=41,
                     previous=FanState("intake", 1))
        self.assertEqual(d.desired, FanState("intake", 1))

    def test_stop_at_target(self):
        d = evaluate(when=at(23), bedroom_f=60, outdoor_f=41,
                     previous=FanState("intake", 1))
        self.assertEqual(d.desired, FanState())

    def test_stop_if_outdoor_not_cool_enough(self):
        d = evaluate(when=at(23), bedroom_f=68, outdoor_f=64,
                     previous=FanState("intake", 2))
        self.assertEqual(d.desired, FanState())

    def test_restart_threshold_exactly_64(self):
        d = evaluate(when=at(23), bedroom_f=64, outdoor_f=41)
        self.assertEqual(d.desired, FanState("intake", 3))

    def test_speed_2_when_continuing(self):
        d = evaluate(when=at(23), bedroom_f=62.6, outdoor_f=41,
                     previous=FanState("intake", 1))
        self.assertEqual(d.desired.speed, 2)

    def test_speed_3_initial(self):
        d = evaluate(when=at(23), bedroom_f=65, outdoor_f=41)
        self.assertEqual(d.desired.speed, 3)

    def test_speed_4_initial(self):
        d = evaluate(when=at(23), bedroom_f=69, outdoor_f=41)
        self.assertEqual(d.desired.speed, 4)

    def test_hysteresis_prevents_chatter(self):
        d = evaluate(when=at(23), bedroom_f=62.2, outdoor_f=41,
                     previous=FanState("intake", 1))
        self.assertEqual(d.desired.speed, 1)
        d = evaluate(when=at(23), bedroom_f=62.6, outdoor_f=41,
                     previous=FanState("intake", 1))
        self.assertEqual(d.desired.speed, 2)
        d = evaluate(when=at(23), bedroom_f=61.8, outdoor_f=41,
                     previous=FanState("intake", 2))
        self.assertEqual(d.desired.speed, 2)
        d = evaluate(when=at(23), bedroom_f=61.4, outdoor_f=41,
                     previous=FanState("intake", 2))
        self.assertEqual(d.desired.speed, 1)

    def test_speed_changes_one_step_per_cycle(self):
        d = evaluate(when=at(23), bedroom_f=80, outdoor_f=41,
                     previous=FanState("intake", 1))
        self.assertEqual(d.desired.speed, 2)

    def test_off_outside_schedule(self):
        d = evaluate(when=at(12), bedroom_f=80, outdoor_f=41,
                     previous=FanState("intake", 4))
        self.assertEqual(d.desired, FanState())

    def test_invalid_temperature_rejected(self):
        with self.assertRaises(ValueError):
            evaluate(when=at(23), bedroom_f=float("nan"), outdoor_f=41)


if __name__ == "__main__":
    unittest.main()
