"""SQLite hardware probe audit tests with a temporary database."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database


class HardwareLoggingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'data' / 'bedroom.sqlite3'
        patcher = patch.object(database, 'DB_PATH', self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_successful_shelly_probe(self):
        database.save_hardware_observation(device='shelly', result='ok',
                                           status={'watts': 11.5, 'relay_on': True})
        with sqlite3.connect(self.path) as c:
            row = c.execute('SELECT device,result,status_json,error_type FROM hardware_observations').fetchone()
        self.assertEqual(row[:2], ('shelly', 'ok'))
        self.assertEqual(json.loads(row[2]), {'watts': 11.5, 'relay_on': True})
        self.assertIsNone(row[3])

    def test_errors_log_type_only(self):
        database.save_hardware_observation(device='broadlink', result='error',
                                           error=RuntimeError('secret token leaked'))
        with sqlite3.connect(self.path) as c:
            row = c.execute('SELECT result,error_type,status_json FROM hardware_observations').fetchone()
        self.assertEqual(row, ('error', 'RuntimeError', None))
        self.assertNotIn('secret', self.path.read_bytes().decode(errors='ignore'))

    def test_bad_observation_rejected(self):
        with self.assertRaises(ValueError):
            database.save_hardware_observation(device='shelly', result='error')
        with self.assertRaises(ValueError):
            database.save_hardware_observation(device='shelly', result='ok', error=RuntimeError())
        with self.assertRaises(ValueError):
            database.save_hardware_observation(device='unknown', result='ok')


if __name__ == '__main__':
    unittest.main()
