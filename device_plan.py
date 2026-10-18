"""Pure, cautious Vornado IR reconciliation plan.

For the standard Transom, power and airflow direction are toggle commands;
speed is adjusted incrementally. Shelly power readings distinguish likely
running/standby after calibration but do not prove direction or speed.

This module never controls a physical device and makes no vendor API calls.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from climate_policy import FanState


@dataclass(frozen=True)
class PowerCalibration:
    # Values are intentionally REQUIRED. No universal wattage values are safe.
    off_max_w: float
    on_min_w: float

    def __post_init__(self):
        if not all(isfinite(v) for v in (self.off_max_w, self.on_min_w)):
            raise ValueError("Wattage thresholds must be finite")
        if self.off_max_w < 0 or self.on_min_w <= self.off_max_w:
            raise ValueError("Need 0 <= off_max_w < on_min_w")


def infer_running(watts: float | None, calibration: PowerCalibration) -> bool | None:
    """None means indeterminate or unavailable; do not toggle the fan."""
    if watts is None or not isfinite(watts) or watts < 0:
        return None
    if watts <= calibration.off_max_w:
        return False
    if watts >= calibration.on_min_w:
        return True
    return None


@dataclass(frozen=True)
class Observation:
    running: bool | None = None
    # Direction and speed are separately tracked, not determined by Shelly.
    direction: str | None = None
    speed: int | None = None

    def __post_init__(self):
        if self.direction not in (None, "intake", "exhaust"):
            raise ValueError("Direction must be intake, exhaust or unknown")
        if self.speed is not None and self.speed not in (1, 2, 3, 4):
            raise ValueError("Speed must be 1–4 or unknown")


@dataclass(frozen=True)
class CommandPlan:
    command: str | None
    reason: str
    blocked: bool = False
    # A non-idempotent IR command must not be repeatedly sent without updating evidence.
    verify_after: bool = False


def plan_next_command(desired: FanState, observed: Observation) -> CommandPlan:
    """Return AT MOST ONE step. Caller must re-observe before proceeding.

    Unknown running state always blocks a power toggle. Starting the fan must
    be followed by new power measurement; direction and speed never assumed.
    This is a planning API only; no command delivery is implemented yet.
    """
    if observed.running is None:
        return CommandPlan(None, "Power state unknown; wait for Shelly evidence", blocked=True)
    if desired.mode == "off":
        if observed.running:
            return CommandPlan("power_toggle", "Fan is running but desired OFF", verify_after=True)
        return CommandPlan(None, "Fan already OFF")
    # Currently only cooling-intake is part of the active policy.
    if desired.mode != "intake":
        return CommandPlan(None, "Unsupported desired mode; no safe action", blocked=True)
    if not observed.running:
        return CommandPlan("power_toggle", "Fan is OFF and intake cooling is requested; verify start", verify_after=True)
    if observed.direction is None:
        return CommandPlan(None, "Airflow direction unknown; require initial calibration", blocked=True)
    if observed.direction != "intake":
        return CommandPlan("direction_toggle", "Fan running in exhaust; request intake; verify manually", verify_after=True)
    if observed.speed is None:
        return CommandPlan(None, "Fan speed unknown; calibrate speed before incremental commands", blocked=True)
    if observed.speed < desired.speed:
        return CommandPlan("speed_up", "Raise fan speed by one step", verify_after=True)
    if observed.speed > desired.speed:
        return CommandPlan("speed_down", "Lower fan speed by one step", verify_after=True)
    return CommandPlan(None, "Fan already at desired intake speed")
