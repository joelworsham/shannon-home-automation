"""Pure safety reconciliation. Planning only: NO network, transmit, or switching APIs.

'Observed' is a power-meter/manual observation, never an inference from a
transmitted IR command. The Vornado power/direction commands toggle, so we
must never auto-repeat a command merely because a timeout elapsed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import isfinite

from climate_policy import FanState
from device_plan import CommandPlan, Observation, plan_next_command


def _aware(moment: datetime) -> None:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError('State-machine timestamps must be timezone-aware')


@dataclass(frozen=True)
class PowerObservation:
    """Current power status; direction/speed are separately MANUALLY verified.

    `running=None` must be used before Shelly power thresholds are calibrated.
    `direction` and `speed` must be None unless explicitly verified outside of
    wattage readings (e.g. physical observation during calibration).
    """
    running: bool | None
    measured_at: datetime | None = None
    direction: str | None = None
    speed: int | None = None
    watts: float | None = None

    def __post_init__(self):
        if self.running is not None and not isinstance(self.running, bool):
            raise ValueError('running must be bool or None')
        if self.measured_at is not None:
            _aware(self.measured_at)
        if self.watts is not None and (not isfinite(self.watts) or self.watts < 0):
            raise ValueError('Invalid watts')
        Observation(self.running, self.direction, self.speed)


@dataclass(frozen=True)
class PendingCommand:
    """A transmitted command awaiting fresh evidence, not a confirmed action."""
    command: str
    sent_at: datetime
    expected_running: bool | None = None

    def __post_init__(self):
        _aware(self.sent_at)
        if self.command not in ('power_toggle', 'direction_toggle', 'speed_up', 'speed_down'):
            raise ValueError('Unsupported Vornado command')
        if self.command == 'power_toggle' and self.expected_running is None:
            raise ValueError('Power toggle requires expected running state')
        if self.command != 'power_toggle' and self.expected_running is not None:
            raise ValueError('Non-power commands cannot prove power state')


@dataclass(frozen=True)
class SafetyDecision:
    status: str
    reason: str
    plan: CommandPlan
    diagnostic_code: str | None = None
    # Caller can clear a pending power command only after confirmation.
    power_confirmed: bool = False


def _stop(status: str, reason: str, diagnostic: str | None = None) -> SafetyDecision:
    return SafetyDecision(status, reason, CommandPlan(None, reason, blocked=True), diagnostic)


def reconcile(
    *, desired: FanState, observed: PowerObservation, now: datetime,
    pending: PendingCommand | None = None,
    max_observation_age_s: int = 600, confirmation_timeout_s: int = 90,
) -> SafetyDecision:
    """Propose at most one next IR command; never execute it.

    A pending power command is confirmed *only* by a later power observation.
    Direction/speed pending commands require explicit manual verification in
    this conservative first version; wattage cannot prove those effects.
    """
    _aware(now)
    if max_observation_age_s <= 0 or confirmation_timeout_s <= 0:
        raise ValueError('Timeouts must be positive')
    sample_time = observed.measured_at
    if sample_time is not None and sample_time > now:
        return _stop('blocked', 'Power observation timestamp is in the future', 'invalid_observation_time')
    if pending is not None:
        if pending.sent_at > now:
            return _stop('blocked', 'Pending command timestamp is in the future', 'invalid_command_time')
        elapsed = (now - pending.sent_at).total_seconds()
        if pending.command != 'power_toggle':
            if elapsed >= confirmation_timeout_s:
                return _stop('blocked', 'Incremental/direction command needs manual verification; no automatic retry',
                             'manual_verification_required')
            return _stop('awaiting_confirmation', 'Direction/speed not verifiable by watts')
        if (sample_time is not None and sample_time > pending.sent_at
                and observed.running is pending.expected_running
                and (now - sample_time).total_seconds() <= max_observation_age_s):
            return SafetyDecision('power_confirmed', 'Fresh Shelly reading matches intended power state',
                                  CommandPlan(None, 'Await next control evaluation'),
                                  power_confirmed=True)
        if elapsed >= confirmation_timeout_s:
            return _stop('blocked', 'Power toggle unconfirmed; do not repeat', 'power_toggle_unconfirmed')
        return _stop('awaiting_confirmation', 'Waiting for a fresh confirming Shelly observation')
    if sample_time is None:
        return _stop('blocked', 'No timestamped Shelly measurement available', 'missing_power_observation')
    if (now - sample_time).total_seconds() > max_observation_age_s:
        return _stop('blocked', 'Shelly measurement is stale', 'stale_power_observation')
    if observed.running is None:
        return _stop('blocked', 'Fan power state is unknown or uncalibrated', 'unknown_power_state')
    plan = plan_next_command(desired, Observation(observed.running, observed.direction, observed.speed))
    return SafetyDecision('blocked' if plan.blocked else ('ready' if plan.command else 'satisfied'),
                          plan.reason, plan,
                          'unknown_airflow_or_speed' if plan.blocked else None)
