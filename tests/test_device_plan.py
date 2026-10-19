"""Pure offline tests for the future BroadLink/Shelly path."""
import unittest

from climate_policy import FanState
from device_plan import (Observation, PowerCalibration, infer_running, plan_next_command)
from devices.interfaces import FakeBroadLink, FakeShelly
from simulate_hardware import simulate


class HardwarePlanningTests(unittest.TestCase):
    def test_meter_never_switches_outlet(self):
        p = FakeShelly(5.5)
        self.assertEqual(p.read_watts(), 5.5)
        self.assertFalse(hasattr(p, 'turn_off'))
        self.assertFalse(hasattr(p, 'turn_on'))

    def test_unknown_power_does_not_toggle(self):
        p = plan_next_command(FanState('intake', 1), Observation())
        self.assertIsNone(p.command)
        self.assertTrue(p.blocked)

    def test_power_calibration(self):
        c = PowerCalibration(off_max_w=2, on_min_w=8)
        self.assertFalse(infer_running(1, c))
        self.assertIsNone(infer_running(5, c))
        self.assertTrue(infer_running(9, c))
        self.assertIsNone(infer_running(None, c))
        self.assertIsNone(infer_running(float('nan'), c))

    def test_reject_bad_thresholds(self):
        with self.assertRaises(ValueError):
            PowerCalibration(off_max_w=10, on_min_w=9)

    def test_turn_off_when_running(self):
        p = plan_next_command(FanState(), Observation(running=True))
        self.assertEqual(p.command, 'power_toggle')
        self.assertTrue(p.verify_after)

    def test_no_action_when_already_off(self):
        p = plan_next_command(FanState(), Observation(running=False))
        self.assertIsNone(p.command)
        self.assertFalse(p.blocked)

    def test_turn_on_requires_followup(self):
        p = plan_next_command(FanState('intake', 1), Observation(running=False))
        self.assertEqual(p.command, 'power_toggle')
        self.assertTrue(p.verify_after)

    def test_unknown_direction_blocks(self):
        p = plan_next_command(FanState('intake', 1), Observation(running=True, speed=2))
        self.assertTrue(p.blocked)
        self.assertIsNone(p.command)

    def test_direction_toggle(self):
        p = plan_next_command(FanState('intake', 1), Observation(running=True, direction='exhaust', speed=1))
        self.assertEqual(p.command, 'direction_toggle')

    def test_unknown_speed_blocks(self):
        p = plan_next_command(FanState('intake', 1), Observation(running=True, direction='intake'))
        self.assertTrue(p.blocked)

    def test_speed_up_one_step(self):
        p = plan_next_command(FanState('intake', 4), Observation(running=True, direction='intake', speed=1))
        self.assertEqual(p.command, 'speed_up')

    def test_speed_down_one_step(self):
        p = plan_next_command(FanState('intake', 1), Observation(running=True, direction='intake', speed=4))
        self.assertEqual(p.command, 'speed_down')

    def test_unchanged_state(self):
        p = plan_next_command(FanState('intake', 2), Observation(running=True, direction='intake', speed=2))
        self.assertIsNone(p.command)
        self.assertFalse(p.blocked)

    def test_fake_records_and_rejects(self):
        device = FakeBroadLink()
        device.send('speed_up')
        self.assertEqual(device.commands, ['speed_up'])
        with self.assertRaises(ValueError):
            device.send('not_a_remote_button')

    def test_simulation_has_no_real_hardware_side_effects(self):
        output = simulate(bedroom_f=66, outdoor_f=40, at='23:00', watts=1,
                          off_max_w=2, on_min_w=8, direction=None, speed=None)
        self.assertEqual(output['desired'], {'mode': 'intake', 'speed': 1})
        self.assertEqual(output['fake_commands'], ['power_toggle'])
        self.assertFalse(output['hardware_contacted'])

    def test_outside_schedule_simulation(self):
        output = simulate(bedroom_f=72, outdoor_f=40, at='12:00', watts=9,
                          off_max_w=2, on_min_w=8, direction='intake', speed=3)
        self.assertEqual(output['desired'], {'mode': 'off', 'speed': 0})
        self.assertEqual(output['fake_commands'], ['power_toggle'])


if __name__ == '__main__':
    unittest.main()
