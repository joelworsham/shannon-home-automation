
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

DB_PATH = Path(__file__).parent / "data" / "bedroom.sqlite3"


def save_reading(bedroom, outdoor):
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)

    try:
        with conn:
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
                    recorded_at,
                    bedroom_temp_f,
                    bedroom_humidity,
                    outdoor_temp_f,
                    outdoor_humidity,
                    outdoor_dew_point_f,
                    bedroom_source_updated,
                    outdoor_source_updated
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                datetime.now(timezone.utc).isoformat(),
                bedroom["temp"],
                bedroom.get("humidity"),
                outdoor["temp"],
                outdoor.get("humidity"),
                outdoor.get("dew_point"),
                bedroom.get("updated"),
                outdoor.get("updated"),
            ))
    finally:
        conn.close()
