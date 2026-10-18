"""SQLite history, including upgrades from previously released schemas.

No database files are included with code releases. Every normal evaluation creates
one controller_run; successful evaluations also append a reading and a decision.
Configuration snapshots and activation history never include secrets.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path

from climate_policy import Decision, FanState
from settings import ControllerSettings

DB_PATH = Path(__file__).resolve().parent / "data" / "bedroom.sqlite3"


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _upgrade_column(conn: sqlite3.Connection, table: str, name: str, definition: str) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if name not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


@contextmanager
def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(DB_PATH, timeout=10)) as conn:
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA foreign_keys=ON")
        with conn:
            _migrate(conn)
        yield conn


def _migrate(conn: sqlite3.Connection) -> None:
    # Preserve original table names, IDs, and all existing rows.
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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS config_snapshots (
            id INTEGER PRIMARY KEY,
            config_hash TEXT NOT NULL UNIQUE,
            first_seen_at_utc TEXT NOT NULL,
            config_json TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS controller_runs (
            id INTEGER PRIMARY KEY,
            started_at_utc TEXT NOT NULL,
            finished_at_utc TEXT,
            status TEXT NOT NULL,
            stage TEXT NOT NULL,
            config_snapshot_id INTEGER REFERENCES config_snapshots(id),
            error_type TEXT,
            error_detail TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS config_activations (
            id INTEGER PRIMARY KEY,
            recorded_at_utc TEXT NOT NULL,
            run_id INTEGER NOT NULL REFERENCES controller_runs(id),
            previous_snapshot_id INTEGER REFERENCES config_snapshots(id),
            current_snapshot_id INTEGER NOT NULL REFERENCES config_snapshots(id),
            changed_values_json TEXT NOT NULL
        )
    """)
    for table, extra in {
        "sensor_readings": {"run_id": "INTEGER REFERENCES controller_runs(id)"},
        "control_decisions": {
            "run_id": "INTEGER REFERENCES controller_runs(id)",
            "config_snapshot_id": "INTEGER REFERENCES config_snapshots(id)",
            "reason_code": "TEXT",
            "state_changed": "INTEGER",
            "cooling_advantage_f": "REAL",
            "target_f": "REAL",
            "restart_f": "REAL",
            "min_cooling_advantage_f": "REAL",
            "policy_config_json": "TEXT",
            "decision_context_json": "TEXT",
        },
    }.items():
        for col, definition in extra.items():
            _upgrade_column(conn, table, col, definition)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hardware_observations (
            id INTEGER PRIMARY KEY,
            recorded_at_utc TEXT NOT NULL,
            device TEXT NOT NULL,
            result TEXT NOT NULL,
            status_json TEXT,
            error_type TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS ix_hardware_observations_time ON hardware_observations(recorded_at_utc)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_decisions_run ON control_decisions(run_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_readings_run ON sensor_readings(run_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_runs_started ON controller_runs(started_at_utc)")


def begin_run() -> int:
    """Log every attempted unattended evaluation, even if an API fails later."""
    with connect() as conn, conn:
        cur = conn.execute(
            "INSERT INTO controller_runs (started_at_utc,status,stage) VALUES (?,?,?)",
            (_utc(), "running", "initialize"),
        )
        return cur.lastrowid


def _diff(old: dict, new: dict) -> dict:
    old_policy = old.get("climate", {})
    new_policy = new.get("climate", {})
    old_flat = {**{f"climate.{k}": v for k, v in old_policy.items()},
                "weather_location": old.get("weather_location")}
    new_flat = {**{f"climate.{k}": v for k, v in new_policy.items()},
                "weather_location": new.get("weather_location")}
    return {key: {"old": old_flat.get(key), "new": value}
            for key, value in new_flat.items() if old_flat.get(key) != value}


def activate_config(run_id: int, settings: ControllerSettings) -> int:
    """Save a unique config snapshot and log each *transition*, including reverts."""
    current = settings.as_public_dict()
    config_json = _json(current)
    digest = hashlib.sha256(config_json.encode("utf-8")).hexdigest()
    with connect() as conn, conn:
        previous_row = conn.execute("""
            SELECT ca.current_snapshot_id, cs.config_json
            FROM config_activations ca
            JOIN config_snapshots cs ON cs.id = ca.current_snapshot_id
            ORDER BY ca.id DESC LIMIT 1
        """).fetchone()
        conn.execute("""
            INSERT OR IGNORE INTO config_snapshots(config_hash,first_seen_at_utc,config_json)
            VALUES(?,?,?)
        """, (digest, _utc(), config_json))
        snapshot_id = conn.execute(
            "SELECT id FROM config_snapshots WHERE config_hash=?", (digest,)
        ).fetchone()[0]
        if not previous_row or previous_row[0] != snapshot_id:
            old_data = json.loads(previous_row[1]) if previous_row else {}
            conn.execute("""
                INSERT INTO config_activations
                    (recorded_at_utc,run_id,previous_snapshot_id,current_snapshot_id,changed_values_json)
                VALUES(?,?,?,?,?)
            """, (_utc(), run_id, previous_row[0] if previous_row else None,
                  snapshot_id, _json(_diff(old_data, current))))
        conn.execute("""
            UPDATE controller_runs SET config_snapshot_id=?,stage=? WHERE id=?
        """, (snapshot_id, "fetch_sensors", run_id))
        return snapshot_id


def update_stage(run_id: int, stage: str) -> None:
    with connect() as conn, conn:
        conn.execute("UPDATE controller_runs SET stage=? WHERE id=?", (stage, run_id))


def mark_failed(run_id: int, stage: str, error: Exception) -> None:
    # Avoid storing HTTP exception messages that might contain credentials/tokens.
    # Configuration validation errors contain only public config keys/values.
    detail = str(error)[:250] if stage == "load_settings" and isinstance(error, ValueError) else None
    with connect() as conn, conn:
        conn.execute("""
            UPDATE controller_runs
            SET status='error',stage=?,finished_at_utc=?,error_type=?,error_detail=?
            WHERE id=?
        """, (stage, _utc(), type(error).__name__, detail, run_id))


def last_desired_state(*, readonly: bool = False) -> FanState:
    """Previous *desired* state, not a verified actual physical state."""
    if readonly:
        if not DB_PATH.exists():
            return FanState()
        with closing(sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)) as conn:
            try:
                row = conn.execute("""
                    SELECT desired_mode, desired_speed FROM control_decisions
                    ORDER BY id DESC LIMIT 1
                """).fetchone()
            except sqlite3.OperationalError:
                return FanState()
    else:
        with connect() as conn:
            row = conn.execute("""
                SELECT desired_mode, desired_speed FROM control_decisions
                ORDER BY id DESC LIMIT 1
            """).fetchone()
    return FanState(row[0], row[1]) if row else FanState()


def save_evaluation(
    *, run_id: int, config_snapshot_id: int, bedroom: dict, outdoor: dict,
    previous: FanState, decision: Decision, settings: ControllerSettings,
) -> None:
    """Atomic append of sensor readings, full reasoning context and run success."""
    policy = settings.policy
    values = settings.as_public_dict()
    with connect() as conn, conn:
        now = _utc()
        conn.execute("""
            INSERT INTO sensor_readings
                (recorded_at,bedroom_temp_f,bedroom_humidity,outdoor_temp_f,
                 outdoor_humidity,outdoor_dew_point_f,bedroom_source_updated,
                 outdoor_source_updated,run_id)
            VALUES(?,?,?,?,?,?,?,?,?)
        """, (now, bedroom["temp"], bedroom.get("humidity"), outdoor["temp"],
              outdoor.get("humidity"), outdoor.get("dew_point"),
              bedroom.get("updated"), outdoor.get("updated"), run_id))
        context = {
            "bedroom": bedroom,
            "outdoor": outdoor,
            "previous_desired": {"mode": previous.mode, "speed": previous.speed},
            "desired": {"mode": decision.desired.mode, "speed": decision.desired.speed},
            "cooling_advantage_f": bedroom["temp"] - outdoor["temp"],
            "scheduled": decision.scheduled,
            "reason_code": decision.reason_code,
            "reason": decision.reason,
            "state_changed": decision.desired != previous,
            "hardware_commands_sent": False,
        }
        conn.execute("""
            INSERT INTO control_decisions
                (recorded_at_utc,bedroom_temp_f,outdoor_temp_f,scheduled,
                 previous_mode,previous_speed,desired_mode,desired_speed,
                 reason,command_sent,run_id,config_snapshot_id,reason_code,
                 state_changed,cooling_advantage_f,target_f,restart_f,
                 min_cooling_advantage_f,policy_config_json,decision_context_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (now, bedroom["temp"], outdoor["temp"], int(decision.scheduled),
              previous.mode, previous.speed, decision.desired.mode, decision.desired.speed,
              decision.reason, 0, run_id, config_snapshot_id, decision.reason_code,
              int(decision.desired != previous), bedroom["temp"] - outdoor["temp"],
              policy.target_f, policy.restart_f, policy.min_cooling_advantage_f,
              _json(values), _json(context)))
        conn.execute("""
            UPDATE controller_runs SET status='success',stage='complete',finished_at_utc=?
            WHERE id=?
        """, (_utc(), run_id))


def save_hardware_observation(*, device: str, result: str,
                              status: dict | None = None,
                              error: Exception | None = None) -> None:
    """Append a manual local-device probe; no credentials or packets are stored.

    For errors, only store the exception type, not URL/token-bearing messages.
    """
    if device not in {"broadlink", "shelly"} or result not in {"ok", "error"}:
        raise ValueError("Invalid device observation")
    if (result == "error") != (error is not None):
        raise ValueError("An error result must have an exception, and vice versa")
    with connect() as conn, conn:
        conn.execute("""
            INSERT INTO hardware_observations
            (recorded_at_utc,device,result,status_json,error_type)
            VALUES (?,?,?,?,?)
        """, (_utc(), device, result,
              _json(status) if status is not None else None,
              type(error).__name__ if error is not None else None))
