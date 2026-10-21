import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import database
from climate_policy import FanState
from delivery_store import DeliveryBlocked, DeliveryStore
from devices.interfaces import FakeBroadLink
from safety_state import PowerObservation
from settings import DeliverySettings, load_delivery_settings
from wake_delivery import WakeAwareDelivery


class Clock:
    def __init__(self):
        self.value = 0.0
        self.waits = []

    def now(self):
        return datetime(2026, 10, 20, tzinfo=timezone.utc) + timedelta(seconds=self.value)

    def sleep(self, seconds):
        self.waits.append(seconds)
        self.value += seconds


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'history.sqlite3'
        patcher = patch.object(database, 'DB_PATH', self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.clock = Clock()
        self.blaster = FakeBroadLink()
        self.engine = self.make_engine()

    def make_engine(self, **kwargs):
        return WakeAwareDelivery(self.blaster, settings=DeliverySettings(enabled=True),
                                 monotonic=lambda: self.clock.value, sleep=self.clock.sleep,
                                 utcnow=self.clock.now, **kwargs)

    def observation(self, running=True, speed=1, direction='intake'):
        return PowerObservation(running, self.clock.now(), direction, speed)

    def execute_off(self):
        return self.engine.execute(FanState(), self.observation, exclusive_control=True)

    def test_disabled_and_nonexclusive_cannot_send(self):
        with self.assertRaises(PermissionError):
            WakeAwareDelivery(self.blaster).execute(FanState(), self.observation, exclusive_control=True)
        with self.assertRaises(DeliveryBlocked):
            self.engine.execute(FanState(), self.observation)
        self.assertEqual(self.blaster.commands, [])
        self.assertFalse(self.path.exists())

    def test_cold_start_waits_wakes_then_actions_and_logs(self):
        attempt = self.execute_off()
        self.assertEqual(self.clock.waits, [6.25, 1])
        self.assertEqual(self.blaster.commands, ['speed_up', 'power_toggle'])
        self.assertEqual(self.engine.display_estimate, 'assumed_awake')
        with sqlite3.connect(self.path) as conn:
            phases = [r[0] for r in conn.execute('SELECT phase FROM delivery_events ORDER BY id')]
        self.assertEqual(phases, ['planned', 'quiet_wait', 'wake_attempt',
                                 'wake_acknowledged', 'action_attempt', 'action_acknowledged', 'action_completed'])
        self.assertEqual(DeliveryStore().pending(attempt)[0], 'power_toggle')

    def test_pending_survives_restart_and_blocks_other_instances(self):
        self.execute_off()
        with self.assertRaises(DeliveryBlocked):
            self.make_engine().execute(FanState(), self.observation, exclusive_control=True)
        self.assertEqual(self.blaster.commands, ['speed_up', 'power_toggle'])

    def test_matching_later_power_confirmation_then_awake_skips_wake(self):
        attempt = self.execute_off()
        with self.assertRaises(DeliveryBlocked):
            self.engine.confirm_power(attempt, self.observation(False))
        self.clock.sleep(0.1)
        with self.assertRaises(DeliveryBlocked):
            self.engine.confirm_power(attempt, self.observation(True))
        self.engine.confirm_power(attempt, self.observation(False))
        self.engine.execute(FanState('intake', 1), lambda: self.observation(False), exclusive_control=True)
        self.assertEqual(self.blaster.commands, ['speed_up', 'power_toggle', 'power_toggle'])

    def test_expired_window_requires_quiet_period_again(self):
        attempt = self.execute_off()
        self.clock.sleep(0.1)
        self.engine.confirm_power(attempt, self.observation(False))
        self.clock.sleep(6)
        self.engine.execute(FanState('intake', 1), lambda: self.observation(False), exclusive_control=True)
        self.assertEqual(self.blaster.commands, ['speed_up', 'power_toggle', 'speed_up', 'power_toggle'])

    def test_restart_does_not_restore_awake_estimate(self):
        attempt = self.execute_off()
        self.clock.sleep(0.1)
        self.engine.confirm_power(attempt, self.observation(False))
        other = self.make_engine()
        other.execute(FanState('intake', 1), lambda: self.observation(False), exclusive_control=True)
        self.assertEqual(self.clock.waits[-2:], [6.25, 1])

    def test_wake_timeout_blocks_action_and_later_retry(self):
        def fail(command):
            self.blaster.commands.append(command)
            raise TimeoutError('private device address must not be logged')
        self.blaster.send = fail
        with self.assertRaises(TimeoutError):
            self.execute_off()
        with self.assertRaises(DeliveryBlocked):
            self.execute_off()
        self.assertEqual(self.blaster.commands, ['speed_up'])
        self.assertNotIn(b'private device', self.path.read_bytes())
        self.assertEqual(self.engine.display_estimate, 'unknown')

    def test_overslept_wake_delay_never_actions(self):
        def sleep(seconds):
            self.clock.sleep(10 if seconds == 1 else seconds)
        self.engine.sleep = sleep
        with self.assertRaises(DeliveryBlocked):
            self.execute_off()
        self.assertEqual(self.blaster.commands, ['speed_up'])

    def test_action_timeout_remains_pending_without_repeat(self):
        def send(command):
            self.blaster.commands.append(command)
            if command == 'power_toggle':
                raise TimeoutError('ack lost')
        self.blaster.send = send
        with self.assertRaises(TimeoutError):
            self.execute_off()
        with self.assertRaises(DeliveryBlocked):
            self.make_engine().execute(FanState(), self.observation, exclusive_control=True)
        self.assertEqual(self.blaster.commands, ['speed_up', 'power_toggle'])

    def test_activity_during_wait_blocks_send(self):
        def sleep(seconds):
            self.clock.sleep(seconds)
            self.engine.invalidate_display()
        self.engine.sleep = sleep
        with self.assertRaises(DeliveryBlocked):
            self.execute_off()
        self.assertEqual(self.blaster.commands, [])

    def test_observation_change_after_wake_blocks_toggle(self):
        calls = [0]
        def observe():
            calls[0] += 1
            return self.observation(calls[0] < 3)
        with self.assertRaises(DeliveryBlocked):
            self.engine.execute(FanState(), observe, exclusive_control=True)
        self.assertEqual(self.blaster.commands, ['speed_up'])

    def test_stale_observation_blocks_before_any_send(self):
        old = self.observation()
        self.clock.sleep(11)
        with self.assertRaises(DeliveryBlocked):
            self.engine.execute(FanState(), lambda: old, exclusive_control=True)
        self.assertEqual(self.blaster.commands, [])

    def test_unknown_speed_establishes_high_endpoint(self):
        self.engine.execute(FanState('intake', 4), lambda: self.observation(speed=None),
                            exclusive_control=True)
        self.assertEqual(self.blaster.commands, ['speed_up'] * 5)  # wake plus four actions
        self.assertEqual(self.clock.waits, [6.25, 1, 1, 1, 1])

    def test_endpoint_saturates_from_every_physical_start_and_is_repeatable(self):
        for target, button in ((1, 'speed_down'), (4, 'speed_up')):
            for initial in range(1, 5):
                with self.subTest(target=target, initial=initial):
                    physical = [initial]
                    def send(command):
                        # A sleeping display consumes the wake press.
                        awake = self.engine.display_estimate == 'assumed_awake'
                        self.blaster.commands.append(command)
                        if awake:
                            physical[0] = max(1, min(4, physical[0] + (1 if command == 'speed_up' else -1)))
                    self.blaster.send = send
                    for _ in range(2):
                        self.blaster.commands.clear()
                        self.engine.invalidate_display()
                        attempt = self.engine.execute(FanState('intake', target),
                            lambda: self.observation(speed=None), exclusive_control=True)
                        self.assertEqual(self.blaster.commands, ['speed_up'] + [button] * 4)
                        self.assertEqual(physical[0], target)
                        self.clock.sleep(0.1)
                        self.engine.resolve_manually(attempt, self.observation(speed=target), note='Checked endpoint')

    def test_awake_endpoint_actions_use_exactly_four_presses(self):
        for target, button in ((1, 'speed_down'), (4, 'speed_up')):
            with self.subTest(target=target):
                start = self.engine.execute(FanState('intake', target),
                    lambda: self.observation(False), exclusive_control=True)
                self.clock.sleep(0.1)
                self.engine.confirm_power(start, self.observation(True))
                self.blaster.commands.clear()
                attempt = self.engine.execute(FanState('intake', target),
                    lambda: self.observation(speed=None), exclusive_control=True)
                self.assertEqual(self.blaster.commands, [button] * 4)
                self.clock.sleep(0.1)
                self.engine.resolve_manually(attempt, self.observation(speed=target), note='Checked endpoint')

    def test_journal_failure_between_endpoint_presses_prevents_next_send(self):
        original = self.engine.store.event
        action_attempts = [0]
        def event(attempt, phase, now, **details):
            if phase == 'action_attempt':
                action_attempts[0] += 1
                if action_attempts[0] == 2:
                    raise OSError('disk full')
            original(attempt, phase, now, **details)
        self.engine.store.event = event
        with self.assertRaises(OSError):
            self.engine.execute(FanState('intake', 4), self.observation, exclusive_control=True)
        self.assertEqual(self.blaster.commands, ['speed_up', 'speed_up'])
        with self.assertRaises(DeliveryBlocked):
            self.make_engine().execute(FanState('intake', 4), self.observation, exclusive_control=True)

    def test_partial_endpoint_failure_stops_sequence_and_blocks_restart(self):
        def send(command):
            self.blaster.commands.append(command)
            if len(self.blaster.commands) == 3:
                raise TimeoutError('lost acknowledgement')
        self.blaster.send = send
        with self.assertRaises(TimeoutError):
            self.engine.execute(FanState('intake', 4), self.observation, exclusive_control=True)
        with self.assertRaises(DeliveryBlocked):
            self.make_engine().execute(FanState('intake', 4), self.observation, exclusive_control=True)
        self.assertEqual(len(self.blaster.commands), 3)
        with database.connect() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM delivery_events WHERE phase='action_completed'").fetchone()[0], 0)

    def test_display_expires_between_endpoint_presses(self):
        waits = [0]
        def sleep(seconds):
            waits[0] += 1
            self.clock.sleep(10 if waits[0] == 3 else seconds)
        self.engine.sleep = sleep
        with self.assertRaises(DeliveryBlocked):
            self.engine.execute(FanState('intake', 4), self.observation, exclusive_control=True)
        self.assertEqual(self.blaster.commands, ['speed_up', 'speed_up'])
        self.assertIsNotNone(self.engine.store.current())

    def test_external_activity_between_endpoint_presses(self):
        waits = [0]
        def sleep(seconds):
            waits[0] += 1
            self.clock.sleep(seconds)
            if waits[0] == 3:
                self.engine.invalidate_display()
        self.engine.sleep = sleep
        with self.assertRaises(DeliveryBlocked):
            self.engine.execute(FanState('intake', 4), self.observation, exclusive_control=True)
        self.assertEqual(self.blaster.commands, ['speed_up', 'speed_up'])

    def test_speed_needs_manual_resolution(self):
        attempt = self.engine.execute(FanState('intake', 4), self.observation, exclusive_control=True)
        self.clock.sleep(0.1)
        with self.assertRaises(DeliveryBlocked):
            self.engine.confirm_power(attempt, self.observation(speed=4))
        self.engine.resolve_manually(attempt, self.observation(speed=4), note='Display shows high')
        self.assertEqual(self.engine.display_estimate, 'unknown')
        with self.assertRaises(DeliveryBlocked):
            DeliveryStore().pending(attempt)

    def test_crash_claim_requires_explicit_recovery(self):
        attempt = DeliveryStore().claim('power_toggle', False, self.clock.now())
        self.clock.sleep(0.1)
        with self.assertRaises(DeliveryBlocked):
            self.engine.confirm_power(attempt, self.observation(False))
        self.engine.resolve_manually(attempt, self.observation(), note='Operator inspected running fan')
        self.execute_off()

    def test_resolution_cannot_race_active_execution(self):
        attempt = DeliveryStore().claim('power_toggle', False, self.clock.now())
        self.clock.sleep(0.1)
        with DeliveryStore().guard():
            with self.assertRaises(DeliveryBlocked):
                self.engine.resolve_manually(attempt, self.observation(), note='Inspection')
            with self.assertRaises(DeliveryBlocked):
                self.execute_off()
        self.assertEqual(self.blaster.commands, [])

    def test_manual_resolution_cannot_use_pre_action_observation(self):
        attempt = self.execute_off()
        earlier = PowerObservation(True, self.clock.now() - timedelta(seconds=1), 'intake', 1)
        with self.assertRaises(DeliveryBlocked):
            self.engine.resolve_manually(attempt, earlier, note='Old inspection')

    def test_failed_journal_write_prevents_transmission(self):
        original = self.engine.store.event
        def event(attempt, phase, now, **details):
            if phase == 'wake_attempt':
                raise OSError('disk full')
            original(attempt, phase, now, **details)
        self.engine.store.event = event
        with self.assertRaises(OSError):
            self.execute_off()
        self.assertEqual(self.blaster.commands, [])
        with self.assertRaises(DeliveryBlocked):
            self.make_engine().execute(FanState(), self.observation, exclusive_control=True)

    def test_history_is_preserved_by_additive_migration(self):
        database.save_hardware_observation(device='shelly', result='ok', status={'watts': 0})
        self.execute_off()
        with sqlite3.connect(self.path) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM hardware_observations').fetchone()[0], 1)


class DeliverySettingsTests(unittest.TestCase):
    def test_default_disabled_and_env_overrides(self):
        with patch('settings.load_dotenv'), patch.dict('os.environ', {}, clear=True):
            self.assertFalse(load_delivery_settings().enabled)
        with patch('settings.load_dotenv'), patch.dict('os.environ', {
                'HARDWARE_CONTROL_ENABLED': 'true', 'FAN_DISPLAY_SLEEP_S': '7'}, clear=True):
            settings = load_delivery_settings()
            self.assertTrue(settings.enabled)
            self.assertEqual(settings.display_sleep_s, 7)

    def test_invalid_timings_and_boolean(self):
        for values in ({'enabled': 'false'}, {'display_sleep_s': float('nan')}, {'wake_delay_s': 6},
                       {'timing_margin_s': 0}, {'observation_max_age_s': -1}):
            with self.assertRaises(ValueError):
                DeliverySettings(**values)
        with patch('settings.load_dotenv'), patch.dict('os.environ', {'HARDWARE_CONTROL_ENABLED': 'yes'}):
            with self.assertRaises(ValueError):
                load_delivery_settings()
