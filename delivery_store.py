"""Durable delivery journal. Unresolved attempts block sends across restarts."""
import json
import uuid
import fcntl
from contextlib import contextmanager
from pathlib import Path

import database


class DeliveryBlocked(RuntimeError):
    pass


class DeliveryStore:
    @contextmanager
    def guard(self):
        """Exclude concurrent delivery or manual resolution, including threads.

        macOS/Linux advisory lock is released by the OS on process death;
        the durable unresolved row still prevents retry after that death.
        """
        path = Path(str(database.DB_PATH) + '.delivery.lock')
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise DeliveryBlocked('Another delivery or resolution is in progress') from exc
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def claim(self, command, expected_running, now, *, run_id=None):
        attempt = str(uuid.uuid4())
        with database.connect() as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            if conn.execute('SELECT 1 FROM delivery_attempts WHERE resolved=0').fetchone():
                raise DeliveryBlocked('An unresolved delivery needs verification; no retry')
            conn.execute('INSERT INTO delivery_attempts '
                         '(id,command,expected_running,started_at_utc,resolved,run_id) VALUES (?,?,?,?,0,?)',
                         (attempt, command, expected_running, now.isoformat(), run_id))
        return attempt

    def current(self):
        with database.connect() as conn:
            return conn.execute('SELECT id,command FROM delivery_attempts WHERE resolved=0').fetchone()

    def event(self, attempt, phase, now, **details):
        with database.connect() as conn, conn:
            conn.execute('INSERT INTO delivery_events (attempt_id,phase,at_utc,details_json) VALUES (?,?,?,?)',
                         (attempt, phase, now.isoformat(), json.dumps(details, allow_nan=False)))

    def pending(self, attempt):
        with database.connect() as conn:
            row = conn.execute('SELECT command,expected_running,started_at_utc FROM delivery_attempts '
                               'WHERE id=? AND resolved=0', (attempt,)).fetchone()
        if row is None:
            raise DeliveryBlocked('Attempt is missing or already resolved')
        return row

    def action_time(self, attempt):
        with database.connect() as conn:
            row = conn.execute("SELECT at_utc FROM delivery_events WHERE attempt_id=? "
                               "AND phase='action_acknowledged' ORDER BY id DESC LIMIT 1", (attempt,)).fetchone()
        return row[0] if row else None

    def last_send_time(self, attempt):
        with database.connect() as conn:
            row = conn.execute("SELECT at_utc FROM delivery_events WHERE attempt_id=? "
                               "AND phase IN ('wake_attempt','action_attempt') ORDER BY id DESC LIMIT 1",
                               (attempt,)).fetchone()
        return row[0] if row else None

    def resolve(self, attempt, now, **evidence):
        with database.connect() as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            changed = conn.execute('UPDATE delivery_attempts SET resolved=1 WHERE id=? AND resolved=0',
                                   (attempt,)).rowcount
            if changed != 1:
                raise DeliveryBlocked('Attempt is missing or already resolved')
            conn.execute('INSERT INTO delivery_events (attempt_id,phase,at_utc,details_json) VALUES (?,?,?,?)',
                         (attempt, 'resolved', now.isoformat(), json.dumps(evidence, allow_nan=False)))
