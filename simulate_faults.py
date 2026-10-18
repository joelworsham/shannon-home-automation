"""Offline, deterministic scenarios. NO APIs, NO hardware, NO production SQLite.

Run `python simulate_faults.py` to exercise missed IR, stale Shelly,
unknown direction, and suspicious temperature trends. Test history is stored
only in an automatically deleted temporary SQLite file.
"""
from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import database
from climate_policy import FanState
from safety_state import PendingCommand, PowerObservation, reconcile
from temperature_diagnostics import ThermalSample, check_temperature_trend

BASE = datetime(2026, 1, 15, 23, 0, tzinfo=timezone.utc)
DESIRED = FanState('intake', 2)


def run_scenarios() -> dict:
    results = {}
    with tempfile.TemporaryDirectory(prefix='bedroom-fault-sim-') as temp:
        with patch.object(database, 'DB_PATH', Path(temp) / 'scenario.sqlite3'):
            # Scenario 1: known-off => plan 1 toggle => fresh, confirming watts.
            off = PowerObservation(False, BASE, watts=1.0)
            first = reconcile(desired=DESIRED, observed=off, now=BASE)
            assert first.plan.command == 'power_toggle'
            database.save_state_snapshot(desired=DESIRED, observed=off, outcome=first, at_utc=BASE.isoformat())
            database.save_command_attempt(command='power_toggle', disposition='simulated',
                                          expected_running=True, note='Fake transmission only',
                                          at_utc=BASE.isoformat())
            pending = PendingCommand('power_toggle', BASE, expected_running=True)
            on = PowerObservation(True, BASE + timedelta(seconds=30), watts=11.0)
            confirmed = reconcile(desired=DESIRED, observed=on, pending=pending,
                                  now=BASE + timedelta(seconds=30))
            database.save_state_snapshot(desired=DESIRED, observed=on, outcome=confirmed,
                                         pending=pending, at_utc=(BASE + timedelta(seconds=30)).isoformat())
            # Even after power confirms, direction and speed are unverified.
            unverified = reconcile(desired=DESIRED, observed=on,
                                   now=BASE + timedelta(seconds=31))
            database.save_state_snapshot(desired=DESIRED, observed=on,
                                         outcome=unverified, at_utc=(BASE + timedelta(seconds=31)).isoformat())
            results['power_start'] = [first.status, confirmed.status, unverified.status]

            # Scenario 2: IR misses => still OFF. Do NOT repeat toggle.
            missed = PowerObservation(False, BASE + timedelta(seconds=120), watts=1.0)
            blocked = reconcile(desired=DESIRED, observed=missed, pending=pending,
                                now=BASE + timedelta(seconds=120))
            database.save_state_snapshot(desired=DESIRED, observed=missed,
                                         outcome=blocked, pending=pending,
                                         at_utc=(BASE + timedelta(seconds=120)).isoformat())
            if blocked.diagnostic_code:
                database.save_diagnostic(code=blocked.diagnostic_code, detail=blocked.reason,
                                         at_utc=(BASE + timedelta(seconds=120)).isoformat())
            results['missed_ir'] = [blocked.status, blocked.diagnostic_code, blocked.plan.command]

            # Scenario 3: Shelly stale: even desired OFF doesn't permit a toggle.
            stale = reconcile(desired=FanState(), observed=PowerObservation(
                True, BASE - timedelta(minutes=20), watts=11.0), now=BASE)
            database.save_state_snapshot(desired=FanState(), observed=PowerObservation(
                True, BASE - timedelta(minutes=20), watts=11.0), outcome=stale,
                at_utc=BASE.isoformat())
            if stale.diagnostic_code:
                database.save_diagnostic(code=stale.diagnostic_code, detail=stale.reason, at_utc=BASE.isoformat())
            results['stale_power'] = [stale.status, stale.diagnostic_code, stale.plan.command]

            # Scenario 4: nominal exhaust with warmer hallway, but actual cooling.
            samples = [ThermalSample(BASE + timedelta(minutes=5 * i),
                                     bedroom_f=55 - 0.4 * i, outside_f=30,
                                     hallway_f=69) for i in range(5)]
            alerts = check_temperature_trend(samples, confirmed_running=True,
                                             verified_direction='exhaust')
            for alert in alerts:
                database.save_diagnostic(code=alert.code, detail=alert.detail,
                                         severity=alert.severity, at_utc=samples[-1].at.isoformat())
            results['exhaust_trend'] = [a.code for a in alerts]
            with database.connect() as conn:
                results['sqlite'] = {
                    'snapshots': conn.execute('SELECT COUNT(*) FROM device_state_snapshots').fetchone()[0],
                    'simulated_commands': conn.execute('SELECT COUNT(*) FROM command_attempts').fetchone()[0],
                    'diagnostics': conn.execute('SELECT COUNT(*) FROM diagnostic_events').fetchone()[0],
                }
    return results


if __name__ == '__main__':
    print(json.dumps(run_scenarios(), indent=2))
    print('Simulation only: no physical commands, network calls, or production DB writes.')
