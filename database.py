"""SQLite sensor history. Preserve data/bedroom.sqlite3 across code releases."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "data" / "bedroom.sqlite3"


def save_reading(bedroom, outdoor):
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sensor_readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recorded_at TEXT NOT NULL,
                bedroom_temp_f REAL,
                bedroom_humidity REAL,
                outdoor_temp_f REAL,
                outdoor_humidity REAL,
                outdoor_dew_point_f REAL,
                bedroom_source_updated TEXT,
                outdoor_source_updated TEXT
            )
        """)
        conn.execute("""
            INSERT INTO sensor_readings (
                recorded_at, bedroom_temp_f, bedroom_humidity,
                outdoor_temp_f, outdoor_humidity, outdoor_dew_point_f,
                bedroom_source_updated, outdoor_source_updated
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.now(timezone.utc).isoformat(),
            bedroom["temp"], bedroom.get("humidity"),
            outdoor["temp"], outdoor.get("humidity"), outdoor.get("dew_point"),
            bedroom.get("updated"), outdoor.get("updated"),
        ))
