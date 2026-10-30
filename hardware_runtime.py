"""One guarded hardware evaluation, plus explicit no-transmission recovery CLI."""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import os
import time

import database
from delivery_store import DeliveryBlocked, DeliveryStore
from device_plan import CommandPlan
from devices.broadlink_local import BroadLinkLocal, CommandBook
from devices.shelly_local import ShellyPlugUSGen4
from power_observer import SettledPowerObserver
from safety_state import SafetyDecision, reconcile
from settings import load_delivery_settings, load_power_settings
from wake_delivery import WakeAwareDelivery


class HardwareRuntime:
    def __init__(self, delivery, observer, power_settings, *, sleep=time.sleep,
                 utcnow=lambda: datetime.now(timezone.utc)):
        self.delivery, self.observer = delivery, observer
        self.power_settings = power_settings
        self.sleep, self.utcnow = sleep, utcnow

    def _record(self, desired, observed, status, note, run_id, attempt=None):
        result = {'status': status, 'note': note, 'attempt_id': attempt}
        database.save_state_snapshot(desired=desired, observed=observed,
            outcome=SafetyDecision(status, note, CommandPlan(None, note)),
            source='live_control', run_id=run_id)
        return result

    def tick(self, desired, *, run_id=None, exclusive_control=False):
        if not self.delivery.settings.enabled or not exclusive_control:
            raise PermissionError('Live control requires enabled delivery and exclusive control')
        with self.delivery.store.guard():
            return self._tick(desired, run_id=run_id)

    def _tick(self, desired, *, run_id):
        pending = self.delivery.store.current()
        if pending:
            at = self.delivery.store.action_time(pending[0])
            if at:
                remaining = self.power_settings.settle_s - (self.utcnow() - datetime.fromisoformat(at)).total_seconds()
                if remaining > 0:
                    self.sleep(remaining)
            self.observer.reset(invalidate_manual=True)
        observed = self.observer()
        if pending:
            attempt, command = pending
            if command == 'power_toggle':
                try:
                    self.delivery._confirm_power(attempt, observed)
                    return self._record(desired, observed, 'power_confirmed',
                        'Earlier power action confirmed; no new action this run', run_id, attempt)
                except DeliveryBlocked:
                    pass
            return self._record(desired, observed, 'blocked',
                'Unresolved delivery; inspect and confirm or resolve manually. No retry.', run_id, attempt)
        decision = reconcile(desired=desired, observed=observed, now=self.utcnow(),
                             max_observation_age_s=self.delivery.settings.observation_max_age_s)
        if decision.status != 'ready':
            return self._record(desired, observed, decision.status, decision.reason, run_id)
        try:
            attempt = self.delivery._execute(desired, self.observer, run_id=run_id)
        except DeliveryBlocked as exc:
            # Controlled blocking messages contain no host or credentials.
            return self._record(desired, observed, 'blocked', str(exc), run_id)
        command = self.delivery.store.pending(attempt)[0]
        # Do not reuse pre-action power samples or speed/direction attestations.
        self.observer.reset(invalidate_manual=True)
        self.sleep(self.power_settings.settle_s)
        observed = self.observer()
        if command == 'power_toggle':
            try:
                self.delivery._confirm_power(attempt, observed)
                return self._record(desired, observed, 'power_confirmed',
                    'Settled power evidence confirms the action', run_id, attempt)
            except DeliveryBlocked:
                return self._record(desired, observed, 'blocked',
                    'Power action unconfirmed; no retry', run_id, attempt)
        return self._record(desired, observed, 'awaiting_manual_verification',
            'Verify actual speed/direction before another action', run_id, attempt)


def build_runtime(delivery_settings, *, speed=None, direction=None, run_id=None):
    power = load_power_settings()
    meter = ShellyPlugUSGen4(os.getenv('SHELLY_HOST', ''),
        username=os.getenv('SHELLY_USERNAME') or 'admin', password=os.getenv('SHELLY_PASSWORD') or None)
    book = CommandBook.from_file(os.getenv('BROADLINK_CODES_FILE') or 'data/remote_codes.json')
    # Check required wake packet before any physical connection or journal claim.
    book.packet('speed_up')
    blaster = BroadLinkLocal(os.getenv('BROADLINK_HOST', ''), book,
                             permit_transmit=delivery_settings.enabled)
    observer = SettledPowerObserver(meter, power, speed=speed, direction=direction, run_id=run_id)
    return HardwareRuntime(WakeAwareDelivery(blaster, settings=delivery_settings), observer, power)


def run_live(desired, delivery_settings, *, run_id, exclusive_control, speed=None, direction=None):
    runtime = build_runtime(delivery_settings, speed=speed, direction=direction, run_id=run_id)
    database.save_hardware_observation(device='shelly', result='ok', status={
        'purpose': 'live_runtime_configuration', 'run_id': run_id,
        'delivery_settings': asdict(delivery_settings), 'power_settings': asdict(runtime.power_settings),
        'manual_speed': speed, 'manual_direction': direction})
    return runtime.tick(desired, run_id=run_id, exclusive_control=exclusive_control)


def main():
    parser = argparse.ArgumentParser(description='Resolve a delivery after physical inspection; never transmits IR')
    parser.add_argument('--resolve-attempt', required=True)
    parser.add_argument('--note', required=True, help='Physical findings, without private addresses or credentials')
    parser.add_argument('--verified-speed', choices=('low', 'high'))
    parser.add_argument('--verified-direction', choices=('intake', 'exhaust'))
    args = parser.parse_args()
    args.verified_speed = {'low': 1, 'high': 4}.get(args.verified_speed)
    settings = load_delivery_settings()
    power = load_power_settings()
    command, _, _ = DeliveryStore().pending(args.resolve_attempt)
    if command in ('set_low', 'set_high') and args.verified_speed is None:
        parser.error('Speed recovery requires --verified-speed from physical inspection')
    if command == 'direction_toggle' and args.verified_direction is None:
        parser.error('Direction recovery requires --verified-direction from physical inspection')
    meter = ShellyPlugUSGen4(os.getenv('SHELLY_HOST', ''),
        username=os.getenv('SHELLY_USERNAME') or 'admin', password=os.getenv('SHELLY_PASSWORD') or None)
    observer = SettledPowerObserver(meter, power, speed=args.verified_speed, direction=args.verified_direction)
    time.sleep(power.settle_s)
    observed = observer()
    if command in ('set_low', 'set_high') and observed.speed is None:
        parser.error('Speed evidence expired or fan power is uncertain; inspect again')
    if command == 'direction_toggle' and observed.direction is None:
        parser.error('Direction evidence expired or fan power is uncertain; inspect again')
    # No blaster exists here: recovery can only read Shelly and update the journal.
    WakeAwareDelivery(None, settings=settings).resolve_manually(args.resolve_attempt, observed, note=args.note)
    print('Physical inspection recorded; delivery resolved. No IR sent.')


if __name__ == '__main__':
    main()
