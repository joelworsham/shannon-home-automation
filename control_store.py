"""Persist dry-run controller decisions in the EXISTING local SQLite database."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from climate_policy import Decision, FanState

DB_PATH = Path(__file__).resolve().parent / "data" / "bedroom.sqlite3"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.execute("PRAGMA busy_timeout=10000")
    connection.execute("""
        CREATE TABLE IF NOT EXISTS control_decisions (
            id INTEGER PRIMARY KEY,
            recorded_at_utc TEXT NOT NULL,
            bedroom_temp_f REAL NOT NULL,
            outdoor_temp_f REAL NOT NULL,
            scheduled INTEGER NOT NULL,
            previous_mode TEXT NOT NULL,
            previous_speed INTEGER NOT NULL,
            desired_mode TEXT NOT NULL,
            desired_speed INTEGER NOT NULL,
            reason TEXT NOT NULL,
            command_sent INTEGER NOT NULL DEFAULT 0
        )
    """)
    connection.commit()
    return connection


def last_desired_state() -> FanState:
    with closing(_connect()) as conn:
        row = conn.execute("""
            SELECT desired_mode, desired_speed
            FROM control_decisions
            ORDER BY id DESC LIMIT 1
        """).fetchone()
    return FanState(row[0], row[1]) if row else FanState()


def record_decision(
    *, bedroom_f: float, outdoor_f: float, previous: FanState, decision: Decision
) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute("""
            INSERT INTO control_decisions (
                recorded_at_utc, bedroom_temp_f, outdoor_temp_f, scheduled,
                previous_mode, previous_speed, desired_mode, desired_speed,
                reason, command_sent
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
        """, (
            datetime.now(timezone.utc).isoformat(), bedroom_f, outdoor_f,
            int(decision.scheduled), previous.mode, previous.speed,
            decision.desired.mode, decision.desired.speed, decision.reason,
        ))
