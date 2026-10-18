"""Bedroom climate controller (dry run; no hardware commands).

Read the Flair Puck and outdoor conditions, calculate scheduled desired fan
state, and append real observations/decisions to local SQLite.
Simulations (--at) never write to the database.
"""

from __future__ import annotations

import argparse
from datetime import datetime, time

from check_cooling import get_bedroom, get_outdoor
from climate_policy import FanState, TIMEZONE, evaluate
from control_store import last_desired_state, record_decision
from database import save_reading


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--at", metavar="HH:MM", help="Simulate local time (no writes to SQLite)")
    parser.add_argument(
        "--previous", choices=("off", "intake1", "intake2", "intake3", "intake4"),
        help="Simulate last desired state; only allowed with --at"
    )
    args = parser.parse_args()
    if args.previous and not args.at:
        parser.error("--previous requires --at")

    now = datetime.now(TIMEZONE)
    if args.at:
        t = time.fromisoformat(args.at)
        now = datetime.combine(now.date(), t, tzinfo=TIMEZONE)

    # If either upstream API errors, do not overwrite persisted state.
    bedroom = get_bedroom()
    outdoor = get_outdoor()

    if args.previous:
        previous = (FanState() if args.previous == "off"
                    else FanState("intake", int(args.previous[-1])))
    else:
        previous = last_desired_state()

    decision = evaluate(
        when=now,
        bedroom_f=bedroom["temp"],
        outdoor_f=outdoor["temp"],
        previous=previous,
    )

    if not args.at:
        save_reading(bedroom, outdoor)  # Existing sensor_readings table
        record_decision(
            bedroom_f=bedroom["temp"], outdoor_f=outdoor["temp"],
            previous=previous, decision=decision,
        )

    print("=== Bedroom climate controller (DRY RUN — no IR commands) ===")
    print(f"Indianapolis time: {now:%Y-%m-%d %H:%M}")
    print(f"Bedroom: {bedroom['temp']:.1f}°F; outside: {outdoor['temp']:.1f}°F")
    print(f"Previous desired: {previous.mode} (speed {previous.speed})")
    print(f"Desired: {decision.desired.mode} (speed {decision.desired.speed})")
    print(f"Reason: {decision.reason}")
    print("SQLite: unchanged (simulation)" if args.at else "SQLite: reading + decision saved")
    print("Physical fan: not controlled; desired state is NOT confirmed state")


if __name__ == "__main__":
    main()
