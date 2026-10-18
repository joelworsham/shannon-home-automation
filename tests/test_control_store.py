"""SQLite decision history tests; uses a disposable database, not user data."""

from __future__ import annotations

import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import control_store
from climate_policy import Decision, FanState


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.db_path = Path(self.tempdir.name) / "data" / "bedroom.sqlite3"
        patcher = patch.object(control_store, "DB_PATH", self.db_path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_initial_state_off(self):
        self.assertEqual(control_store.last_desired_state(), FanState())

    def test_persists_last_desired_mode_and_speed(self):
        control_store.record_decision(
            bedroom_f=67.8, outdoor_f=40.1, previous=FanState(),
            decision=Decision(FanState("intake", 2), "Start cooling", True),
        )
        self.assertEqual(control_store.last_desired_state(), FanState("intake", 2))

        control_store.record_decision(
            bedroom_f=60.0, outdoor_f=40.1,
            previous=FanState("intake", 2),
            decision=Decision(FanState(), "At target", True),
        )
        self.assertEqual(control_store.last_desired_state(), FanState())

    def test_dry_run_never_marks_command_sent(self):
        control_store.record_decision(
            bedroom_f=65.0, outdoor_f=40.1, previous=FanState(),
            decision=Decision(FanState("intake", 1), "Start cooling", True),
        )
        with closing(sqlite3.connect(self.db_path)) as db, db:
            row = db.execute("""
                SELECT command_sent, desired_mode, desired_speed
                FROM control_decisions ORDER BY id DESC LIMIT 1
            """).fetchone()
        self.assertEqual(row, (0, "intake", 1))

    def test_does_not_damage_existing_sensor_readings_table(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.db_path)) as db, db:
            db.execute("CREATE TABLE sensor_readings (id INTEGER PRIMARY KEY, bedroom_temp_f REAL)")
            db.execute("INSERT INTO sensor_readings (bedroom_temp_f) VALUES (66.1)")
        control_store.last_desired_state()
        with closing(sqlite3.connect(self.db_path)) as db, db:
            value = db.execute("SELECT bedroom_temp_f FROM sensor_readings").fetchone()[0]
        self.assertEqual(value, 66.1)


if __name__ == "__main__":
    unittest.main()
