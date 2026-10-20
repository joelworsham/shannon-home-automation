"""Opt-in delivery foundation used only by explicitly enabled live control.

Exclusive control is a caller contract: no app/physical-remote activity during
delivery. Display timing is an estimate, never a verified physical state.
"""
from datetime import datetime, timezone
import time

from delivery_store import DeliveryBlocked, DeliveryStore
from safety_state import reconcile
from settings import DeliverySettings


class WakeAwareDelivery:
    def __init__(self, blaster, *, settings=None, store=None,
                 monotonic=time.monotonic, sleep=time.sleep,
                 utcnow=lambda: datetime.now(timezone.utc)):
        self.blaster = blaster
        self.settings = settings or DeliverySettings()
        self.store = store or DeliveryStore()
        self.clock, self.sleep, self.utcnow = monotonic, sleep, utcnow
        # Never restore an awake estimate across process restarts.
        self._awake_until = None
        self._generation = 0

    def invalidate_display(self):
        """Call on manual/app activity or loss of exclusive control."""
        self._generation += 1
        self._awake_until = None

    @property
    def display_estimate(self):
        if self._awake_until is not None and self.clock() < self._awake_until:
            return 'assumed_awake'
        return 'unknown'

    def _plan(self, desired, observe):
        observation = observe()
        result = reconcile(desired=desired, observed=observation, now=self.utcnow(),
                           max_observation_age_s=self.settings.observation_max_age_s)
        if result.status != 'ready':
            raise DeliveryBlocked(result.reason)
        return observation, result.plan.command

    def execute(self, desired, observe, *, exclusive_control=False, run_id=None):
        """Deliver one policy action, then leave a durable verification barrier.

        observe() supplies freshly verified PowerObservation, not a wattage-
        guessed speed/direction. The caller owns calibrated power classification.
        Unknown display state requires a full quiet interval then speed_up to
        wake. This relies on exclusive control and the configured sleep timing.
        """
        if not self.settings.enabled:
            raise PermissionError('Hardware delivery is disabled')
        if not exclusive_control:
            raise DeliveryBlocked('Exclusive control is required for wake sequencing')
        with self.store.guard():
            return self._execute(desired, observe, run_id=run_id)

    def _execute(self, desired, observe, *, run_id=None):
        observed, command = self._plan(desired, observe)
        expected = not observed.running if command == 'power_toggle' else None
        attempt = self.store.claim(command, expected, self.utcnow(), run_id=run_id)
        generation = self._generation
        try:
            self.store.event(attempt, 'planned', self.utcnow(), command=command,
                             display_estimate=self.display_estimate,
                             display_sleep_s=self.settings.display_sleep_s,
                             wake_delay_s=self.settings.wake_delay_s)
            if self.display_estimate != 'assumed_awake':
                self.store.event(attempt, 'quiet_wait', self.utcnow(),
                                 seconds=self.settings.display_sleep_s + self.settings.timing_margin_s)
                self.sleep(self.settings.display_sleep_s + self.settings.timing_margin_s)
                fresh, fresh_command = self._plan(desired, observe)
                if fresh_command != command or fresh.running != observed.running:
                    raise DeliveryBlocked('State changed during quiet interval')
                if generation != self._generation:
                    raise DeliveryBlocked('External activity invalidated display timing')
                self._send(attempt, 'wake', 'speed_up')
                self.sleep(self.settings.wake_delay_s)
            # Recheck evidence before the action; observing may consume time.
            fresh, fresh_command = self._plan(desired, observe)
            if fresh_command != command or fresh.running != observed.running:
                raise DeliveryBlocked('State changed before action; verify wake effects')
            if generation != self._generation or self.display_estimate != 'assumed_awake':
                raise DeliveryBlocked('Awake window expired or was invalidated; no action')
            self._send(attempt, 'action', command)
            return attempt
        except BaseException as exc:
            self.invalidate_display()
            # Claim remains unresolved even if recording the failure also fails.
            self.store.event(attempt, 'uncertain', self.utcnow(), error_type=type(exc).__name__)
            raise

    def _send(self, attempt, phase, command):
        # Commit intent BEFORE transmission: a crash cannot invite a repeat.
        self.store.event(attempt, phase + '_attempt', self.utcnow(), command=command)
        started = self.clock()
        if phase == 'action' and (self._awake_until is None or started >= self._awake_until):
            raise DeliveryBlocked('Awake window expired while recording intent')
        self.blaster.send(command)
        # Conservative estimate from send start, not late acknowledgement time.
        self._awake_until = started + self.settings.display_sleep_s - self.settings.timing_margin_s
        self.store.event(attempt, phase + '_acknowledged', self.utcnow(), command=command,
                         display_estimate='assumed_awake', physical_result='unverified')

    def confirm_power(self, attempt, observed):
        """Clear barrier only with later, fresh, matching calibrated evidence."""
        with self.store.guard():
            self._confirm_power(attempt, observed)

    def _confirm_power(self, attempt, observed):
        command, expected, _ = self.store.pending(attempt)
        at = self.store.action_time(attempt)
        self._validate_evidence(observed)
        if (command != 'power_toggle' or at is None or
                observed.measured_at <= datetime.fromisoformat(at) or
                observed.running is not bool(expected)):
            raise DeliveryBlocked('Power action lacks fresh matching confirmation')
        self.store.resolve(attempt, self.utcnow(), source='calibrated_power_observation',
                           running=observed.running, measured_at=observed.measured_at.isoformat())

    def resolve_manually(self, attempt, observed, *, note):
        """Explicit operator recovery/verification, never an automatic retry.

        Used for speed/direction, failed sends, and interrupted attempts.
        The next policy evaluation must use the newly observed physical state.
        """
        with self.store.guard():
            self._resolve_manually(attempt, observed, note=note)

    def _resolve_manually(self, attempt, observed, *, note):
        _, _, started = self.store.pending(attempt)
        self._validate_evidence(observed)
        latest = self.store.last_send_time(attempt) or started
        if not note.strip() or observed.measured_at <= datetime.fromisoformat(latest):
            raise DeliveryBlocked('Manual resolution needs later evidence and a note')
        # Recovery invalidates display estimates, even if the fan was inspected.
        self.invalidate_display()
        self.store.resolve(attempt, self.utcnow(), source='manual_verification',
                           running=observed.running, direction=observed.direction,
                           speed=observed.speed, measured_at=observed.measured_at.isoformat(), note=note)

    def _validate_evidence(self, observed):
        now = self.utcnow()
        if (observed.running is None or observed.measured_at is None or
                not 0 <= (now - observed.measured_at).total_seconds() <= self.settings.observation_max_age_s):
            raise DeliveryBlocked('Fresh verified running/standby evidence is required')
