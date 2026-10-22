import json
from pathlib import Path
import unittest
from unittest.mock import patch

import database
import unattended
from climate_policy import FanState
from devices.shelly_local import ShellyPowerStatus
from tests import test_hardware_runtime as runtime_fixture


class UnattendedTests(unittest.TestCase):
    make_observer = runtime_fixture.RuntimeTests.make_observer

    def setUp(self):
        runtime_fixture.RuntimeTests.setUp(self)
        self.running = True
        self.actual_speed = 4
        self.config = {'initial_speed': 4, 'direction': 'intake',
            'commissioned_at_utc': self.clock.now().isoformat(),
            'bands': {'1': [24.5, 25.5], '4': [29.5, 30.5]}}
        p = patch.object(unattended, 'COMMISSION_FAULT_FILE', self.path.parent / 'fault.json')
        p.start()
        self.addCleanup(p.stop)

        def read():
            self.clock.value += .01
            watts = self.watt_values.pop(0) if self.watt_values else (
                {1: 25, 2: 26, 3: 28, 4: 30}[self.actual_speed] if self.running else 0)
            return ShellyPowerStatus(watts, self.relay, 120, .2, self.clock.now().isoformat())
        self.meter.read_status = read

        def send(command):
            self.commands.append((self.clock.value, command))
            awake = self.clock.value < self.awake_until
            self.awake_until = self.clock.value + 6
            if awake:
                if command == 'power_toggle':
                    self.running = not self.running
                elif command == 'speed_up':
                    self.actual_speed = min(4, self.actual_speed + 1)
                elif command == 'speed_down':
                    self.actual_speed = max(1, self.actual_speed - 1)
        self.blaster.send = send
        self.observer = unattended.CommissionedObserver(self.make_observer(), self.config)
        self.runtime = unattended.UnattendedRuntime(self.delivery, self.observer, self.power,
            sleep=self.clock.sleep, utcnow=self.clock.now)

    def tick(self, speed=4):
        return self.runtime.tick(FanState('intake', speed), exclusive_control=True)

    def test_matching_low_and_high_skip_all_transmissions(self):
        for speed in (1, 4):
            self.actual_speed = speed
            for _ in range(2):
                self.assertEqual(self.tick(speed)['status'], 'satisfied')
            self.assertEqual(self.commands, [])

    def test_low_high_transitions_and_next_process_estimate(self):
        for speed in (1, 4):
            result = self.tick(speed)
            self.assertEqual(result['status'], 'endpoint_consistent')
            self.assertEqual(self.actual_speed, speed)
            self.assertIsNone(self.delivery.store.current())
            self.assertEqual(unattended.expected_speed(self.config), speed)
            count = len(self.commands)
            # A new process checks fresh power and skips an unnecessary command.
            self.observer = unattended.CommissionedObserver(self.make_observer(), self.config)
            self.runtime.observer = self.observer
            self.assertEqual(self.tick(speed)['status'], 'satisfied')
            self.assertEqual(len(self.commands), count)
        with database.connect() as db:
            snapshots = db.execute('SELECT verified_direction,verified_speed FROM device_state_snapshots').fetchall()
        self.assertTrue(snapshots)
        self.assertTrue(all(direction is None and speed is None for direction, speed in snapshots))

    def test_endpoint_missed_retries_on_next_evaluation(self):
        original = self.blaster.send
        def miss(command):
            if command == 'speed_down':
                self.commands.append((self.clock.value, command))
                self.awake_until = self.clock.value + 6
            else:
                original(command)
        self.blaster.send = miss
        self.assertEqual(self.tick(1)['status'], 'blocked')
        self.assertIsNotNone(self.delivery.store.current())
        self.assertEqual(len(self.commands), 5)  # no immediate retry loop
        self.blaster.send = original
        self.assertEqual(self.tick(1)['status'], 'endpoint_consistent')
        self.assertEqual(self.actual_speed, 1)
        self.assertIsNone(self.delivery.store.current())

    def test_pending_speed_after_restart_is_reconciled(self):
        self.delivery.store.claim('set_low', None, self.clock.now())
        self.assertEqual(self.tick(1)['status'], 'endpoint_consistent')
        self.assertEqual(self.actual_speed, 1)
        self.assertIsNone(self.delivery.store.current())

    def test_manual_speed_changes_are_corrected_when_power_disagrees(self):
        for speed, target in ((2, 4), (3, 1), (4, 1), (1, 4)):
            self.actual_speed = speed
            self.assertEqual(self.tick(target)['status'], 'endpoint_consistent')
            self.assertEqual(self.actual_speed, target)
            self.assertFalse(unattended.COMMISSION_FAULT_FILE.exists())
        # Speed 2 can look exactly like the commissioned low band.
        self.actual_speed = 2
        self.config['bands']['1'] = [24, 27]
        count = len(self.commands)
        self.assertEqual(self.tick(1)['status'], 'satisfied')
        self.assertEqual(self.actual_speed, 2)  # accepted power-band ambiguity
        self.assertEqual(len(self.commands), count)

    def test_power_off_allowed_with_speed_mismatch(self):
        self.actual_speed = 2
        result = self.runtime.tick(FanState(), exclusive_control=True)
        self.assertEqual(result['status'], 'power_confirmed')
        self.assertFalse(self.running)

    def test_remote_power_off_is_restored_with_speed_in_same_evaluation(self):
        self.running = False
        self.actual_speed = 3
        self.assertEqual(self.tick(1)['status'], 'endpoint_consistent')
        self.assertTrue(self.running)
        self.assertEqual(self.actual_speed, 1)
        self.assertEqual(sum(c == 'power_toggle' for _, c in self.commands), 1)
        self.assertEqual(unattended.expected_speed(self.config), 1)

    def test_power_on_with_matching_speed_does_not_send_speed_sequence(self):
        self.running = False
        self.actual_speed = 1
        self.assertEqual(self.tick(1)['status'], 'satisfied')
        self.assertEqual([c for _, c in self.commands], ['speed_up', 'power_toggle'])
        self.assertTrue(self.running)

    def test_pending_speed_already_satisfied_is_retired_without_send(self):
        self.actual_speed = 1
        self.delivery.store.claim('set_low', None, self.clock.now())
        self.assertEqual(self.tick(1)['status'], 'satisfied')
        self.assertEqual(self.commands, [])
        self.assertIsNone(self.delivery.store.current())

    def test_remote_power_on_is_turned_off(self):
        self.assertEqual(self.runtime.tick(FanState(), exclusive_control=True)['status'], 'power_confirmed')
        self.running = True
        self.assertEqual(self.runtime.tick(FanState(), exclusive_control=True)['status'], 'power_confirmed')
        self.assertFalse(self.running)

    def test_pending_power_replans_from_actual_state_not_old_intent(self):
        for running in (False, True):
            with self.subTest(running=running):
                self.running = running
                self.delivery.store.claim('power_toggle', True, self.clock.now())
                self.commands.clear()
                result = self.runtime.tick(FanState(), exclusive_control=True)
                self.assertIn(result['status'], ('satisfied', 'power_confirmed'))
                self.assertFalse(self.running)
                self.assertEqual(sum(c == 'power_toggle' for _, c in self.commands), int(running))
                self.assertIsNone(self.delivery.store.current())

    def test_pending_direction_is_not_automatically_replayed(self):
        self.delivery.store.claim('direction_toggle', None, self.clock.now())
        self.assertEqual(self.tick()['status'], 'blocked')
        self.assertEqual(self.commands, [])

    def test_unknown_power_preserves_pending_attempt_and_sends_nothing(self):
        attempt = self.delivery.store.claim('power_toggle', True, self.clock.now())
        self.relay = False
        self.assertEqual(self.tick()['status'], 'blocked')
        self.assertEqual(self.delivery.store.current()[0], attempt)
        self.assertEqual(self.commands, [])
        self.relay = True
        self.assertEqual(self.tick()['status'], 'satisfied')

    def test_power_miss_stops_this_run_then_retries(self):
        self.running = False
        original = self.blaster.send
        self.blaster.send = lambda command: self.commands.append((self.clock.value, command))
        self.assertEqual(self.tick(1)['status'], 'blocked')
        self.assertEqual(len(self.commands), 2)
        self.blaster.send = original
        self.assertEqual(self.tick(1)['status'], 'endpoint_consistent')
        self.assertTrue(self.running)
        self.assertEqual(self.actual_speed, 1)

    def test_remote_off_during_start_does_not_cause_second_toggle(self):
        self.running = False
        original = self.delivery._confirm_power
        def confirm_then_remote_off(attempt, observed):
            original(attempt, observed)
            self.running = False
            self.clock.sleep(3)  # discard cached power window
        self.delivery._confirm_power = confirm_then_remote_off
        result = self.tick(1)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(sum(c == 'power_toggle' for _, c in self.commands), 1)
        self.assertIsNone(self.delivery.store.current())

    def test_concurrent_evaluation_cannot_recover_active_attempt(self):
        attempt = self.delivery.store.claim('set_low', None, self.clock.now())
        with self.delivery.store.guard():
            with self.assertRaises(unattended.DeliveryBlocked):
                self.tick(1)
        self.assertEqual(self.delivery.store.current()[0], attempt)
        self.assertEqual(self.commands, [])

    def test_partial_sequence_timeout_recovers_next_evaluation(self):
        original = self.blaster.send
        def fail(command):
            if command == 'speed_down' and sum(c == 'speed_down' for _, c in self.commands) == 1:
                raise TimeoutError('Synthetic dropped acknowledgement')
            original(command)
        self.blaster.send = fail
        with self.assertRaises(TimeoutError):
            self.tick(1)
        self.assertIsNotNone(self.delivery.store.current())
        count = len(self.commands)
        self.blaster.send = original
        self.assertEqual(self.tick(1)['status'], 'endpoint_consistent')
        self.assertGreater(len(self.commands), count)
        self.assertEqual(self.actual_speed, 1)

    def set_power_window(self, values):
        from safety_state import PowerObservation
        self.observer.latest = PowerObservation(True, self.clock.now(), watts=values[-1])
        self.observer.raw.samples = [(i, self.clock.now(), True, w) for i, w in enumerate(values)]

    def test_power_check_tolerates_drift_and_one_outlier(self):
        self.set_power_window([25.8, 50, 25.9])
        self.assertTrue(self.observer.matches(1))
        self.set_power_window([28, 28, 25])
        self.assertFalse(self.observer.matches(1))
        self.set_power_window([28, 28, 28])
        self.assertFalse(self.observer.matches(4))
        self.observer.raw.samples.pop()
        self.assertFalse(self.observer.matches(1))

    def test_power_padding_does_not_bridge_small_endpoint_gap(self):
        self.config['bands'] = {'1': [24, 26], '4': [27, 30]}
        self.set_power_window([26.5, 26.5, 26.5])
        self.assertFalse(self.observer.matches(1))
        self.assertFalse(self.observer.matches(4))

    def test_disabled_runtime_does_not_touch_hardware(self):
        from dataclasses import replace
        self.delivery.settings = replace(self.delivery.settings, enabled=False)
        with patch.object(self.observer.raw, 'meter') as meter:
            with self.assertRaises(PermissionError):
                self.tick()
            meter.read_status.assert_not_called()

    def test_unattended_gate_and_legacy_fault(self):
        with patch.dict('os.environ', {'UNATTENDED_CONTROL_ENABLED': 'false'}), \
             patch.object(unattended, 'build_runtime') as build:
            with self.assertRaises(PermissionError):
                unattended.run_unattended(FanState(), self.delivery.settings, run_id=1)
            build.assert_not_called()
        unattended.COMMISSION_FAULT_FILE.write_text('{}')
        with patch.dict('os.environ', {'UNATTENDED_CONTROL_ENABLED': 'true'}), \
             patch.object(unattended, 'load_commissioning', return_value=self.config), \
             patch.object(unattended, 'build_runtime', return_value=self.runtime), \
             patch.object(unattended.UnattendedRuntime, 'tick', return_value={'status': 'ok'}) as tick:
            self.assertEqual(unattended.run_unattended(FanState('intake', 4),
                self.delivery.settings, run_id=1)['status'], 'ok')
            tick.assert_called_once()

    def test_changed_installation_and_invalid_bands_reject_commissioning(self):
        config = dict(self.config, version=1, exclusive_control=True, fingerprint='original')
        path = self.path.parent / 'commission.json'
        path.write_text(json.dumps(config))
        with patch.object(unattended, 'installation_fingerprint', return_value='changed'):
            with self.assertRaises(ValueError):
                unattended.load_commissioning(path)
        with patch.object(unattended, 'installation_fingerprint', return_value='original'), \
             patch.object(unattended, 'load_power_settings', return_value=self.power):
            self.assertEqual(unattended.load_commissioning(path)['initial_speed'], 4)
            config['bands'] = {'1': [24, 31], '4': [29, 32]}
            path.write_text(json.dumps(config))
            with self.assertRaises(ValueError):
                unattended.load_commissioning(path)


if __name__ == '__main__':
    unittest.main()
