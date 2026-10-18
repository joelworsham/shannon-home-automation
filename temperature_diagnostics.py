"""Conservative temperature-trend anomaly detection (advisory, never actuation).

Room temperature behavior alone cannot prove fan direction, command delivery,
or whether warm hallway air is actually entering through the door gap.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import isfinite


@dataclass(frozen=True)
class ThermalSample:
    at: datetime
    bedroom_f: float
    outside_f: float
    hallway_f: float | None = None

    def __post_init__(self):
        if self.at.tzinfo is None or self.at.utcoffset() is None:
            raise ValueError('Trend samples require timezone-aware datetimes')
        if not all(isfinite(t) for t in (self.bedroom_f, self.outside_f)):
            raise ValueError('Invalid temperature')
        if self.hallway_f is not None and not isfinite(self.hallway_f):
            raise ValueError('Invalid hallway temperature')


@dataclass(frozen=True)
class TrendAlert:
    code: str
    severity: str
    detail: str
    delta_f: float
    duration_minutes: float


def check_temperature_trend(
    samples: list[ThermalSample], *,
    confirmed_running: bool | None,
    verified_direction: str | None,
    max_gap_minutes: float = 12,
    min_window_minutes: float = 15,
) -> list[TrendAlert]:
    """Require 3+ ordered, sufficiently dense samples and independent fan evidence.

    Avoid alerts for dry-run desired states or unverified assumed direction.
    Outside temperature advantage and hallway contrast must hold throughout
    the sample window, not just at its endpoints.
    """
    if len(samples) < 3 or confirmed_running is None:
        return []
    if verified_direction not in (None, 'intake', 'exhaust'):
        raise ValueError('verified_direction must be intake, exhaust or None')
    if any(b.at <= a.at or (b.at - a.at).total_seconds() > max_gap_minutes * 60
           for a, b in zip(samples, samples[1:])):
        return []
    minutes = (samples[-1].at - samples[0].at).total_seconds() / 60
    if minutes < min_window_minutes:
        return []
    delta = samples[-1].bedroom_f - samples[0].bedroom_f
    def alert(code, detail):
        return [TrendAlert(code, 'warning', detail, round(delta, 3), round(minutes, 1))]
    if not confirmed_running and delta <= -2.0 and minutes >= 30:
        return alert('rapid_cooling_while_off', 'Unusually fast cooling despite verified fan-off; check leakage or HVAC')
    if confirmed_running and verified_direction == 'intake':
        if all(s.bedroom_f - s.outside_f >= 5 for s in samples) and delta >= 0.75:
            return alert('warming_during_intake', 'Bedroom warmed despite verified intake and cooler outside air')
    if confirmed_running and verified_direction == 'exhaust':
        if all(s.hallway_f is not None and s.hallway_f - s.bedroom_f >= 5 for s in samples) and delta <= -1.0:
            return alert('cooling_during_exhaust', 'Bedroom cooled during exhaust with warmer hallway; investigate airflow')
    return []
