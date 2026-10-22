"""Commissioned endpoint reconciliation for unattended intake operation.

Watts never prove direction or speed. Direction is an operator-owned contract;
Endpoint power bands provide a working speed estimate, not physical verification.
Ordinary live/manual operation retains its existing verification requirements.
"""
import argparse
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from statistics import median

import database
from delivery_store import DeliveryBlocked, DeliveryStore
from device_plan import command_buttons
from hardware_runtime import HardwareRuntime, build_runtime
from settings import load_delivery_settings, load_power_settings
from safety_state import reconcile

COMMISSION_FILE = Path('data/fan_commissioning.json')
COMMISSION_FAULT_FILE = Path('data/fan_commissioning_fault.json')


def installation_fingerprint():
    """Invalidate commissioning when hardware, packets, or power settings change."""
    power = load_power_settings()
    codebook = Path(os.getenv('BROADLINK_CODES_FILE') or 'data/remote_codes.json')
    from devices.broadlink_local import CommandBook
    book = CommandBook.from_file(codebook)
    for command in ('speed_up', 'speed_down', 'power_toggle'):
        book.packet(command)
    values = [os.getenv('SHELLY_HOST'), os.getenv('BROADLINK_HOST'),
              repr(power), hashlib.sha256(codebook.read_bytes()).hexdigest()]
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()


def load_commissioning(path=None):
    path = path or COMMISSION_FILE
    config = json.loads(path.read_text())
    if (config.get('version') != 1 or config.get('exclusive_control') is not True
            or config.get('direction') != 'intake'
            or config.get('fingerprint') != installation_fingerprint()):
        raise ValueError('Missing or invalid commissioning; recommission after hardware/configuration changes')
    at = datetime.fromisoformat(config['commissioned_at_utc'])
    if at.tzinfo is None or at > datetime.now(timezone.utc):
        raise ValueError('Invalid commissioning timestamp')
    if config.get('initial_speed') not in (1, 4):
        raise ValueError('Invalid initial endpoint')
    low, high = config['bands']['1'], config['bands']['4']
    from math import isfinite
    if (len(low) != 2 or len(high) != 2
            or not all(isinstance(v, (int, float)) and isfinite(v) for v in low + high)
            or not load_power_settings().calibration.on_min_w <= low[0] < low[1] < high[0] < high[1]):
        raise ValueError('Endpoint power bands must be finite, ordered and nonoverlapping')
    return config


def expected_speed(config):
    """A durable commanded estimate, never a general watts-to-speed classifier."""
    with database.connect() as conn:
        row = conn.execute('SELECT id,command FROM delivery_attempts '
            'WHERE resolved=1 AND started_at_utc>=? ORDER BY rowid DESC LIMIT 1',
            (config['commissioned_at_utc'],)).fetchone()
        if row is None:
            return config['initial_speed']
        attempt, command = row
        if command not in ('set_low', 'set_high'):
            # Power restoration does not establish restored speed.
            return None
        event = conn.execute("SELECT details_json FROM delivery_events WHERE attempt_id=? "
            "AND phase='resolved' ORDER BY id DESC LIMIT 1", (attempt,)).fetchone()
    evidence = json.loads(event[0]) if event else {}
    if evidence.get('source') == 'manual_verification':
        return evidence.get('speed') if evidence.get('speed') in (1, 4) else None
    if evidence.get('source') == 'commissioned_endpoint_model':
        return 1 if command == 'set_low' else 4
    return None


class CommissionedObserver:
    def __init__(self, observer, config):
        self.raw = observer
        self.config = config
        self.latest = None

    def reset(self, **kwargs):
        self.raw.reset(**kwargs)
        self.latest = None

    def matches(self, speed):
        # Allow ordinary drift without letting the two endpoint bands overlap.
        # A median rejects a brief spike; running/standby still requires the
        # entire raw window to agree and uses the original calibrated thresholds.
        low, high = self.config['bands']['1'], self.config['bands']['4']
        padding = min(1.0, (high[0] - low[1]) / 3)
        band = self.config['bands'][str(speed)]
        samples = self.raw.samples
        return bool(self.latest and self.latest.running is True and len(samples) >= 3
                    and samples[-1][0] - samples[0][0] >= self.raw.settings.window_s
                    and band[0] - padding <= median(s[3] for s in samples) <= band[1] + padding)

    def __call__(self):
        self.latest = observed = self.raw()
        # Use fresh settled power to estimate the endpoint, independently of
        # command history. Intermediate speeds may overlap these bands.
        speed = next((s for s in (1, 4) if self.matches(s)), None)
        # Intake remains the operator's contract, never a wattage inference.
        return replace(observed, direction='intake' if observed.running else None,
                       speed=speed)


class UnattendedRuntime(HardwareRuntime):
    def _record(self, desired, observed, status, note, run_id, attempt=None):
        database.save_hardware_observation(device='shelly', result='ok', status={
            'purpose': 'commissioned_endpoint_model', 'run_id': run_id,
            'direction_basis': 'operator_exclusive_intake_contract',
            'modeled_direction': 'intake', 'modeled_speed': observed.speed,
            'speed_basis': 'padded_median_power_band',
            'physical_speed_direction_verified': False})
        return super()._record(desired, replace(observed, direction=None, speed=None),
                               status, note + '; intake is commissioned, not sensed', run_id, attempt)

    def tick(self, desired, *, run_id=None, exclusive_control=False):
        if not self.delivery.settings.enabled or not exclusive_control:
            raise PermissionError('Live control requires enabled delivery and exclusive control')
        # Cover observation, recovery, and delivery with one lock. A second
        # process must not retire an attempt while its owner is still sending.
        with self.delivery.store.guard():
            return self._tick(desired, run_id=run_id)

    def _tick(self, desired, *, run_id):
        pending = self.delivery.store.current()
        self.delivery.invalidate_display()
        if pending:
            attempt, command = pending
            if command not in ('power_toggle', 'set_low', 'set_high'):
                return self._record(desired, self.observer(), 'blocked',
                    'Pending direction action requires physical inspection', run_id, attempt)
            _, _, started = self.delivery.store.pending(attempt)
            at = self.delivery.store.last_send_time(attempt) or started
            elapsed = (self.utcnow() - datetime.fromisoformat(at)).total_seconds()
            if elapsed < 0:
                raise DeliveryBlocked('Pending attempt timestamp is in the future')
            self.sleep(max(0, self.power_settings.settle_s - elapsed))
        self.observer.reset(invalidate_manual=True)
        observed = self.observer()
        try:
            self.delivery._validate_evidence(observed)
        except DeliveryBlocked as exc:
            return self._record(desired, observed, 'blocked', str(exc), run_id,
                                pending[0] if pending else None)
        if pending:
            # Retire uncertainty using NEW measured power, not the old intended
            # result. Replan toggles from that state; never replay a toggle.
            if observed.measured_at <= datetime.fromisoformat(at):
                raise DeliveryBlocked('Recovery requires power evidence later than the attempt')
            self.delivery.store.resolve(attempt, self.utcnow(),
                source='fresh_power_reconciliation', previous_command=command,
                running=observed.running, measured_at=observed.measured_at.isoformat(),
                physical_speed_direction_verified=False)

        # At most a power action followed by an endpoint action. Unconfirmed
        # delivery stops this evaluation and is reconciled on the NEXT tick.
        for action_index in range(2):
            def observe_for_delivery():
                fresh = self.observer()
                if action_index and fresh.running is not True:
                    raise DeliveryBlocked('Fan state changed after start; reconcile next evaluation')
                return fresh

            try:
                observed = observe_for_delivery()
                decision = reconcile(desired=desired, observed=observed, now=self.utcnow(),
                    max_observation_age_s=self.delivery.settings.observation_max_age_s)
                if decision.status != 'ready':
                    return self._record(desired, observed, decision.status, decision.reason, run_id)
                # The caller owns the delivery lock for the entire evaluation.
                attempt = self.delivery._execute(desired, observe_for_delivery, run_id=run_id)
            except DeliveryBlocked as exc:
                pending = self.delivery.store.current()
                return self._record(desired, observed, 'blocked', str(exc), run_id,
                                    pending[0] if pending else None)
            command = self.delivery.store.pending(attempt)[0]
            self.observer.reset(invalidate_manual=True)
            self.sleep(self.power_settings.settle_s)
            observed = self.observer()
            if command == 'power_toggle':
                try:
                    self.delivery._confirm_power(attempt, observed)
                except DeliveryBlocked:
                    return self._record(desired, observed, 'blocked',
                        'Power action unconfirmed; reconcile fresh power next evaluation', run_id, attempt)
                self.delivery.invalidate_display()
                if desired.mode == 'off':
                    return self._record(desired, observed, 'power_confirmed',
                        'Settled power confirms OFF', run_id, attempt)
                continue

            speed = 1 if command == 'set_low' else 4
            with database.connect() as conn:
                events = conn.execute('SELECT phase,details_json FROM delivery_events '
                    'WHERE attempt_id=? ORDER BY id', (attempt,)).fetchall()
            attempts = [json.loads(d)['command'] for p, d in events if p == 'action_attempt']
            acks = [json.loads(d)['command'] for p, d in events if p == 'action_acknowledged']
            expected = list(command_buttons(command))
            if (attempts != expected or acks != expected
                    or sum(p == 'action_completed' for p, _ in events) != 1
                    or any(p == 'uncertain' for p, _ in events)
                    or not self.observer.matches(speed)):
                return self._record(desired, observed, 'blocked',
                    'Endpoint sequence/power check failed; reconcile next evaluation', run_id, attempt)
            self.delivery._validate_evidence(observed)
            at = self.delivery.store.action_time(attempt)
            if at is None or observed.measured_at <= datetime.fromisoformat(at):
                raise DeliveryBlocked('Endpoint needs later settled power evidence')
            self.delivery.store.resolve(attempt, self.utcnow(),
                source='commissioned_endpoint_model', speed=speed,
                physical_speed_direction_verified=False,
                measured_at=observed.measured_at.isoformat(),
                basis='Complete acknowledged endpoint sequence plus padded median settled power agreement')
            self.delivery.invalidate_display()
            return self._record(desired, replace(observed, speed=speed), 'endpoint_consistent',
                'Requested endpoint reasserted with settled power agreement', run_id, attempt)


def run_unattended(desired, delivery_settings, *, run_id):
    if os.getenv('UNATTENDED_CONTROL_ENABLED', 'false').strip().lower() != 'true':
        raise PermissionError('Set UNATTENDED_CONTROL_ENABLED=true explicitly before unattended control')
    config = load_commissioning()
    base = build_runtime(delivery_settings, run_id=run_id)
    observer = CommissionedObserver(base.observer, config)
    runtime = UnattendedRuntime(base.delivery, observer, base.power_settings)
    return runtime.tick(desired, run_id=run_id, exclusive_control=True)


def main():
    parser = argparse.ArgumentParser(description='Commission exclusive intake operation; sends no IR')
    parser.add_argument('--exclusive-control', action='store_true', required=True)
    parser.add_argument('--verified-direction', choices=('intake',), required=True)
    parser.add_argument('--verified-speed', choices=('low', 'high'), required=True)
    parser.add_argument('--low-band', nargs=2, type=float, required=True, metavar=('MIN_W', 'MAX_W'))
    parser.add_argument('--high-band', nargs=2, type=float, required=True, metavar=('MIN_W', 'MAX_W'))
    args = parser.parse_args()
    delivery = load_delivery_settings()
    base = build_runtime(delivery)
    store = DeliveryStore()
    with store.guard():
        if store.current():
            raise DeliveryBlocked('Resolve pending delivery before commissioning')
        observed = base.observer()
        speed = 1 if args.verified_speed == 'low' else 4
        config = {'version': 1, 'exclusive_control': True, 'direction': 'intake',
            'initial_speed': speed, 'commissioned_at_utc': datetime.now(timezone.utc).isoformat(),
            'fingerprint': installation_fingerprint(),
            'bands': {'1': args.low_band, '4': args.high_band}}
        wrapper = CommissionedObserver(base.observer, config)
        wrapper.latest = observed
        if not wrapper.matches(speed):
            raise ValueError('Current settled power disagrees with the physically verified endpoint')
        COMMISSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = COMMISSION_FILE.with_suffix('.tmp')
        temporary.write_text(json.dumps(config, indent=2) + '\n')
        load_commissioning(temporary)
        temporary.replace(COMMISSION_FILE)
        COMMISSION_FAULT_FILE.unlink(missing_ok=True)
    print('Intake contract commissioned. No IR sent. Automation overrides remote power/speed changes.')


if __name__ == '__main__':
    main()
