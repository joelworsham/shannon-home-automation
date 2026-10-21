"""Pure climate policy: desired state only, never a claim about fan hardware."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from math import isfinite
from zoneinfo import ZoneInfo

TIMEZONE = ZoneInfo("America/Indiana/Indianapolis")


@dataclass(frozen=True)
class PolicyConfig:
    target_f: float = 60.0
    restart_f: float = 64.0
    min_cooling_advantage_f: float = 5.0
    speed_hysteresis_f: float = 0.5
    high_at_f: float | None = None
    schedule_start: time = time(22, 0)
    schedule_end: time = time(7, 30)
    timezone: str = "America/Indiana/Indianapolis"

    def __post_init__(self) -> None:
        temps = (self.target_f, self.restart_f, self.min_cooling_advantage_f,
                 self.speed_hysteresis_f)
        if self.high_at_f is not None:
            temps += (self.high_at_f,)
        if any(not isfinite(v) for v in temps):
            raise ValueError("Climate settings must be finite numbers")
        if self.restart_f <= self.target_f:
            raise ValueError("CLIMATE_RESTART_F must exceed CLIMATE_TARGET_F")
        if self.min_cooling_advantage_f < 0 or self.speed_hysteresis_f < 0:
            raise ValueError("Cooling advantage and speed hysteresis must be nonnegative")
        if self.schedule_start == self.schedule_end:
            raise ValueError("Schedule start and end must differ")
        ZoneInfo(self.timezone)  # Reject misspelled IANA time zone names.

    def as_dict(self) -> dict:
        return {
            "target_f": self.target_f,
            "restart_f": self.restart_f,
            "min_cooling_advantage_f": self.min_cooling_advantage_f,
            "speed_hysteresis_f": self.speed_hysteresis_f,
            "high_at_f": self.high_at_f,
            "schedule_start": self.schedule_start.strftime("%H:%M"),
            "schedule_end": self.schedule_end.strftime("%H:%M"),
            "timezone": self.timezone,
        }


@dataclass(frozen=True)
class FanState:
    mode: str = "off"
    speed: int = 0

    def __post_init__(self) -> None:
        if self.mode == "off" and self.speed != 0:
            raise ValueError("Off mode requires speed 0")
        if self.mode == "intake" and self.speed not in (1, 4):
            raise ValueError("Intake speed must be low (1) or high (4)")
        if self.mode not in ("off", "intake"):
            raise ValueError(f"Unsupported mode: {self.mode}")


@dataclass(frozen=True)
class Decision:
    desired: FanState
    reason: str
    scheduled: bool
    reason_code: str = "unspecified"


def schedule_active(when: datetime, config: PolicyConfig = PolicyConfig()) -> bool:
    if when.tzinfo is None or when.utcoffset() is None:
        raise ValueError("when must be timezone-aware")
    local_time = when.astimezone(ZoneInfo(config.timezone)).time().replace(tzinfo=None)
    start, end = config.schedule_start, config.schedule_end
    if start < end:
        return start <= local_time < end
    return local_time >= start or local_time < end


def _initial_speed(temp_f: float, config: PolicyConfig) -> int:
    return 4 if config.high_at_f is not None and temp_f >= config.high_at_f else 1


def _speed_with_hysteresis(temp_f: float, previous_speed: int, config: PolicyConfig) -> int:
    threshold = config.high_at_f
    if threshold is None:
        return 1
    if threshold <= config.target_f:
        return 4
    if previous_speed == 1 and temp_f >= threshold + config.speed_hysteresis_f:
        return 4
    if previous_speed == 4 and temp_f < threshold - config.speed_hysteresis_f:
        return 1
    return previous_speed


def evaluate(
    *,
    when: datetime,
    bedroom_f: float,
    outdoor_f: float,
    previous: FanState = FanState(),
    config: PolicyConfig = PolicyConfig(),
) -> Decision:
    if any(not isfinite(v) for v in (bedroom_f, outdoor_f)):
        raise ValueError("Temperature readings must be finite")
    if not schedule_active(when, config):
        hours = f"{config.schedule_start:%H:%M}–{config.schedule_end:%H:%M}"
        return Decision(FanState(), f"Outside {hours} schedule", False, "outside_schedule")
    if bedroom_f <= config.target_f:
        return Decision(FanState(), f"Bedroom at/below {config.target_f:g}°F target", True, "at_target")
    if bedroom_f - outdoor_f < config.min_cooling_advantage_f:
        return Decision(FanState(), "Outdoor cooling advantage insufficient", True, "outside_not_cool_enough")
    if previous.mode != "intake" and bedroom_f < config.restart_f:
        return Decision(FanState(), f"Waiting for {config.restart_f:g}°F restart threshold", True, "below_restart")
    if previous.mode == "intake":
        speed = _speed_with_hysteresis(bedroom_f, previous.speed, config)
        return Decision(FanState("intake", speed), "Continue cooling; adjust speed if needed", True, "continue_cooling")
    return Decision(FanState("intake", _initial_speed(bedroom_f, config)), "Start cooling", True, "start_cooling")
