"""SQLite continuity and simulated-command constraints."""
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import database
from climate_policy import FanState
from safety_state import PowerObservation, reconcile
from simulate_faults import run_scenarios

T = datetime(2026, 1, 15, 23, 0, tzinfo=timezone.utc)


class StateHistoryTests(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.path = Path(td.name) / 'data' / 'bedroom.sqlite3'
        p = patch.object(database, 'DB_PATH', self.path)
        p.start()
        self.addCleanup(p.stop)

    def test_device_snapshots_are_immutable_and_unknowns_null(self):
        desired = FanState('intake', 4)
        observed = PowerObservation(None)
        result = reconcile(desired=desired, observed=observed, now=T)
        database.save_state_snapshot(desired=desired, observed=observed, outcome=result)
        with database.connect() as conn:
            row = conn.execute('''SELECT desired_mode,desired_speed,last_command,observed_running,
                                  reconciliation_status FROM device_state_snapshots''').fetchone()
        self.assertEqual(row, ('intake', 4, None, None, 'blocked'))

    def test_live_dry_run_no_phantom_ir_commands(self):
        from climate_policy import Decision, PolicyConfig
        from settings import ControllerSettings
        config = ControllerSettings(PolicyConfig(), 'Indianapolis')
        run = database.begin_run()
        sid = database.activate_config(run, config)
        database.save_evaluation(run_id=run, config_snapshot_id=sid,
            bedroom={'temp': 66, 'humidity': 45}, outdoor={'temp': 45}, previous=FanState(),
            decision=Decision(FanState('intake', 4),'Start cooling',True,'start_cooling'), settings=config)
        with database.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM command_attempts').fetchone()[0], 0)
            row = conn.execute('''SELECT desired_mode,desired_speed,last_command,observed_running,
                                  reconciliation_status FROM device_state_snapshots''').fetchone()
        self.assertEqual(row, ('intake', 4, None, None, 'physical_state_unverified'))

    def test_live_failure_creates_diagnostic_without_secrets(self):
        run = database.begin_run()
        database.mark_failed(run, 'fetch_bedroom', RuntimeError('secret-abcd'))
        with database.connect() as conn:
            row = conn.execute('SELECT code,detail FROM diagnostic_events').fetchone()
        self.assertEqual(row[0], 'controller_run_failed')
        self.assertNotIn('secret', row[1])

    def test_only_simulated_commands_can_be_recorded(self):
        with self.assertRaises(PermissionError):
            database.save_command_attempt(command='power_toggle', disposition='sent', note='should fail')
        database.save_command_attempt(command='power_toggle', disposition='simulated', note='fake')
        with database.connect() as conn:
            self.assertEqual(conn.execute('SELECT disposition FROM command_attempts').fetchone()[0], 'simulated')

    def test_simulator_never_touches_production_db(self):
        summary = run_scenarios()
        self.assertEqual(summary['missed_ir'][1], 'power_toggle_unconfirmed')
        self.assertEqual(summary['power_start'][1], 'power_confirmed')
        self.assertEqual(summary['exhaust_trend'], ['cooling_during_exhaust'])
        self.assertEqual(summary['sqlite']['simulated_commands'], 1)
        self.assertFalse(self.path.exists())

    def test_upgrades_existing_database_without_deleting_history(self):
        self.path.parent.mkdir(parents=True)
        with sqlite3.connect(self.path) as conn:
            conn.execute('CREATE TABLE sensor_readings (id INTEGER PRIMARY KEY, recorded_at TEXT, bedroom_temp_f REAL)')
            conn.execute('INSERT INTO sensor_readings (recorded_at,bedroom_temp_f) VALUES (?,?)', ('old', 60.1))
        with database.connect() as conn:
            self.assertEqual(conn.execute('SELECT bedroom_temp_f FROM sensor_readings').fetchone()[0], 60.1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM device_state_snapshots").fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
