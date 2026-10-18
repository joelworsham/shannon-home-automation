"""Read-only inspection of local settings, every decision, and failed runs."""

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
    args = parser.parse_args()
    if args.decisions < 0 or args.decisions > 1000:
        parser.error("--decisions must be 0–1000")
    if not DB.exists():
        parser.error(f"No SQLite history found: {DB}")

    with sqlite3.connect(f"file:{DB}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        if args.decisions:
            print("DECISIONS (latest first, UTC):")
            try:
                rows = conn.execute("""
                    SELECT recorded_at_utc, bedroom_temp_f, outdoor_temp_f,
                           target_f, restart_f, previous_mode, previous_speed,
                           desired_mode, desired_speed, reason, state_changed, command_sent
                    FROM control_decisions ORDER BY id DESC LIMIT ?
                """, (args.decisions,)).fetchall()
                for r in rows:
                    print(f"{r['recorded_at_utc']} | room={r['bedroom_temp_f']}°F "
                          f"outside={r['outdoor_temp_f']}°F target={r['target_f']}°F "
                          f"restart={r['restart_f']}°F | "
                          f"{r['previous_mode']}/{r['previous_speed']} -> "
                          f"{r['desired_mode']}/{r['desired_speed']} | "
                          f"change={r['state_changed']} sent={r['command_sent']} | {r['reason']}")
            except sqlite3.OperationalError as exc:
                parser.error(f"History schema not up to date. Run controller.py first. ({exc})")
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
        if args.failures:
            print("\nFAILED RUNS (UTC):")
            for row in conn.execute("""
                SELECT started_at_utc, stage, error_type, error_detail
                FROM controller_runs WHERE status='error' ORDER BY id DESC LIMIT 20
            """):
                print(" | ".join(str(x) for x in row if x is not None))


if __name__ == "__main__":
    main()
