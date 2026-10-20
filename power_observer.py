"""Read-only power windows; speed/direction are explicit, expiring attestations."""
from dataclasses import asdict
from datetime import datetime, timezone
import time

from database import save_hardware_observation
from device_plan import Observation, infer_running
from safety_state import PowerObservation


class SettledPowerObserver:
    def __init__(self, meter, settings, *, speed=None, direction=None, run_id=None,
                 sleep=time.sleep, monotonic=time.monotonic,
                 utcnow=lambda: datetime.now(timezone.utc)):
        Observation(None, direction, speed)
        self.meter, self.settings = meter, settings
        self.speed, self.direction = speed, direction
        self.sleep, self.clock, self.utcnow = sleep, monotonic, utcnow
        self.manual_at = self.clock()
        self.run_id = run_id
        self.samples = []

    def reset(self, *, invalidate_manual=False):
        self.samples.clear()
        if invalidate_manual:
            self.speed = self.direction = None

    def __call__(self):
        # A cached window is only usable if its newest sample is recent.
        if self.samples and self.clock() - self.samples[-1][0] > self.settings.sample_interval_s * 2:
            self.samples.clear()
        deadline = self.clock() + self.settings.window_s + self.settings.sample_interval_s
        while True:
            try:
                status = self.meter.read_status()
                now = self.utcnow()
                measured = datetime.fromisoformat(status.read_at_utc)
                if measured.tzinfo is None or not 0 <= (now - measured).total_seconds() <= 2:
                    raise ValueError('Invalid or stale Shelly observation timestamp')
                if self.samples and measured <= self.samples[-1][1]:
                    raise ValueError('Shelly observation timestamp did not advance')
                save_hardware_observation(device='shelly', result='ok', status={
                    **asdict(status), 'purpose': 'controller_power_window', 'run_id': self.run_id})
            except Exception as exc:
                self.samples.clear()
                save_hardware_observation(device='shelly', result='error', error=exc)
                raise
            at = self.clock()
            state = infer_running(status.watts, self.settings.calibration) if status.relay_on else None
            self.samples.append((at, measured, state))
            # Retain one sample at/before the window boundary plus newer samples.
            while len(self.samples) > 1 and self.samples[1][0] <= at - self.settings.window_s:
                self.samples.pop(0)
            span = at - self.samples[0][0]
            ready = len(self.samples) >= 3 and span >= self.settings.window_s
            if ready or at >= deadline or not status.relay_on:
                states = [sample[2] for sample in self.samples]
                running = states[0] if ready and states[0] is not None and all(s is states[0] for s in states) else None
                manual_fresh = 0 <= at - self.manual_at <= self.settings.manual_evidence_max_age_s
                return PowerObservation(running, measured,
                    self.direction if manual_fresh and running else None,
                    self.speed if manual_fresh and running else None, status.watts)
            self.sleep(self.settings.sample_interval_s)
