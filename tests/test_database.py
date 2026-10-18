"""SQLite upgrade / history tests; isolated temporary database, no user data."""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database
from climate_policy import Decision, FanState, PolicyConfig
from settings import ControllerSettings


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.db_path = Path(self.tempdir.name) / "data" / "bedroom.sqlite3"
        patcher = patch.object(database, "DB_PATH", self.db_path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.settings = ControllerSettings(PolicyConfig(), "Indianapolis")
        self.bedroom = {"temp": 66.1, "humidity": 44, "updated": "2026-10-18T11:39:31+00:00"}
        self.outdoor = {"temp": 40.3, "humidity": 87, "dew_point": 36.7,
                        "updated": "2026-10-18T09:30", "location": "Indianapolis, Indiana"}

    def _save(self, previous=FanState(), desired=FanState("intake", 1), reason="Start cooling"):
        run_id = database.begin_run()
        config_id = database.activate_config(run_id, self.settings)
        decision = Decision(desired, reason, True, "start_cooling")
        database.save_evaluation(run_id=run_id, config_snapshot_id=config_id,
                                 bedroom=self.bedroom, outdoor=self.outdoor,
                                 previous=previous, decision=decision, settings=self.settings)
        return run_id, config_id

    def test_new_db_starts_off(self):
        self.assertEqual(database.last_desired_state(), FanState())

    def test_every_unchanged_decision_persisted(self):
        self._save()
        self._save(previous=FanState("intake", 1), desired=FanState("intake", 1), reason="Continue")
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM control_decisions").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT count(*) FROM sensor_readings").fetchone()[0], 2)
            rows = conn.execute("SELECT reason,state_changed,command_sent FROM control_decisions ORDER BY id").fetchall()
        self.assertEqual(rows, [("Start cooling", 1, 0), ("Continue", 0, 0)])
        self.assertEqual(database.last_desired_state(), FanState("intake", 1))

    def test_config_snapshots_and_transitions(self):
        _, id1 = self._save()
        _, id2 = self._save()
        self.assertEqual(id1, id2)
        self.settings = ControllerSettings(PolicyConfig(target_f=57, restart_f=61), "Indianapolis")
        _, id3 = self._save()
        self.assertNotEqual(id1, id3)
        self.settings = ControllerSettings(PolicyConfig(), "Indianapolis")
        _, id4 = self._save()
        self.assertEqual(id1, id4)
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM config_snapshots").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT count(*) FROM config_activations").fetchone()[0], 3)
            delta = json.loads(conn.execute("SELECT changed_values_json FROM config_activations ORDER BY id DESC LIMIT 1").fetchone()[0])
            self.assertEqual(delta["climate.target_f"], {"old": 57, "new": 58})
            decision = conn.execute("SELECT target_f,restart_f,policy_config_json,reason FROM control_decisions ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(decision[0:2], (58, 62))
        self.assertEqual(json.loads(decision[2])["climate"]["target_f"], 58)
        self.assertEqual(decision[3], "Start cooling")

    def test_failure_logged_without_fake_decision(self):
        run = database.begin_run()
        database.mark_failed(run, "fetch_outdoor", RuntimeError("API token: sensitive"))
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(conn.execute("SELECT status,stage,error_type,error_detail FROM controller_runs").fetchone(),
                             ("error", "fetch_outdoor", "RuntimeError", None))
            self.assertEqual(conn.execute("SELECT count(*) FROM control_decisions").fetchone()[0], 0)

    def test_config_failure_safe_diagnostics(self):
        run = database.begin_run()
        database.mark_failed(run, "load_settings", ValueError("CLIMATE_TARGET_F must be a number"))
        with sqlite3.connect(self.db_path) as conn:
            self.assertIn("CLIMATE_TARGET_F", conn.execute("SELECT error_detail FROM controller_runs").fetchone()[0])

    def test_legacy_tables_are_preserved(self):
        self.db_path.parent.mkdir(parents=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("CREATE TABLE sensor_readings (id INTEGER PRIMARY KEY, recorded_at TEXT, bedroom_temp_f REAL)")
            conn.execute("INSERT INTO sensor_readings(recorded_at,bedroom_temp_f) VALUES (?,?)", ("old", 65.4))
            conn.execute("""CREATE TABLE control_decisions (
                id INTEGER PRIMARY KEY, recorded_at_utc TEXT NOT NULL,
                bedroom_temp_f REAL NOT NULL, outdoor_temp_f REAL NOT NULL,
                scheduled INTEGER NOT NULL, previous_mode TEXT NOT NULL,
                previous_speed INTEGER NOT NULL, desired_mode TEXT NOT NULL,
                desired_speed INTEGER NOT NULL, reason TEXT NOT NULL,
                command_sent INTEGER NOT NULL DEFAULT 0)""")
            conn.execute("""INSERT INTO control_decisions
                (recorded_at_utc,bedroom_temp_f,outdoor_temp_f,scheduled,
                 previous_mode,previous_speed,desired_mode,desired_speed,reason,command_sent)
                VALUES ('old',65,40,1,'off',0,'intake',1,'previous',0)""")
        # Initial schema differs from release by missing optional columns.
        self.assertEqual(database.last_desired_state(), FanState("intake", 1))
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT bedroom_temp_f FROM sensor_readings WHERE id=1").fetchone()[0]
            decision = conn.execute("SELECT reason,reason_code FROM control_decisions WHERE id=1").fetchone()
        self.assertEqual(row, 65.4)
        self.assertEqual(decision, ("previous", None))

    def test_readonly_previous_no_write(self):
        self.assertEqual(database.last_desired_state(readonly=True), FanState())
        self.assertFalse(self.db_path.exists())


if __name__ == '__main__':
    unittest.main()
