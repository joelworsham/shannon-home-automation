"""Pure, cautious Vornado IR reconciliation plan.

For the standard Transom, power and airflow direction are toggle commands;
low/high are established by saturating the physical speed buttons. Shelly power readings distinguish likely
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
        if self.speed is not None and self.speed not in (1, 4):
            raise ValueError("Speed must be low (1), high (4), or unknown")


@dataclass(frozen=True)
class CommandPlan:
    command: str | None
    reason: str
    blocked: bool = False
    # Power/direction toggles require confirmation; endpoint actions require verification.
    verify_after: bool = False


def plan_next_command(desired: FanState, observed: Observation) -> CommandPlan:
    """Return at most one logical action. Re-observe after delivery.

    Unknown running state always blocks a power toggle. Starting the fan must
    be followed by new power measurement; direction and speed never assumed.
    Unknown speed can be replaced by a deterministic low/high endpoint action.
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
    if observed.speed == desired.speed:
        return CommandPlan(None, "Fan already at desired intake speed")
    command = "set_low" if desired.speed == 1 else "set_high"
    return CommandPlan(command, "Establish requested speed endpoint with four button presses", verify_after=True)


def command_buttons(command: str) -> tuple[str, ...]:
    """Expand one logical action; endpoint sequences never depend on starting speed."""
    if command in ("set_low", "set_high"):
        return (("speed_down" if command == "set_low" else "speed_up"),) * 4
    if command in ("power_toggle", "direction_toggle"):
        return (command,)
    raise ValueError(f"Unsupported fan action: {command}")
