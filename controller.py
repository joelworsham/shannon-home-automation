"""Single canonical controller. Dry-run by default; guarded live mode is opt-in.

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
                      save_evaluation, update_stage, finish_hardware_run)
from settings import load_settings, load_delivery_settings
from hardware_runtime import run_live


def _print_result(now, bedroom, outdoor, previous, decision, config, saved: bool, hardware=None) -> None:
    print("=== Bedroom climate controller (GUARDED LIVE) ===" if hardware is not None else
          "=== Bedroom climate controller (DRY RUN — no IR commands) ===")
    print(f"Local time: {now:%Y-%m-%d %H:%M} ({config.policy.timezone})")
    print(f"Bedroom: {bedroom['temp']:.1f}°F; outside: {outdoor['temp']:.1f}°F")
    print(f"Settings: target={config.policy.target_f:g}°F, restart={config.policy.restart_f:g}°F, "
          f"outdoor advantage={config.policy.min_cooling_advantage_f:g}°F")
    speed_names = {0: 'off', 1: 'low', 4: 'high'}
    print(f"Previous desired: {previous.mode} (speed {speed_names[previous.speed]})")
    print(f"Desired: {decision.desired.mode} (speed {speed_names[decision.desired.speed]})")
    print(f"Decision: {decision.reason_code} — {decision.reason}")
    print("SQLite: run, reading, config and decision saved" if saved
          else "SQLite: unchanged (simulation)")
    if hardware is None:
        print("Physical fan: not controlled; desired state is NOT confirmed physical state")
    else:
        print(f"Hardware: {hardware['status']} — {hardware['note']}")
        if hardware.get('attempt_id'):
            print(f"Delivery attempt: {hardware['attempt_id']}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--at", metavar="HH:MM", help="Simulate local time; NO DB writes")
    parser.add_argument("--previous", choices=("off", "intake-low", "intake-high"),
                        help="Override previous desired state (only with --at)")
    parser.add_argument('--live', action='store_true', help='One guarded hardware evaluation; also requires env enable')
    parser.add_argument('--exclusive-control', action='store_true', help='Confirm no app/original-remote use during delivery')
    parser.add_argument('--verified-speed', choices=('low', 'high'), help='Physically verified NOW, never a desired value')
    parser.add_argument('--verified-direction', choices=('intake', 'exhaust'), help='Physically verified NOW')
    args = parser.parse_args()
    args.verified_speed = {'low': 1, 'high': 4}.get(args.verified_speed)
    if args.previous and not args.at:
        parser.error("--previous requires --at")
    if args.live and (args.at or not args.exclusive_control):
        parser.error('--live requires --exclusive-control and cannot be combined with --at')
    if not args.live and (args.exclusive_control or args.verified_speed is not None or args.verified_direction):
        parser.error('Hardware observation flags require --live')
    delivery_settings = None
    if args.live:
        delivery_settings = load_delivery_settings()
        if not delivery_settings.enabled:
            parser.error('Set HARDWARE_CONTROL_ENABLED=true explicitly before --live')
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
                    FanState("intake", 1 if args.previous == "intake-low" else 4) if args.previous else
                    last_desired_state(readonly=True))
        decision = evaluate(when=now, bedroom_f=bedroom["temp"],
                            outdoor_f=outdoor["temp"], previous=previous,
                            config=config.policy)
        _print_result(now, bedroom, outdoor, previous, decision, config, False)
        return

    run_id = begin_run()
    stage = "load_settings"
    hardware = None
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
                        decision=decision, settings=config, live=args.live)
        if args.live:
            stage = 'hardware_control'
            update_stage(run_id, stage)
            try:
                hardware = run_live(decision.desired, delivery_settings, run_id=run_id,
                    exclusive_control=args.exclusive_control, speed=args.verified_speed,
                    direction=args.verified_direction)
            except Exception:
                finish_hardware_run(run_id, {'status': 'error', 'note': 'Hardware stage failed; inspect delivery journal'}, failed=True)
                raise
            finish_hardware_run(run_id, hardware)
    except Exception as exc:
        mark_failed(run_id, stage, exc, live=args.live)
        raise
    _print_result(now, bedroom, outdoor, previous, decision, config, True, hardware)


if __name__ == "__main__":
    main()
