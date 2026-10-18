
import argparse
from datetime import datetime, time
from zoneinfo import ZoneInfo
from database import save_reading

from check_cooling import (
    get_bedroom,
    get_outdoor,
    evaluate,
)

TIMEZONE = ZoneInfo("America/Indiana/Indianapolis")
START_TIME = time(22, 0)
END_TIME = time(7, 30)


def is_scheduled_time(current_time):
    # Handles schedules that cross midnight.
    return (
        current_time >= START_TIME
        or current_time < END_TIME
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--at",
        help="Simulate a local time, e.g. 23:00",
    )
    args = parser.parse_args()

    now = datetime.now(TIMEZONE)
    current_time = (
        time.fromisoformat(args.at)
        if args.at
        else now.time()
    )

    bedroom = get_bedroom()
    outdoor = get_outdoor()

    # Store real readings during normal runs,
    # but not during simulated schedule tests.
    if not args.at:
        save_reading(bedroom, outdoor)
        print("Sensor readings saved to SQLite.")

    if not is_scheduled_time(current_time):
        action = "STOP"
        reason = "Outside scheduled operating hours"
    else:
        action, reason = evaluate(
            bedroom["temp"],
            outdoor["temp"],
        )

    print("=== Vornado Controller (DRY RUN) ===")
    print(f"Time: {current_time:%H:%M}")
    print(f"Schedule active: {is_scheduled_time(current_time)}")
    print(f"Bedroom: {bedroom['temp']:.1f}°F")
    print(f"Outside: {outdoor['temp']:.1f}°F")
    print()
    print(f"Decision: {action}")
    print(f"Reason: {reason}")
    print("No hardware commands sent.")


if __name__ == "__main__":
    main()
