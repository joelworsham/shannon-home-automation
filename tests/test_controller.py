"""End-to-end offline runs via mocked Flair and weather APIs."""

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import controller
import database
from climate_policy import PolicyConfig
from settings import ControllerSettings, DeliverySettings


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.db_path = Path(self.tempdir.name) / 'data' / 'bedroom.sqlite3'
        patches = [
            patch.object(database, 'DB_PATH', self.db_path),
            patch.object(controller, 'get_bedroom', return_value={'temp': 68.0, 'humidity': 40}),
            patch.object(controller, 'get_outdoor', return_value={'temp': 42.0, 'humidity': 80, 'dew_point': 36}),
            patch.object(controller, 'load_settings', return_value=ControllerSettings(PolicyConfig(), 'Indianapolis')),
            patch('sys.argv', ['controller.py']),
            patch('sys.stdout', new_callable=io.StringIO),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_normal_run_persists_decision_readings_config(self):
        controller.main()
        with database.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM controller_runs WHERE status="success"').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM sensor_readings').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM control_decisions').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM config_snapshots').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM config_activations').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM device_state_snapshots').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM command_attempts').fetchone()[0], 0)

    def test_failed_api_call_creates_error_run(self):
        with patch.object(controller, 'get_outdoor', side_effect=RuntimeError('API unavailable')):
            with self.assertRaises(RuntimeError):
                controller.main()
        with database.connect() as conn:
            self.assertEqual(conn.execute('SELECT status,stage FROM controller_runs').fetchone(), ('error', 'fetch_outdoor'))
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM control_decisions').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT code FROM diagnostic_events').fetchone()[0], 'controller_run_failed')

    def test_simulation_does_not_create_db(self):
        with patch('sys.argv', ['controller.py', '--at', '23:00', '--previous', 'off']):
            controller.main()
        self.assertFalse(self.db_path.exists())

    def test_default_never_calls_hardware_even_if_environment_enabled(self):
        with patch.dict('os.environ', {'HARDWARE_CONTROL_ENABLED': 'true'}), patch.object(controller, 'run_live') as live:
            controller.main()
        live.assert_not_called()

    def test_live_requires_enable_and_exclusive_flags(self):
        for argv in (['controller.py', '--live'], ['controller.py', '--live', '--exclusive-control'],
                     ['controller.py', '--live', '--exclusive-control', '--at', '23:00']):
            with self.subTest(argv=argv), patch('sys.argv', argv), patch('sys.stderr', new_callable=io.StringIO), \
                 patch.object(controller, 'load_delivery_settings', return_value=DeliverySettings()), \
                 patch.object(controller, 'run_live') as live, self.assertRaises(SystemExit):
                controller.main()
            live.assert_not_called()

    def test_live_run_links_journal_without_dry_run_claims(self):
        from delivery_store import DeliveryStore
        from datetime import datetime, timezone
        def live(desired, config, *, run_id, **kwargs):
            store = DeliveryStore()
            now = datetime.now(timezone.utc)
            attempt = store.claim('power_toggle', True, now, run_id=run_id)
            store.event(attempt, 'action_attempt', now)
            store.event(attempt, 'action_acknowledged', now)
            return {'status': 'power_confirmed', 'note': 'Fake power confirmation', 'attempt_id': attempt}
        with patch('sys.argv', ['controller.py', '--live', '--exclusive-control']), \
             patch.object(controller, 'load_delivery_settings', return_value=DeliverySettings(enabled=True)), \
             patch.object(controller, 'run_live', side_effect=live):
            controller.main()
        with database.connect() as conn:
            self.assertEqual(conn.execute('SELECT command_sent FROM control_decisions').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT source FROM device_state_snapshots').fetchone()[0], 'live_control')
            self.assertEqual(conn.execute('SELECT status FROM controller_runs').fetchone()[0], 'success')

    def test_uncertain_send_is_recorded_unknown_not_false(self):
        from delivery_store import DeliveryStore
        from datetime import datetime, timezone
        def fail(desired, config, *, run_id, **kwargs):
            store = DeliveryStore()
            now = datetime.now(timezone.utc)
            attempt = store.claim('power_toggle', True, now, run_id=run_id)
            store.event(attempt, 'action_attempt', now)
            raise TimeoutError('private host')
        with patch('sys.argv', ['controller.py', '--live', '--exclusive-control']), \
             patch.object(controller, 'load_delivery_settings', return_value=DeliverySettings(enabled=True)), \
             patch.object(controller, 'run_live', side_effect=fail), self.assertRaises(TimeoutError):
            controller.main()
        with database.connect() as conn:
            self.assertEqual(conn.execute('SELECT command_sent FROM control_decisions').fetchone()[0], -1)
            self.assertEqual(conn.execute('SELECT status FROM controller_runs').fetchone()[0], 'error')
        self.assertNotIn(b'private host', self.db_path.read_bytes())


if __name__ == '__main__':
    unittest.main()
