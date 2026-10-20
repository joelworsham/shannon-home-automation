"""Read and validate non-secret climate settings from .env / environment."""

from __future__ import annotations

import os
from math import isfinite
from dataclasses import dataclass
from datetime import time

from dotenv import load_dotenv
from climate_policy import PolicyConfig
from device_plan import PowerCalibration


@dataclass(frozen=True)
class PowerSettings:
    calibration: PowerCalibration
    sample_interval_s: float = 1.0
    window_s: float = 2.0
    settle_s: float = 6.0
    manual_evidence_max_age_s: float = 30.0

    def __post_init__(self):
        values = (self.sample_interval_s, self.window_s, self.settle_s,
                  self.manual_evidence_max_age_s)
        if not all(isfinite(v) and v > 0 for v in values):
            raise ValueError('Power observation timings must be positive and finite')
        if self.window_s < 2 * self.sample_interval_s:
            raise ValueError('Power window must span at least three samples')


def load_power_settings() -> PowerSettings:
    load_dotenv(override=False)
    off = _optional_number('FAN_STANDBY_MAX_W')
    on = _optional_number('FAN_RUNNING_MIN_W')
    if off is None or on is None:
        raise ValueError('Set FAN_STANDBY_MAX_W and FAN_RUNNING_MIN_W before live control')
    return PowerSettings(
        calibration=PowerCalibration(off, on),
        sample_interval_s=_number('FAN_POWER_SAMPLE_INTERVAL_S', 1),
        window_s=_number('FAN_POWER_WINDOW_S', 2),
        settle_s=_number('FAN_POWER_SETTLE_S', 6),
        manual_evidence_max_age_s=_number('FAN_MANUAL_EVIDENCE_MAX_AGE_S', 30),
    )


@dataclass(frozen=True)
class DeliverySettings:
    enabled: bool = False
    display_sleep_s: float = 6.0
    wake_delay_s: float = 1.0
    timing_margin_s: float = 0.25
    observation_max_age_s: float = 10.0

    def __post_init__(self):
        if not isinstance(self.enabled, bool):
            raise ValueError('Delivery enabled must be a boolean')
        values = (self.display_sleep_s, self.wake_delay_s,
                  self.timing_margin_s, self.observation_max_age_s)
        if not all(isfinite(v) and v > 0 for v in values):
            raise ValueError('Delivery timing values must be positive and finite')
        if self.wake_delay_s + 2 * self.timing_margin_s >= self.display_sleep_s:
            raise ValueError('Wake delay needs room inside the display awake window')


def load_delivery_settings() -> DeliverySettings:
    load_dotenv(override=False)
    enabled = os.getenv('HARDWARE_CONTROL_ENABLED', 'false').strip().lower()
    if enabled not in ('true', 'false'):
        raise ValueError('HARDWARE_CONTROL_ENABLED must be true or false')
    return DeliverySettings(
        enabled=enabled == 'true',
        display_sleep_s=_number('FAN_DISPLAY_SLEEP_S', 6),
        wake_delay_s=_number('FAN_WAKE_DELAY_S', 1),
        timing_margin_s=_number('FAN_TIMING_MARGIN_S', 0.25),
        observation_max_age_s=_number('HARDWARE_OBSERVATION_MAX_AGE_S', 10),
    )


@dataclass(frozen=True)
class ControllerSettings:
    policy: PolicyConfig
    weather_location: str

    def as_public_dict(self) -> dict:
        """Safe to write to SQLite or Git: never contains Flair credentials."""
        return {"climate": self.policy.as_dict(), "weather_location": self.weather_location}


def _number(name: str, fallback: float) -> float:
    raw = os.getenv(name, str(fallback))
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc


def _optional_number(name: str) -> float | None:
    """Absent or blank speed thresholds disable that speed tier."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number or left blank") from exc


def _clock(name: str, fallback: str) -> time:
    raw = os.getenv(name, fallback)
    try:
        parsed = time.fromisoformat(raw)
        if parsed.tzinfo is not None or parsed.second or parsed.microsecond:
            raise ValueError
        return parsed
    except ValueError as exc:
        raise ValueError(f"{name} must be HH:MM in 24-hour format") from exc


def load_settings() -> ControllerSettings:
    # Real environment variables override the .env file if present.
    load_dotenv(override=False)
    policy = PolicyConfig(
        target_f=_number("CLIMATE_TARGET_F", 60),
        restart_f=_number("CLIMATE_RESTART_F", 64),
        min_cooling_advantage_f=_number("CLIMATE_MIN_COOLING_ADVANTAGE_F", 5),
        speed_hysteresis_f=_number("CLIMATE_SPEED_HYSTERESIS_F", 0.5),
        speed_thresholds_f=(
            _optional_number("CLIMATE_SPEED_2_AT_F"),
            _optional_number("CLIMATE_SPEED_3_AT_F"),
            _optional_number("CLIMATE_SPEED_4_AT_F"),
        ),
        schedule_start=_clock("CLIMATE_SCHEDULE_START", "22:00"),
        schedule_end=_clock("CLIMATE_SCHEDULE_END", "07:30"),
        timezone=os.getenv("CLIMATE_TIMEZONE", "America/Indiana/Indianapolis").strip(),
    )
    location = os.getenv("WEATHER_LOCATION", "Indianapolis").strip()
    if not location:
        raise ValueError("WEATHER_LOCATION must not be blank")
    return ControllerSettings(policy=policy, weather_location=location)
