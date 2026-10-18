"""Offline demonstration of planned IR commands. Never sends a real IR signal."""
import argparse
from dataclasses import asdict
from datetime import datetime
from zoneinfo import ZoneInfo

from climate_policy import FanState, PolicyConfig, evaluate
from settings import load_settings
from device_plan import Observation, PowerCalibration, infer_running, plan_next_command
from devices.interfaces import FakeBroadLink, FakeShelly


def simulate(*, bedroom_f: float, outdoor_f: float, at: str, watts: float | None,
             off_max_w: float, on_min_w: float, direction: str | None, speed: int | None,
             config: PolicyConfig | None = None) -> dict:
    """Show decision + one potential command using *illustrative* calibration."""
    config = config or PolicyConfig()
    local = datetime.now(ZoneInfo(config.timezone))
    hour, minute = map(int, at.split(":"))
    when = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    # In a true live implementation, previous desired state comes from the DB.
    # Here it is deliberately assumed OFF as a simple first-evaluation example.
    decision = evaluate(when=when, bedroom_f=bedroom_f, outdoor_f=outdoor_f,
                        previous=FanState(), config=config)
    meter = FakeShelly(watts)
    running = infer_running(meter.read_watts(), PowerCalibration(off_max_w, on_min_w))
    observation = Observation(running=running, direction=direction, speed=speed)
    plan = plan_next_command(decision.desired, observation)
    fake_ir = FakeBroadLink()
    # Simulated command is recorded in memory only. Not a physical command.
    if plan.command is not None:
        fake_ir.send(plan.command)
    return {
        "desired": asdict(decision.desired),
        "decision_reason": decision.reason,
        "observed": asdict(observation),
        "plan": asdict(plan),
        "fake_commands": fake_ir.commands,
        "hardware_contacted": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bedroom', type=float, default=66)
    parser.add_argument('--outside', type=float, default=40)
    parser.add_argument('--at', default='23:00')
    parser.add_argument('--watts', type=float, default=None)
    parser.add_argument('--off-max', type=float, default=2,
                        help='SIMULATION ONLY: example standby cutoff (not calibrated)')
    parser.add_argument('--on-min', type=float, default=8,
                        help='SIMULATION ONLY: example running cutoff (not calibrated)')
    parser.add_argument('--direction', choices=['intake', 'exhaust', 'unknown'], default='unknown')
    parser.add_argument('--speed', type=int, choices=[1, 2, 3, 4])
    args = parser.parse_args()
    direction = None if args.direction == 'unknown' else args.direction
    import json
    print(json.dumps(simulate(bedroom_f=args.bedroom, outdoor_f=args.outside, at=args.at,
                              watts=args.watts, off_max_w=args.off_max, on_min_w=args.on_min,
                              direction=direction, speed=args.speed,
                              config=load_settings().policy), indent=2))


if __name__ == '__main__':
    main()
