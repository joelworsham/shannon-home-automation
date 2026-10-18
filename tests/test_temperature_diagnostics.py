import unittest
from datetime import datetime, timedelta, timezone

from temperature_diagnostics import ThermalSample, check_temperature_trend

T = datetime(2026, 1, 15, 23, 0, tzinfo=timezone.utc)


def samples(temps, *, outside=30, hallway=69, gap=5):
    return [ThermalSample(T + timedelta(minutes=gap*i), temp, outside, hallway)
            for i, temp in enumerate(temps)]


class ThermalDiagnosticsTests(unittest.TestCase):
    def test_exhaust_cooling_warning_no_direction_assumption(self):
        alerts = check_temperature_trend(samples([55, 54.6, 54.2, 53.8, 53.4]),
                                         confirmed_running=True, verified_direction='exhaust')
        self.assertEqual([a.code for a in alerts], ['cooling_during_exhaust'])

    def test_intake_warming_warning(self):
        alerts = check_temperature_trend(samples([64, 64.4, 64.5, 65]),
                                         confirmed_running=True, verified_direction='intake')
        self.assertEqual([a.code for a in alerts], ['warming_during_intake'])

    def test_rapid_cooling_verified_off(self):
        alerts = check_temperature_trend(samples([62, 61.6, 61.2, 60.8, 60.3, 60, 59.5]),
                                         confirmed_running=False, verified_direction=None)
        self.assertEqual([a.code for a in alerts], ['rapid_cooling_while_off'])

    def test_unknown_power_suppresses_alert(self):
        self.assertEqual(check_temperature_trend(samples([55,54,53,52,51]),
                                                 confirmed_running=None, verified_direction='exhaust'), [])

    def test_unverified_direction_suppresses_alert(self):
        self.assertEqual(check_temperature_trend(samples([55,54,53,52,51]),
                                                 confirmed_running=True, verified_direction=None), [])

    def test_not_enough_samples_or_window(self):
        self.assertEqual(check_temperature_trend(samples([55,53]),
                                                 confirmed_running=False, verified_direction=None), [])
        self.assertEqual(check_temperature_trend(samples([55,54,53],gap=2),
                                                 confirmed_running=True, verified_direction='intake'), [])

    def test_discontinuous_or_reordered_samples_suppressed(self):
        self.assertEqual(check_temperature_trend(samples([55,54,53],gap=15),
                                                 confirmed_running=False, verified_direction=None), [])
        duplicate = [ThermalSample(T, 55, 30), ThermalSample(T, 54, 30), ThermalSample(T, 53, 30)]
        self.assertEqual(check_temperature_trend(duplicate, confirmed_running=False, verified_direction=None), [])

    def test_outdoor_not_advantageous_suppresses_intake_alert(self):
        self.assertEqual(check_temperature_trend(samples([60,60.4,60.7,61],outside=58),
                                                 confirmed_running=True, verified_direction='intake'), [])

    def test_hallway_not_reliably_warmer_suppresses_exhaust_alert(self):
        self.assertEqual(check_temperature_trend(samples([55,54.5,54,53.5],hallway=55),
                                                 confirmed_running=True, verified_direction='exhaust'), [])


if __name__ == '__main__':
    unittest.main()
