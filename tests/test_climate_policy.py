"""Offline tests: python -m unittest -v test_climate_policy.py"""

import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from climate_policy import FanState, PolicyConfig, evaluate, schedule_active

TZ = ZoneInfo("America/Indiana/Indianapolis")
LOW_HIGH_POLICY = PolicyConfig(high_at_f=68)


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
        d = evaluate(when=at(23), bedroom_f=65, outdoor_f=41, previous=FanState(), config=LOW_HIGH_POLICY)
        self.assertEqual(d.desired, FanState("intake", 1))

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
                     previous=FanState('intake', 4))
        self.assertEqual(d.desired, FanState())

    def test_restart_threshold_exactly_64(self):
        d = evaluate(when=at(23), bedroom_f=64, outdoor_f=41, config=LOW_HIGH_POLICY)
        self.assertEqual(d.desired, FanState("intake", 1))

    def test_initial_low_and_high(self):
        for temp, speed in ((64, 1), (67.9, 1), (68, 4), (80, 4)):
            with self.subTest(temp=temp):
                self.assertEqual(evaluate(when=at(23), bedroom_f=temp, outdoor_f=40,
                                         config=LOW_HIGH_POLICY).desired.speed, speed)

    def test_hysteresis_prevents_chatter(self):
        for previous, temp, expected in ((1, 68.4, 1), (1, 68.5, 4),
                                         (4, 67.5, 4), (4, 67.4, 1)):
            with self.subTest(previous=previous, temp=temp):
                result = evaluate(when=at(23), bedroom_f=temp, outdoor_f=40,
                                  previous=FanState('intake', previous), config=LOW_HIGH_POLICY)
                self.assertEqual(result.desired.speed, expected)

    def test_high_disabled(self):
        for speed in (1, 4):
            result = evaluate(when=at(23), bedroom_f=90, outdoor_f=40,
                              previous=FanState('intake', speed))
            self.assertEqual(result.desired.speed, 1)

    def test_high_until_target(self):
        config = PolicyConfig(high_at_f=60)
        for previous in (FanState(), FanState('intake', 1), FanState('intake', 4)):
            result = evaluate(when=at(23), bedroom_f=64, outdoor_f=40,
                              previous=previous, config=config)
            self.assertEqual(result.desired.speed, 4)
        result = evaluate(when=at(23), bedroom_f=60.1, outdoor_f=40,
                          previous=FanState('intake', 1), config=config)
        self.assertEqual(result.desired.speed, 4)
        self.assertEqual(evaluate(when=at(23), bedroom_f=60, outdoor_f=40,
                                 previous=FanState('intake', 4), config=config).desired, FanState())

    def test_intermediate_speeds_rejected(self):
        for speed in (2, 3):
            with self.assertRaises(ValueError):
                FanState('intake', speed)

    def test_nonfinite_high_threshold_rejected(self):
        with self.assertRaises(ValueError):
            PolicyConfig(high_at_f=float('nan'))

    def test_off_outside_schedule(self):
        d = evaluate(when=at(12), bedroom_f=80, outdoor_f=41,
                     previous=FanState("intake", 4))
        self.assertEqual(d.desired, FanState())

    def test_invalid_temperature_rejected(self):
        with self.assertRaises(ValueError):
            evaluate(when=at(23), bedroom_f=float("nan"), outdoor_f=41)


if __name__ == "__main__":
    unittest.main()
