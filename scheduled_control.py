"""Small persisted schedule lifecycle, serialized with hardware delivery.

Only explicit live runs advance this state. A gap becomes inactive after a
confirmed shutdown, not merely after accepting or sending an OFF request.
"""
from contextlib import closing, nullcontext
from datetime import datetime, timezone
from dataclasses import replace
import sqlite3
from zoneinfo import ZoneInfo

import database
from climate_policy import FanState
from delivery_store import DeliveryBlocked, DeliveryStore
from hardware_runtime import build_runtime
from schedules import block_decision, gap_decision, select_block


def utcnow():
    return datetime.now(timezone.utc)


def read_phase():
    if not database.DB_PATH.exists():
        return 'inactive'
    with closing(sqlite3.connect(f'file:{database.DB_PATH}?mode=ro', uri=True)) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='schedule_state'").fetchone():
            return 'inactive'
        row = conn.execute('SELECT phase FROM schedule_state WHERE id=1').fetchone()
    phase = row[0] if row else 'inactive'
    if phase not in ('active', 'shutdown_pending', 'inactive'):
        raise ValueError('Invalid persisted schedule state; delivery blocked')
    return phase


def write_phase(phase):
    with database.connect() as conn, conn:
        conn.execute('INSERT INTO schedule_state(id,phase,updated_at_utc) VALUES(1,?,?) '
                     'ON CONFLICT(id) DO UPDATE SET phase=excluded.phase, updated_at_utc=excluded.updated_at_utc',
                     (phase, utcnow().isoformat()))


def make_runtime(args, delivery_settings, run_id):
    if args.unattended:
        from unattended import build_unattended_runtime
        return build_unattended_runtime(delivery_settings, run_id=run_id)
    return build_runtime(delivery_settings, speed=args.verified_speed,
                         direction=args.verified_direction, run_id=run_id)


def guard_schedule(config, block, when):
    """Do not start a new sequence using a block that has since ended."""
    local = when.astimezone(ZoneInfo(config.policy.timezone))
    key = block.occurrence(local) if block else None

    def check():
        now = utcnow()
        current = select_block(now, config.schedule_blocks, config.policy.timezone)
        current_key = current.occurrence(now.astimezone(local.tzinfo)) if current else None
        if current_key != key:
            raise DeliveryBlocked('Schedule changed before delivery; reevaluate next run')
    return check


def run(args, config, delivery_settings, run_id, snapshot_id, get_bedroom, get_outdoor):
    try:
        return _run(args, config, delivery_settings, run_id, snapshot_id, get_bedroom, get_outdoor)
    except Exception:
        if args.live or args.unattended:
            database.finish_hardware_run(run_id, {'status': 'error', 'note': 'Scheduled evaluation failed; inspect run and delivery history'}, failed=True)
        raise


def _run(args, config, delivery_settings, run_id, snapshot_id, get_bedroom, get_outdoor):
    live = args.live or args.unattended
    # The existing lock covers selection, lifecycle state and every hardware
    # operation. No second lock, scheduler process, or command service is needed.
    with DeliveryStore().guard() if live else nullcontext():
        now = utcnow()
        block = select_block(now, config.schedule_blocks, config.policy.timezone)
        phase = read_phase()
        next_phase = 'active' if block else 'shutdown_pending' if phase != 'inactive' else 'inactive'
        if live and next_phase != phase:
            write_phase(next_phase)
        hardware = None
        previous = database.last_desired_state()
        if block:
            database.update_stage(run_id, 'fetch_bedroom')
            bedroom = get_bedroom()
            database.update_stage(run_id, 'fetch_outdoor')
            outdoor = get_outdoor()
            if live:
                runtime = make_runtime(args, delivery_settings, run_id)
                # Manual use during a gap makes the previous desired state
                # unsuitable for temperature hysteresis. Use fresh power.
                runtime.observer.reset(invalidate_manual=False)
                observed = runtime.observer()
                runtime.delivery._validate_evidence(observed)
                if observed.running:
                    speed = observed.speed or (4 if block.high_at_f is not None and bedroom['temp'] >= block.high_at_f else 1)
                    previous = FanState('intake', speed)
                else:
                    previous = FanState()
            decision = block_decision(block, when=now, bedroom_f=bedroom['temp'], outdoor_f=outdoor['temp'],
                                      previous=previous, base=config.policy)
            if live:
                decision = replace(decision, previous_basis='fresh_power_endpoint_estimate')
            database.save_evaluation(run_id=run_id, config_snapshot_id=snapshot_id,
                                     bedroom=bedroom, outdoor=outdoor, previous=previous,
                                     decision=decision, settings=config, live=live)
            if live:
                runtime.delivery.before_sequence = guard_schedule(config, block, now)
                hardware = runtime._tick(decision.desired, run_id=run_id)
        else:
            decision = gap_decision(next_phase == 'shutdown_pending')
            if live:
                if decision.desired is None:
                    hardware = {'status': 'inactive', 'note': 'No active block; fan left under manual control'}
                else:
                    # Shutdown needs calibrated power, but no Flair/weather API.
                    database.update_stage(run_id, 'schedule_shutdown')
                    runtime = make_runtime(args, delivery_settings, run_id)
                    runtime.delivery.before_sequence = guard_schedule(config, None, now)
                    hardware = runtime._tick(decision.desired, run_id=run_id)
                    if hardware['status'] in ('satisfied', 'power_confirmed'):
                        observed = runtime.observer()
                        runtime.delivery._validate_evidence(observed)
                        if observed.running is False:
                            write_phase('inactive')
                            decision = replace(decision, automation_mode='inactive',
                                               reason='Block ended; fan confirmed OFF, automation now inactive')
                        else:
                            hardware = {'status': 'blocked', 'note': 'Earlier delivery reconciled; shutdown remains pending'}
            # Monitoring failures cannot prevent shutdown or make it repeat.
            # A failed collection still appears accurately as an error run.
            database.update_stage(run_id, 'fetch_bedroom')
            bedroom = get_bedroom()
            database.update_stage(run_id, 'fetch_outdoor')
            outdoor = get_outdoor()
            database.save_evaluation(run_id=run_id, config_snapshot_id=snapshot_id,
                                     bedroom=bedroom, outdoor=outdoor, previous=previous,
                                     decision=decision, settings=config, live=live)
        if live:
            database.finish_hardware_run(run_id, hardware)
        return now, bedroom, outdoor, previous, decision, hardware


def simulate(when, config, bedroom, outdoor, previous):
    block = select_block(when, config.schedule_blocks, config.policy.timezone)
    if block:
        return block_decision(block, when=when, bedroom_f=bedroom['temp'], outdoor_f=outdoor['temp'],
                              previous=previous, base=config.policy)
    return gap_decision(read_phase() != 'inactive')
