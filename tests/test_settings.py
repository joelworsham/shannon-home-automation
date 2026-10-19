"""Settings validation and custom schedule regression tests."""
import os
import unittest
from datetime import datetime, time
from unittest.mock import patch
from zoneinfo import ZoneInfo

from climate_policy import FanState, PolicyConfig, evaluate, schedule_active
from settings import load_settings


class SettingsTests(unittest.TestCase):
    def setUp(self):
        # Tests must not inherit the user's real .env climate preferences.
        guard = patch.dict(os.environ, {}, clear=True)
        guard.start()
        self.addCleanup(guard.stop)
        dotenv_mock = patch("settings.load_dotenv")
        dotenv_mock.start()
        self.addCleanup(dotenv_mock.stop)

    def test_default(self):
        for name in ("CLIMATE_TARGET_F", "CLIMATE_RESTART_F", "CLIMATE_SPEED_2_AT_F"):
            os.environ.pop(name, None)
        s = load_settings()
        self.assertEqual((s.policy.target_f, s.policy.restart_f), (60, 64))
        self.assertEqual(s.policy.speed_thresholds_f, (62, 64, 68))
        self.assertEqual(s.policy.schedule_start, time(22))

    def test_env_override(self):
        with patch.dict(os.environ, {
            "CLIMATE_TARGET_F": "58", "CLIMATE_RESTART_F": "62",
            "CLIMATE_SPEED_2_AT_F": "68", "CLIMATE_SPEED_3_AT_F": "71",
            "CLIMATE_SPEED_4_AT_F": "75", "CLIMATE_SCHEDULE_START": "21:30",
            "CLIMATE_SCHEDULE_END": "08:00",
        }):
            config = load_settings().policy
            self.assertEqual(config.target_f, 58)
            self.assertEqual(config.restart_f, 62)
            self.assertEqual(config.schedule_start, time(21, 30))
            self.assertEqual(config.speed_thresholds_f, (68, 71, 75))
            t = datetime(2026, 10, 18, 21, 45, tzinfo=ZoneInfo(config.timezone))
            self.assertTrue(schedule_active(t, config))
            d = evaluate(when=t, bedroom_f=63, outdoor_f=40, config=config)
            self.assertEqual(d.desired, FanState("intake", 1))

    def test_invalid_restart(self):
        with patch.dict(os.environ, {"CLIMATE_TARGET_F": "60", "CLIMATE_RESTART_F": "59"}):
            with self.assertRaisesRegex(ValueError, "RESTART"):
                load_settings()

    def test_nan_rejected(self):
        with patch.dict(os.environ, {"CLIMATE_TARGET_F": "nan"}):
            with self.assertRaises(ValueError):
                load_settings()

    def test_bad_time_rejected(self):
        with patch.dict(os.environ, {"CLIMATE_SCHEDULE_START": "27:20"}):
            with self.assertRaisesRegex(ValueError, "SCHEDULE_START"):
                load_settings()

    def test_bad_speed_threshold_order(self):
        with patch.dict(os.environ, {"CLIMATE_SPEED_2_AT_F": "76", "CLIMATE_SPEED_3_AT_F": "70"}):
            with self.assertRaises(ValueError):
                load_settings()

    def test_daytime_schedule_supported(self):
        policy = PolicyConfig(schedule_start=time(8), schedule_end=time(17))
        t = lambda hour: datetime(2026, 10, 18, hour, tzinfo=ZoneInfo(policy.timezone))
        self.assertFalse(schedule_active(t(7), policy))
        self.assertTrue(schedule_active(t(11), policy))
        self.assertFalse(schedule_active(t(18), policy))

    def test_no_secrets_in_public_snapshot(self):
        snapshot = str(load_settings().as_public_dict())
        self.assertNotIn("FLAIR_CLIENT_ID", snapshot)
        self.assertNotIn("FLAIR_CLIENT_SECRET", snapshot)


if __name__ == '__main__':
    unittest.main()
