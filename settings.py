"""Read and validate non-secret climate settings from .env / environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import time

from dotenv import load_dotenv
from climate_policy import PolicyConfig


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
