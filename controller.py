"""Single canonical controller. DRY RUN: never sends commands to real hardware.

Normal runs log every evaluation and failure to SQLite. Simulated --at runs
read live APIs but do not modify any database or fan hardware state.
"""

from __future__ import annotations

import argparse
from datetime import datetime, time
from zoneinfo import ZoneInfo

from check_cooling import get_bedroom, get_outdoor
from climate_policy import FanState, evaluate
from database import (activate_config, begin_run, last_desired_state, mark_failed,
                      save_evaluation, update_stage)
from settings import load_settings


def _print_result(now, bedroom, outdoor, previous, decision, config, saved: bool) -> None:
    print("=== Bedroom climate controller (DRY RUN — no IR commands) ===")
    print(f"Local time: {now:%Y-%m-%d %H:%M} ({config.policy.timezone})")
    print(f"Bedroom: {bedroom['temp']:.1f}°F; outside: {outdoor['temp']:.1f}°F")
    print(f"Settings: target={config.policy.target_f:g}°F, restart={config.policy.restart_f:g}°F, "
          f"outdoor advantage={config.policy.min_cooling_advantage_f:g}°F")
    print(f"Previous desired: {previous.mode} (speed {previous.speed})")
    print(f"Desired: {decision.desired.mode} (speed {decision.desired.speed})")
    print(f"Decision: {decision.reason_code} — {decision.reason}")
    print("SQLite: run, reading, config and decision saved" if saved
          else "SQLite: unchanged (simulation)")
    print("Physical fan: not controlled; desired state is NOT confirmed physical state")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--at", metavar="HH:MM", help="Simulate local time; NO DB writes")
    parser.add_argument("--previous", choices=("off", "intake1", "intake2", "intake3", "intake4"),
                        help="Override previous desired state (only with --at)")
    args = parser.parse_args()
    if args.previous and not args.at:
        parser.error("--previous requires --at")
    if args.at:
        try:
            fake_clock = time.fromisoformat(args.at)
            if fake_clock.tzinfo or fake_clock.second or fake_clock.microsecond:
                raise ValueError
        except ValueError:
            parser.error("--at must be a local HH:MM time")
        config = load_settings()
        tz = ZoneInfo(config.policy.timezone)
        now = datetime.combine(datetime.now(tz).date(), fake_clock, tzinfo=tz)
        bedroom = get_bedroom()
        outdoor = get_outdoor()
        previous = (FanState() if args.previous == "off" else
                    FanState("intake", int(args.previous[-1])) if args.previous else
                    last_desired_state(readonly=True))
        decision = evaluate(when=now, bedroom_f=bedroom["temp"],
                            outdoor_f=outdoor["temp"], previous=previous,
                            config=config.policy)
        _print_result(now, bedroom, outdoor, previous, decision, config, False)
        return

    run_id = begin_run()
    stage = "load_settings"
    try:
        config = load_settings()
        snapshot_id = activate_config(run_id, config)
        stage = "fetch_bedroom"
        update_stage(run_id, stage)
        bedroom = get_bedroom()
        stage = "fetch_outdoor"
        update_stage(run_id, stage)
        outdoor = get_outdoor()
        stage = "evaluate"
        update_stage(run_id, stage)
        previous = last_desired_state()
        now = datetime.now(ZoneInfo(config.policy.timezone))
        decision = evaluate(when=now, bedroom_f=bedroom["temp"],
                            outdoor_f=outdoor["temp"], previous=previous,
                            config=config.policy)
        stage = "save_evaluation"
        save_evaluation(run_id=run_id, config_snapshot_id=snapshot_id,
                        bedroom=bedroom, outdoor=outdoor, previous=previous,
                        decision=decision, settings=config)
    except Exception as exc:
        mark_failed(run_id, stage, exc)
        raise
    _print_result(now, bedroom, outdoor, previous, decision, config, True)


if __name__ == "__main__":
    main()
