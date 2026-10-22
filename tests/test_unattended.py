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

    def test_commissioned_endpoint_agreement_skips_transmission(self):
        self.assertEqual(self.tick()['status'], 'satisfied')
        self.assertEqual(self.commands, [])

    def test_low_high_transitions_and_next_process_estimate(self):
        for speed in (1, 4):
            result = self.tick(speed)
            self.assertEqual(result['status'], 'endpoint_consistent')
            self.assertEqual(self.actual_speed, speed)
            self.assertIsNone(self.delivery.store.current())
            self.assertEqual(unattended.expected_speed(self.config), speed)
            count = len(self.commands)
            # A new observer/runtime uses only the durable model plus new power.
            self.observer = unattended.CommissionedObserver(self.make_observer(), self.config)
            self.runtime.observer = self.observer
            self.assertEqual(self.tick(speed)['status'], 'satisfied')
            self.assertEqual(len(self.commands), count)
        with database.connect() as db:
            snapshots = db.execute('SELECT verified_direction,verified_speed FROM device_state_snapshots').fetchall()
        self.assertTrue(snapshots)
        self.assertTrue(all(direction is None and speed is None for direction, speed in snapshots))

    def test_endpoint_missed_leaves_pending_and_cannot_repeat(self):
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
        count = len(self.commands)
        self.assertEqual(self.tick(1)['status'], 'blocked')
        self.assertEqual(len(self.commands), count)

    def test_pending_speed_after_restart_cannot_auto_resolve(self):
        attempt = self.delivery.store.claim('set_low', None, self.clock.now())
        self.delivery.store.event(attempt, 'action_completed', self.clock.now())
        self.assertEqual(self.tick(1)['status'], 'blocked')
        self.assertEqual(self.commands, [])
        self.assertIsNotNone(self.delivery.store.current())

    def test_mismatch_latches_fault_without_send(self):
        self.actual_speed = 2
        self.assertEqual(self.tick()['status'], 'blocked')
        self.assertTrue(unattended.COMMISSION_FAULT_FILE.exists())
        self.assertEqual(self.commands, [])

    def test_power_off_allowed_with_speed_mismatch(self):
        self.actual_speed = 2
        result = self.runtime.tick(FanState(), exclusive_control=True)
        self.assertEqual(result['status'], 'power_confirmed')
        self.assertFalse(self.running)

    def test_power_cycle_discards_endpoint_estimate_and_reestablishes(self):
        self.runtime.tick(FanState(), exclusive_control=True)
        self.assertIsNone(unattended.expected_speed(self.config))
        self.assertEqual(self.tick(1)['status'], 'power_confirmed')
        self.assertIsNone(unattended.expected_speed(self.config))
        self.assertEqual(self.tick(1)['status'], 'endpoint_consistent')
        self.assertEqual(self.actual_speed, 1)

    def test_partial_sequence_timeout_never_resolves(self):
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
        self.assertEqual(self.tick(1)['status'], 'blocked')
        self.assertEqual(len(self.commands), count)

    def test_whole_window_must_agree_not_just_final_reading(self):
        self.observer.latest = self.make_observer()()
        # Populate an explicitly synthetic transition window ending in-band.
        self.observer.latest = self.observer.latest.__class__(True, self.clock.now(), watts=25)
        self.observer.raw.samples = [(0, self.clock.now(), True, 26),
                                     (1, self.clock.now(), True, 25),
                                     (2, self.clock.now(), True, 25)]
        self.assertFalse(self.observer.matches(1))

    def test_unattended_gate_and_fault_are_checked_before_building_hardware(self):
        with patch.dict('os.environ', {'UNATTENDED_CONTROL_ENABLED': 'false'}), \
             patch.object(unattended, 'build_runtime') as build:
            with self.assertRaises(PermissionError):
                unattended.run_unattended(FanState(), self.delivery.settings, run_id=1)
            build.assert_not_called()
        unattended.COMMISSION_FAULT_FILE.write_text('{}')
        with patch.dict('os.environ', {'UNATTENDED_CONTROL_ENABLED': 'true'}), \
             patch.object(unattended, 'load_commissioning', return_value=self.config), \
             patch.object(unattended, 'build_runtime') as build:
            with self.assertRaises(unattended.DeliveryBlocked):
                unattended.run_unattended(FanState('intake', 4), self.delivery.settings, run_id=1)
            build.assert_not_called()

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
