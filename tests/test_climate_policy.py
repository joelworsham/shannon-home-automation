"""Offline tests: python -m unittest -v test_climate_policy.py"""

import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from climate_policy import FanState, PolicyConfig, evaluate, schedule_active

TZ = ZoneInfo("America/Indiana/Indianapolis")
FULL_TIERS = PolicyConfig(speed_thresholds_f=(62, 64, 68))


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
        d = evaluate(when=at(23), bedroom_f=65, outdoor_f=41, previous=FanState(), config=FULL_TIERS)
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
        d = evaluate(when=at(23), bedroom_f=64, outdoor_f=41, config=FULL_TIERS)
        self.assertEqual(d.desired, FanState("intake", 3))

    def test_speed_2_when_continuing(self):
        d = evaluate(when=at(23), bedroom_f=62.6, outdoor_f=41,
                     previous=FanState("intake", 1), config=FULL_TIERS)
        self.assertEqual(d.desired.speed, 2)

    def test_speed_3_initial(self):
        d = evaluate(when=at(23), bedroom_f=65, outdoor_f=41, config=FULL_TIERS)
        self.assertEqual(d.desired.speed, 3)

    def test_speed_4_initial(self):
        d = evaluate(when=at(23), bedroom_f=69, outdoor_f=41, config=FULL_TIERS)
        self.assertEqual(d.desired.speed, 4)

    def test_hysteresis_prevents_chatter(self):
        d = evaluate(when=at(23), bedroom_f=62.2, outdoor_f=41,
                     previous=FanState("intake", 1), config=FULL_TIERS)
        self.assertEqual(d.desired.speed, 1)
        d = evaluate(when=at(23), bedroom_f=62.6, outdoor_f=41,
                     previous=FanState("intake", 1), config=FULL_TIERS)
        self.assertEqual(d.desired.speed, 2)
        d = evaluate(when=at(23), bedroom_f=61.8, outdoor_f=41,
                     previous=FanState("intake", 2), config=FULL_TIERS)
        self.assertEqual(d.desired.speed, 2)
        d = evaluate(when=at(23), bedroom_f=61.4, outdoor_f=41,
                     previous=FanState("intake", 2), config=FULL_TIERS)
        self.assertEqual(d.desired.speed, 1)

    def test_speed_changes_one_step_per_cycle(self):
        d = evaluate(when=at(23), bedroom_f=80, outdoor_f=41,
                     previous=FanState("intake", 1), config=FULL_TIERS)
        self.assertEqual(d.desired.speed, 2)


    def test_no_optional_speeds_only_uses_speed_1(self):
        for temperature in (61, 64, 70, 90):
            decision = evaluate(when=at(23), bedroom_f=temperature, outdoor_f=40,
                                previous=FanState("intake", 1))
            self.assertEqual(decision.desired, FanState("intake", 1))

    def test_only_speed_four_until_target(self):
        config = PolicyConfig(speed_thresholds_f=(None, None, 60))
        first = evaluate(when=at(23), bedroom_f=64, outdoor_f=40, config=config)
        self.assertEqual(first.desired, FanState("intake", 4))
        continuing = evaluate(when=at(23), bedroom_f=60.1, outdoor_f=40,
                              previous=FanState("intake", 4), config=config)
        self.assertEqual(continuing.desired, FanState("intake", 4))
        changed_policy = evaluate(when=at(23), bedroom_f=60.1, outdoor_f=40,
                                  previous=FanState("intake", 1), config=config)
        self.assertEqual(changed_policy.desired, FanState("intake", 4))
        stopped = evaluate(when=at(23), bedroom_f=60, outdoor_f=40,
                           previous=FanState("intake", 4), config=config)
        self.assertEqual(stopped.desired, FanState())
        waiting = evaluate(when=at(23), bedroom_f=63, outdoor_f=40, config=config)
        self.assertEqual(waiting.desired, FanState())

    def test_sparse_speed_tiers_skip_disabled_speeds(self):
        config = PolicyConfig(speed_thresholds_f=(None, 64, 68))
        initial = evaluate(when=at(23), bedroom_f=65, outdoor_f=40, config=config)
        self.assertEqual(initial.desired, FanState("intake", 3))
        speed_up = evaluate(when=at(23), bedroom_f=68.6, outdoor_f=40,
                            previous=FanState("intake", 3), config=config)
        self.assertEqual(speed_up.desired, FanState("intake", 4))
        speed_down = evaluate(when=at(23), bedroom_f=63.4, outdoor_f=40,
                              previous=FanState("intake", 3), config=config)
        self.assertEqual(speed_down.desired, FanState("intake", 1))
        disabled_previous = evaluate(when=at(23), bedroom_f=65, outdoor_f=40,
                                     previous=FanState("intake", 2), config=config)
        self.assertEqual(disabled_previous.desired, FanState("intake", 3))

    def test_sparse_thresholds_still_validate_order(self):
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            PolicyConfig(speed_thresholds_f=(None, 75, 70))
        with self.assertRaises(ValueError):
            PolicyConfig(speed_thresholds_f=(float("nan"), None, 68))

    def test_off_outside_schedule(self):
        d = evaluate(when=at(12), bedroom_f=80, outdoor_f=41,
                     previous=FanState("intake", 4))
        self.assertEqual(d.desired, FanState())

    def test_invalid_temperature_rejected(self):
        with self.assertRaises(ValueError):
            evaluate(when=at(23), bedroom_f=float("nan"), outdoor_f=41)


if __name__ == "__main__":
    unittest.main()
