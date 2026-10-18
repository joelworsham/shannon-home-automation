"""End-to-end offline runs via mocked Flair and weather APIs."""

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import controller
import database
from climate_policy import PolicyConfig
from settings import ControllerSettings


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


if __name__ == '__main__':
    unittest.main()
