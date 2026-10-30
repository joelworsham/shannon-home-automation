"""Read-only inspection of local decisions, state, command and diagnostic history."""

import argparse
import json
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent / "data" / "bedroom.sqlite3"


def main():
    parser = argparse.ArgumentParser(description="Inspect local climate history (read-only)")
    parser.add_argument("--decisions", type=int, default=10, metavar="N",
                        help="Print last N decisions, including unchanged states (default: 10)")
    parser.add_argument("--configs", action="store_true", help="Show all configuration activations/changes")
    parser.add_argument("--failures", action="store_true", help="Show recent controller errors")
    parser.add_argument("--hardware", type=int, default=0, metavar="N",
                        help="Show last N local hardware probes")
    parser.add_argument("--states", type=int, default=0, metavar="N",
                        help="Show N recent desired/commanded/observed state snapshots")
    parser.add_argument("--commands", type=int, default=0, metavar="N",
                        help="Show N command attempts (simulation only for now)")
    parser.add_argument("--diagnostics", type=int, default=0, metavar="N",
                        help="Show N recent anomalies and failure events")
    parser.add_argument('--deliveries', type=int, default=0, metavar='N',
                        help='Show guarded delivery attempts and their events')
    args = parser.parse_args()
    for key in ("states", "commands", "diagnostics", "deliveries"):
        if not 0 <= getattr(args, key) <= 1000:
            parser.error(f"--{key} must be 0–1000")
    if args.hardware < 0 or args.hardware > 1000:
        parser.error("--hardware must be 0–1000")
    if args.decisions < 0 or args.decisions > 1000:
        parser.error("--decisions must be 0–1000")
    if not DB.exists():
        parser.error(f"No SQLite history found: {DB}")

    with sqlite3.connect(f"file:{DB}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        if args.decisions:
            print("DECISIONS (latest first, UTC):")
            try:
                columns = {r[1] for r in conn.execute('PRAGMA table_info(control_decisions)')}
                schedule_columns = ('active_block, automation_mode, high_at_f' if 'active_block' in columns
                                    else 'NULL AS active_block, NULL AS automation_mode, NULL AS high_at_f')
                rows = conn.execute("""
                    SELECT recorded_at_utc, bedroom_temp_f, outdoor_temp_f,
                           target_f, restart_f, previous_mode, previous_speed,
                           desired_mode, desired_speed, reason, state_changed, command_sent,
                           """ + schedule_columns + """
                    FROM control_decisions ORDER BY id DESC LIMIT ?
                """, (args.decisions,)).fetchall()
                for r in rows:
                    temperatures = (f"target={r['target_f']}°F restart={r['restart_f']}°F" if r['target_f'] is not None
                                    else 'no temperature target')
                    schedule = ''
                    if r['automation_mode']:
                        schedule = f"block={r['active_block'] or '(gap)'} automation={r['automation_mode']}"
                        if r['automation_mode'] == 'temperature':
                            schedule += f" high={r['high_at_f'] if r['high_at_f'] is not None else 'disabled'}"
                        schedule += ' | '
                    print(f"{r['recorded_at_utc']} | room={r['bedroom_temp_f']}°F "
                          f"outside={r['outdoor_temp_f']}°F {temperatures} | {schedule}"
                          f"{r['previous_mode']}/{r['previous_speed']} -> "
                          f"{r['desired_mode']}/{r['desired_speed']} | "
                          f"change={r['state_changed']} sent={r['command_sent']} | {r['reason']}")
            except sqlite3.OperationalError as exc:
                parser.error(f"History schema not up to date. Run controller.py first. ({exc})")
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='schedule_state'").fetchone():
                state = conn.execute('SELECT phase,updated_at_utc FROM schedule_state WHERE id=1').fetchone()
                if state:
                    print(f'SCHEDULE CONTROL: {state[0]} (last transition {state[1]})')
        if args.configs:
            print("\nCONFIGURATION ACTIVATIONS (oldest first, UTC):")
            for row in conn.execute("""
                SELECT ca.recorded_at_utc, ca.changed_values_json, cs.config_json
                FROM config_activations ca
                JOIN config_snapshots cs ON cs.id=ca.current_snapshot_id
                ORDER BY ca.id
            """):
                print(row[0])
                print("  Changes:", json.dumps(json.loads(row[1]), indent=2))
                print("  Effective:", json.dumps(json.loads(row[2]), indent=2))
        if args.hardware:
            print("\nHARDWARE OBSERVATIONS (latest first, UTC):")
            try:
                for row in conn.execute("""
                    SELECT recorded_at_utc,device,result,status_json,error_type
                    FROM hardware_observations ORDER BY id DESC LIMIT ?
                """, (args.hardware,)):
                    details = json.loads(row[3]) if row[3] else {}
                    print(f"{row[0]} | {row[1]} | {row[2]} | {details} | {row[4] or ''}")
            except sqlite3.OperationalError as exc:
                parser.error(f"History schema not up to date. Run controller.py first. ({exc})")
        if args.states:
            print("\nDEVICE STATE SNAPSHOTS (latest first, UTC):")
            for row in conn.execute("""
                SELECT recorded_at_utc,source,desired_mode,desired_speed,
                       last_command,observed_running,observed_watts,
                       verified_direction,verified_speed,reconciliation_status,
                       planned_command,note
                FROM device_state_snapshots ORDER BY id DESC LIMIT ?
            """, (args.states,)):
                print(" | ".join(str(v) if v is not None else "unknown" for v in row))
        if args.commands:
            print("\nCOMMAND ATTEMPTS (latest first, UTC):")
            for row in conn.execute("""
                SELECT recorded_at_utc,command,disposition,expected_running,note
                FROM command_attempts ORDER BY id DESC LIMIT ?
            """, (args.commands,)):
                print(" | ".join(str(v) if v is not None else "unknown" for v in row))
        if args.diagnostics:
            print("\nDIAGNOSTICS (latest first, UTC):")
            for row in conn.execute("""
                SELECT recorded_at_utc,source,code,severity,detail
                FROM diagnostic_events ORDER BY id DESC LIMIT ?
            """, (args.diagnostics,)):
                print(" | ".join(str(v) for v in row))
        if args.failures:
            print("\nFAILED RUNS (UTC):")
            for row in conn.execute("""
                SELECT started_at_utc, stage, error_type, error_detail
                FROM controller_runs WHERE status='error' ORDER BY id DESC LIMIT 20
            """):
                print(" | ".join(str(x) for x in row if x is not None))
        if args.deliveries:
            print('\nGUARDED DELIVERIES (latest first, UTC):')
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='delivery_attempts'").fetchone():
                print('No guarded delivery history yet.')
            else:
                for row in conn.execute('SELECT id,command,expected_running,started_at_utc,resolved '
                                        'FROM delivery_attempts ORDER BY rowid DESC LIMIT ?', (args.deliveries,)):
                    print(f'{row[0]} | {row[3]} | {row[1]} | expected_running={row[2]} | resolved={bool(row[4])}')
                    for event in conn.execute('SELECT at_utc,phase,details_json FROM delivery_events WHERE attempt_id=? ORDER BY id', (row[0],)):
                        print(f'  {event[0]} | {event[1]} | {event[2]}')


if __name__ == "__main__":
    main()
