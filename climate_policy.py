"""Hardware-independent Vornado climate policy (dry run, no network or device calls).

All temperatures are Fahrenheit. Decisions express DESIRED state, not verified
physical state. An IR-only controller cannot confirm power/speed/direction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from math import isfinite
from zoneinfo import ZoneInfo

TIMEZONE = ZoneInfo("America/Indiana/Indianapolis")
START = time(22, 0)
END = time(7, 30)


@dataclass(frozen=True)
class PolicyConfig:
    target_f: float = 60.0
    restart_f: float = 64.0
    min_cooling_advantage_f: float = 5.0
    speed_hysteresis_f: float = 0.5
    # Thresholds for increasing fan speed, ordered 1->2, 2->3, 3->4.
    speed_thresholds_f: tuple[float, float, float] = (67.0, 70.0, 74.0)


@dataclass(frozen=True)
class FanState:
    # "off" or "intake". Reserved future mode: "exhaust" (not enabled).
    mode: str = "off"
    speed: int = 0

    def __post_init__(self) -> None:
        if self.mode == "off" and self.speed != 0:
            raise ValueError("Off must have speed 0")
        if self.mode == "intake" and self.speed not in (1, 2, 3, 4):
            raise ValueError("Intake speed must be 1–4")
        if self.mode not in ("off", "intake"):
            raise ValueError(f"Unsupported mode: {self.mode}")


@dataclass(frozen=True)
class Decision:
    desired: FanState
    reason: str
    scheduled: bool


def schedule_active(when: datetime) -> bool:
    """Local 22:00–07:30 schedule, including DST changes."""
    if when.tzinfo is None or when.utcoffset() is None:
        raise ValueError("when must be timezone-aware")
    local_time = when.astimezone(TIMEZONE).time().replace(tzinfo=None)
    return local_time >= START or local_time < END


def _initial_speed(temp_f: float, config: PolicyConfig) -> int:
    t12, t23, t34 = config.speed_thresholds_f
    if temp_f >= t34:
        return 4
    if temp_f >= t23:
        return 3
    if temp_f >= t12:
        return 2
    return 1


def _speed_with_hysteresis(temp_f: float, previous_speed: int, config: PolicyConfig) -> int:
    """Adjust by at most one level per 5-minute cycle; avoid threshold chatter."""
    thresholds = config.speed_thresholds_f
    if previous_speed < 4 and temp_f >= thresholds[previous_speed - 1] + config.speed_hysteresis_f:
        return previous_speed + 1
    if previous_speed > 1 and temp_f < thresholds[previous_speed - 2] - config.speed_hysteresis_f:
        return previous_speed - 1
    return previous_speed


def evaluate(
    *,
    when: datetime,
    bedroom_f: float,
    outdoor_f: float,
    previous: FanState = FanState(),
    config: PolicyConfig = PolicyConfig(),
) -> Decision:
    """Evaluate desired fan state; never sends hardware commands.

    Between 60 and 64 F, prior desired state decides whether to continue intake.
    This state comes from the database on normal runs. It is not proof the
    physical fan received the command.
    """
    if not all(isfinite(float(x)) for x in (bedroom_f, outdoor_f)):
        raise ValueError("Temperature readings must be finite numbers")
    if not config.target_f < config.restart_f:
        raise ValueError("restart_f must exceed target_f")
    if not schedule_active(when):
        return Decision(FanState(), "Outside 22:00–07:30 schedule", False)
    if bedroom_f <= config.target_f:
        return Decision(FanState(), "Bedroom at or below cooling target", True)
    if (bedroom_f - outdoor_f) < config.min_cooling_advantage_f:
        return Decision(FanState(), "Outdoor cooling advantage insufficient", True)

    if previous.mode != "intake" and bedroom_f < config.restart_f:
        return Decision(FanState(), "Waiting for 64°F restart threshold", True)

    if previous.mode == "intake":
        speed = _speed_with_hysteresis(bedroom_f, previous.speed, config)
        reason = "Continue cooling; adjust speed if needed"
    else:
        speed = _initial_speed(bedroom_f, config)
        reason = "Start cooling"

    return Decision(FanState("intake", speed), reason, True)
