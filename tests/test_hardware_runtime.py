import tempfile
import io
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import database
from climate_policy import FanState
from device_plan import PowerCalibration
from devices.shelly_local import ShellyPowerStatus
from hardware_runtime import HardwareRuntime
from power_observer import SettledPowerObserver
from settings import DeliverySettings, PowerSettings, load_power_settings
from tests.test_wake_delivery import Clock
from wake_delivery import WakeAwareDelivery


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.path = Path(td.name) / 'history.sqlite3'
        p = patch.object(database, 'DB_PATH', self.path)
        p.start()
        self.addCleanup(p.stop)
        self.clock = Clock()
        self.running = False
        self.relay = True
        self.awake_until = 0
        self.commands = []
        self.watt_values = []
        self.sample_times = []
        self.power = PowerSettings(PowerCalibration(0.5, 15))  # synthetic test settings

        class Meter:
            def read_status(inner):
                self.clock.value += 0.01
                self.sample_times.append(self.clock.value)
                watts = self.watt_values.pop(0) if self.watt_values else (24 if self.running else 0)
                return ShellyPowerStatus(watts, self.relay, 120, 0.2, self.clock.now().isoformat())
        self.meter = Meter()

        class Blaster:
            def send(inner, command):
                self.commands.append((self.clock.value, command))
                awake = self.clock.value < self.awake_until
                self.awake_until = self.clock.value + 6
                if awake and command == 'power_toggle':
                    self.running = not self.running
        self.blaster = Blaster()
        self.observer = self.make_observer()
        self.delivery = WakeAwareDelivery(self.blaster, settings=DeliverySettings(enabled=True),
            sleep=self.clock.sleep, monotonic=lambda: self.clock.value, utcnow=self.clock.now)
        self.runtime = HardwareRuntime(self.delivery, self.observer, self.power,
                                      sleep=self.clock.sleep, utcnow=self.clock.now)

    def make_observer(self, **kwargs):
        return SettledPowerObserver(self.meter, self.power, sleep=self.clock.sleep,
            monotonic=lambda: self.clock.value, utcnow=self.clock.now, **kwargs)

    def test_power_on_one_action_then_settled_confirmation(self):
        result = self.runtime.tick(FanState('intake', 4), exclusive_control=True)
        self.assertEqual(result['status'], 'power_confirmed')
        self.assertEqual([c for _, c in self.commands], ['speed_up', 'power_toggle'])
        action_at = self.commands[-1][0]
        post = [t for t in self.sample_times if t > action_at]
        self.assertGreaterEqual(min(post) - action_at, self.power.settle_s)
        self.assertIsNone(self.delivery.store.current())
        self.assertTrue(self.running)
        self.assertLess(self.commands[1][0] - self.commands[0][0], 5.75)

    def test_power_off_confirmed(self):
        self.running = True
        result = self.runtime.tick(FanState(), exclusive_control=True)
        self.assertEqual(result['status'], 'power_confirmed')
        self.assertFalse(self.running)

    def test_satisfied_does_not_transmit(self):
        result = self.runtime.tick(FanState(), exclusive_control=True)
        self.assertEqual(result['status'], 'satisfied')
        self.assertEqual(self.commands, [])

    def test_unknown_direction_blocks_running_fan(self):
        self.running = True
        result = self.runtime.tick(FanState('intake', 4), exclusive_control=True)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(self.commands, [])

    def test_speed_action_remains_pending_for_manual_verification(self):
        self.running = True
        self.runtime.observer = self.make_observer(speed=1, direction='intake')
        result = self.runtime.tick(FanState('intake', 4), exclusive_control=True)
        self.assertEqual(result['status'], 'awaiting_manual_verification')
        self.assertEqual([c for _, c in self.commands], ['speed_up'] * 5)
        result = self.runtime.tick(FanState('intake', 4), exclusive_control=True)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(len(self.commands), 5)

    def test_missing_power_response_never_retries(self):
        self.blaster.send = lambda command: self.commands.append((self.clock.value, command))
        result = self.runtime.tick(FanState('intake', 4), exclusive_control=True)
        self.assertEqual(result['status'], 'blocked')
        result = self.runtime.tick(FanState('intake', 4), exclusive_control=True)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(len(self.commands), 2)

    def test_pending_confirmation_waits_for_settling_then_stops_this_run(self):
        attempt = self.delivery.execute(FanState('intake', 4), self.observer, exclusive_control=True)
        action_at = self.commands[-1][0]
        result = self.runtime.tick(FanState(), exclusive_control=True)
        self.assertEqual(result['status'], 'power_confirmed')
        self.assertEqual(result['attempt_id'], attempt)
        self.assertEqual(len(self.commands), 2)
        self.assertGreaterEqual(min(t for t in self.sample_times if t > action_at) - action_at, 6)

    def test_unknown_gap_or_relay_off_never_classified_as_standby(self):
        self.watt_values = [3, 2.3, 0]
        self.assertIsNone(self.observer().running)
        self.observer.reset()
        self.relay = False
        self.assertIsNone(self.observer().running)

    def test_mixed_running_standby_window_is_uncertain(self):
        self.watt_values = [24, 0, 0]
        self.assertIsNone(self.observer().running)

    def test_window_includes_three_samples_and_timestamped_reads(self):
        result = self.observer()
        self.assertFalse(result.running)
        self.assertEqual(len(self.sample_times), 3)
        self.assertGreaterEqual(self.sample_times[-1] - self.sample_times[0], 2)

    def test_expired_manual_speed_not_refreshed_by_power_reads(self):
        self.running = True
        observer = self.make_observer(speed=1, direction='intake')
        self.assertEqual(observer().speed, 1)
        self.clock.sleep(31)
        result = observer()
        self.assertTrue(result.running)
        self.assertIsNone(result.speed)
        self.assertIsNone(result.direction)

    def test_stale_meter_timestamp_rejected(self):
        self.meter.read_status = lambda: ShellyPowerStatus(0, True, None, None,
            (self.clock.now() - timedelta(seconds=10)).isoformat())
        with self.assertRaises(ValueError):
            self.observer()
        self.assertEqual(self.observer.samples, [])

    def test_power_configuration_requires_explicit_thresholds(self):
        with patch('settings.load_dotenv'), patch.dict('os.environ', {}, clear=True):
            with self.assertRaises(ValueError):
                load_power_settings()
        with patch('settings.load_dotenv'), patch.dict('os.environ', {
                'FAN_STANDBY_MAX_W': '0.5', 'FAN_RUNNING_MIN_W': '15'}, clear=True):
            self.assertEqual(load_power_settings().calibration, PowerCalibration(0.5, 15))

    def test_invalid_power_timings(self):
        for kwargs in ({'window_s': 1}, {'settle_s': float('nan')}, {'sample_interval_s': 0}):
            with self.assertRaises(ValueError):
                PowerSettings(PowerCalibration(0.5, 15), **kwargs)

    def test_controller_through_runtime_to_fake_hardware(self):
        import controller
        from climate_policy import Decision, PolicyConfig
        from settings import ControllerSettings
        def build(settings, *, run_id, **kwargs):
            self.observer.run_id = run_id
            return self.runtime
        with patch('sys.argv', ['controller.py', '--live', '--exclusive-control']), \
             patch('sys.stdout', new_callable=io.StringIO), \
             patch.object(controller, 'load_settings', return_value=ControllerSettings(PolicyConfig(), 'Test')), \
             patch.object(controller, 'load_delivery_settings', return_value=DeliverySettings(enabled=True)), \
             patch.object(controller, 'get_bedroom', return_value={'temp': 68}), \
             patch.object(controller, 'get_outdoor', return_value={'temp': 40}), \
             patch.object(controller, 'evaluate', return_value=Decision(FanState('intake', 4), 'Cool', True, 'start_cooling')), \
             patch('hardware_runtime.build_runtime', side_effect=build):
            controller.main()
        with database.connect() as conn:
            self.assertEqual(conn.execute('SELECT command_sent FROM control_decisions').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT resolved FROM delivery_attempts').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT status FROM controller_runs').fetchone()[0], 'success')
            self.assertEqual(conn.execute('SELECT count(*) FROM delivery_attempts a JOIN controller_runs r ON a.run_id=r.id').fetchone()[0], 1)
        self.assertEqual([c for _, c in self.commands], ['speed_up', 'power_toggle'])

    def test_manual_recovery_cli_resolves_without_a_blaster(self):
        import hardware_runtime
        self.running = True
        attempt = self.delivery.store.claim('set_high', None, self.clock.now())
        self.delivery.store.event(attempt, 'action_attempt', self.clock.now())
        def observer(meter, settings, **kwargs):
            return self.make_observer(**kwargs)
        with patch('sys.argv', ['hardware_runtime.py', '--resolve-attempt', attempt,
                               '--note', 'Inspected actual speed', '--verified-speed', 'high']), \
             patch('sys.stdout', new_callable=io.StringIO), \
             patch.object(hardware_runtime, 'load_delivery_settings', return_value=DeliverySettings()), \
             patch.object(hardware_runtime, 'load_power_settings', return_value=self.power), \
             patch.object(hardware_runtime, 'ShellyPlugUSGen4', return_value=self.meter), \
             patch.object(hardware_runtime, 'SettledPowerObserver', side_effect=observer), \
             patch.object(hardware_runtime.time, 'sleep', side_effect=self.clock.sleep), \
             patch.object(hardware_runtime, 'WakeAwareDelivery', side_effect=lambda blaster, settings: WakeAwareDelivery(
                 blaster, settings=settings, utcnow=self.clock.now, monotonic=lambda: self.clock.value)):
            hardware_runtime.main()
        self.assertIsNone(self.delivery.store.current())
        self.assertEqual(self.commands, [])
