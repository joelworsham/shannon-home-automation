"""Daily blocks and persisted shutdown lifecycle; no network or real hardware."""
from dataclasses import replace
from datetime import datetime, time, timezone
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import controller
import database
import history
import scheduled_control
from climate_policy import FanState, PolicyConfig
from delivery_store import DeliveryBlocked, DeliveryStore
from schedules import ScheduleBlock, block_decision, load_blocks, select_block, validate_blocks
from settings import ControllerSettings, load_settings
from tests import test_unattended as unattended_fixture


def block(name='sleep', start='22:00', end='07:30', target=60, restart=62, high=60):
    return ScheduleBlock(name, time.fromisoformat(start), time.fromisoformat(end), target, restart, high)


class BlockTests(unittest.TestCase):
    def test_boundaries_overnight_and_adjacent(self):
        blocks = (block(), block('morning', '07:30', '09:00', 64, 68, None))
        validate_blocks(blocks)
        for clock, expected in [('21:59', None), ('22:00', 'sleep'), ('00:00', 'sleep'),
                                ('07:29', 'sleep'), ('07:30', 'morning'), ('09:00', None)]:
            with self.subTest(clock=clock):
                now = datetime.fromisoformat('2026-10-30T' + clock + ':00-04:00')
                selected = select_block(now, blocks, 'America/Indiana/Indianapolis')
                self.assertEqual(selected.name if selected else None, expected)

    def test_overlap_and_duplicate_names_rejected(self):
        for blocks in ((block(), block('other', '07:00', '08:00')),
                       (block(), block('other', '23:00', '23:30')),
                       (block(), block('other', '21:00', '23:00')),
                       (block(), block('sleep', '12:00', '13:00'))):
            with self.subTest(blocks=blocks), self.assertRaises(ValueError):
                validate_blocks(blocks)

    def test_dst_fold_and_spring_gap_use_local_wall_clock(self):
        blocks = (block(start='01:00', end='02:30'),)
        tz = 'America/Indiana/Indianapolis'
        for utc in ('2026-11-01T05:15:00+00:00', '2026-11-01T06:15:00+00:00',
                    '2026-03-08T06:59:00+00:00'):
            self.assertIsNotNone(select_block(datetime.fromisoformat(utc), blocks, tz))
        self.assertIsNone(select_block(datetime.fromisoformat('2026-03-08T07:00:00+00:00'), blocks, tz))
        with self.assertRaises(ValueError):
            select_block(datetime(2026, 10, 30), blocks, tz)

    def test_effective_thresholds_and_hysteresis(self):
        selected = block('day', '08:00', '18:00', 68, 72, None)
        now = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
        for previous, expected in ((FanState(), FanState()), (FanState('intake', 4), FanState('intake', 1))):
            result = block_decision(selected, when=now, bedroom_f=70, outdoor_f=40,
                                    previous=previous, base=PolicyConfig(timezone='UTC'))
            self.assertEqual(result.desired, expected)
            self.assertEqual(result.active_block, 'day')
            self.assertEqual(result.effective_policy.target_f, 68)

    def test_json_validation_and_settings(self):
        with tempfile.TemporaryDirectory() as folder, patch('settings.load_dotenv'), patch.dict(os.environ, {}, clear=True):
            path = Path(folder) / 'schedule.json'
            good = block().as_dict()
            path.write_text(json.dumps([good]))
            with patch.dict(os.environ, {'CLIMATE_SCHEDULE_FILE': str(path)}):
                settings = load_settings()
                self.assertEqual(settings.schedule_blocks, (block(),))
                self.assertEqual(settings.as_public_dict()['climate']['blocks'], [good])
                self.assertNotIn('target_f', settings.as_public_dict()['climate'])
            for raw in ({}, [dict(good, start='1:00')], [dict(good, end='22:00')],
                        [dict(good, target_f=None)], [dict(good, target_f=True)],
                        [dict(good, restart_f=59)], [dict(good, high_at_f=float('nan'))],
                        [dict(good, name='')], [dict(good, extra=1)], [dict(name='incomplete')]):
                with self.subTest(raw=raw):
                    path.write_text(json.dumps(raw))
                    with self.assertRaises(ValueError):
                        load_blocks(path)
            path.write_text('[]')
            self.assertEqual(load_blocks(path), ())
            with patch.dict(os.environ, {'CLIMATE_SCHEDULE_FILE': str(path),
                                         'CLIMATE_TARGET_F': 'invalid legacy setting',
                                         'CLIMATE_SCHEDULE_START': 'bad legacy time'}):
                self.assertEqual(load_settings().schedule_blocks, ())
            path.write_text('broken json')
            with self.assertRaises(ValueError):
                load_blocks(path)
            with self.assertRaises(ValueError):
                load_blocks(Path(folder) / 'missing.json')


class ScheduledRuntimeTests(unittest.TestCase):
    make_observer = unattended_fixture.UnattendedTests.make_observer

    def setUp(self):
        unattended_fixture.UnattendedTests.setUp(self)
        self.schedule = ControllerSettings(PolicyConfig(timezone='UTC'), 'test',
                                           (block(start='22:00', end='01:00'),))
        self.args = SimpleNamespace(live=False, unattended=True, verified_speed=None, verified_direction=None)
        self.bedroom = Mock(return_value={'temp': 70})
        self.outdoor = Mock(return_value={'temp': 40})
        for p in (patch('scheduled_control.utcnow', self.clock.now),
                  patch('scheduled_control.make_runtime', return_value=self.runtime)):
            p.start()
            self.addCleanup(p.stop)

    def run_schedule(self):
        run_id = database.begin_run()
        snapshot = database.activate_config(run_id, self.schedule)
        return scheduled_control.run(self.args, self.schedule, self.delivery.settings, run_id, snapshot,
                                     self.bedroom, self.outdoor)

    def test_shutdown_once_then_manual_changes_persist_across_restarts(self):
        self.run_schedule()
        self.assertEqual(scheduled_control.read_phase(), 'active')
        self.clock.value = 3600
        result = self.run_schedule()
        self.assertEqual(result[4].reason_code, 'schedule_shutdown')
        self.assertFalse(self.running)
        self.assertEqual(scheduled_control.read_phase(), 'inactive')
        count = len(self.commands)
        self.running, self.actual_speed = True, 2  # original remote during gap
        self.clock.value += 300
        for _ in range(2):
            result = self.run_schedule()
            self.assertEqual(result[5]['status'], 'inactive')
            self.assertIsNone(result[4].desired)
        self.assertTrue(self.running)
        self.assertEqual(self.actual_speed, 2)
        self.assertEqual(len(self.commands), count)
        with database.connect() as conn:
            self.assertEqual(conn.execute('SELECT desired_mode,target_f,restart_f,high_at_f FROM control_decisions ORDER BY id DESC LIMIT 1').fetchone(),
                             ('inactive', None, None, None))
        with patch.object(history, 'DB', database.DB_PATH), patch('sys.argv', ['history.py']), \
             patch('sys.stdout', new_callable=io.StringIO) as output:
            history.main()
        self.assertIn('automation=inactive', output.getvalue())

    def test_first_start_in_gap_does_not_shutdown_manual_fan(self):
        self.clock.value = 3600
        self.run_schedule()
        self.assertTrue(self.running)
        self.assertEqual(self.commands, [])

    def test_adjacent_block_takes_over_without_shutdown(self):
        self.schedule = replace(self.schedule, schedule_blocks=self.schedule.schedule_blocks +
                                (block('morning', '01:00', '03:00', 65, 69, 65),))
        self.run_schedule()
        self.clock.value = 3600
        result = self.run_schedule()
        self.assertEqual(result[4].active_block, 'morning')
        self.assertEqual(result[4].effective_policy.target_f, 65)
        self.assertTrue(self.running)
        self.assertEqual(self.commands, [])
        with database.connect() as conn:
            self.assertEqual(conn.execute('SELECT active_block,target_f,restart_f,high_at_f FROM control_decisions ORDER BY id DESC LIMIT 1').fetchone(),
                             ('morning', 65, 69, 65))

    def test_unknown_shutdown_power_stays_pending_then_recovers(self):
        self.run_schedule()
        self.clock.value = 3600
        self.relay = False
        self.assertEqual(self.run_schedule()[5]['status'], 'blocked')
        self.assertEqual(scheduled_control.read_phase(), 'shutdown_pending')
        self.assertEqual(self.commands, [])
        self.relay = True
        self.clock.value += 300
        self.run_schedule()
        self.assertFalse(self.running)
        self.assertEqual(scheduled_control.read_phase(), 'inactive')

    def test_sensor_outage_does_not_prevent_shutdown_or_repeat_it(self):
        self.run_schedule()
        self.clock.value = 3600
        self.bedroom.side_effect = TimeoutError('Synthetic sensor outage')
        with self.assertRaises(TimeoutError):
            self.run_schedule()
        self.assertFalse(self.running)
        self.assertEqual(scheduled_control.read_phase(), 'inactive')
        count = len(self.commands)
        self.running = True
        with self.assertRaises(TimeoutError):
            self.run_schedule()
        self.assertTrue(self.running)
        self.assertEqual(len(self.commands), count)

    def test_temperature_policy_uses_actual_manual_state_on_return(self):
        self.clock.value = 3600
        self.run_schedule()  # gap, no control state established
        self.bedroom.return_value = {'temp': 61}  # between target and restart
        self.running, self.actual_speed = True, 4
        self.clock.value = 22 * 3600
        self.assertEqual(self.run_schedule()[4].desired, FanState('intake', 4))
        self.assertEqual(self.commands, [])
        # An actually OFF fan must instead wait for the restart threshold.
        self.running = False
        self.clock.value += 300
        self.assertEqual(self.run_schedule()[4].desired, FanState())
        self.assertEqual(self.commands, [])

    def test_pending_delivery_in_inactive_gap_is_not_recovered_or_transmitted(self):
        self.clock.value = 3600
        attempt = self.delivery.store.claim('set_low', None, self.clock.now())
        self.run_schedule()
        self.assertEqual(self.delivery.store.current()[0], attempt)
        self.assertEqual(self.commands, [])

    def test_dry_run_cannot_consume_live_shutdown_or_change_state(self):
        scheduled_control.write_phase('active')
        self.clock.value = 3600
        self.args.unattended = False
        self.assertEqual(self.run_schedule()[4].reason_code, 'schedule_shutdown')
        self.assertEqual(scheduled_control.read_phase(), 'active')
        self.assertEqual(self.commands, [])

    def test_simulation_does_not_write_database_or_consume_shutdown(self):
        scheduled_control.write_phase('active')
        before = database.DB_PATH.read_bytes()
        decision = scheduled_control.simulate(self.clock.now().replace(hour=12), self.schedule,
                                              {'temp': 70}, {'temp': 40}, FanState())
        self.assertEqual(decision.reason_code, 'schedule_shutdown')
        self.assertEqual(database.DB_PATH.read_bytes(), before)

    def test_concurrent_scheduler_cannot_select_or_change_state(self):
        with DeliveryStore().guard(), self.assertRaises(DeliveryBlocked):
            self.run_schedule()
        self.assertEqual(scheduled_control.read_phase(), 'inactive')
        self.assertEqual(self.commands, [])

    def test_boundary_during_sensor_fetch_blocks_stale_start(self):
        self.running = False
        self.clock.value = 3599
        def read():
            self.clock.value += 5
            return {'temp': 40}
        self.outdoor.side_effect = read
        result = self.run_schedule()
        self.assertEqual(result[5]['status'], 'blocked')
        self.assertEqual(self.commands, [])
        self.outdoor.side_effect = None
        self.run_schedule()
        self.assertEqual(scheduled_control.read_phase(), 'inactive')

    def test_boundary_during_quiet_wait_sends_no_wake(self):
        self.running = False
        self.clock.value = 3593
        result = self.run_schedule()
        self.assertEqual(result[5]['status'], 'blocked')
        self.assertEqual(self.commands, [])
        self.run_schedule()  # resolves reserved, unsent attempt using real power
        self.assertEqual(scheduled_control.read_phase(), 'inactive')

    def test_missed_shutdown_reconciles_without_blind_toggle(self):
        self.run_schedule()
        self.clock.value = 3600
        original = self.blaster.send
        self.blaster.send = lambda cmd: self.commands.append((self.clock.value, cmd))
        self.assertEqual(self.run_schedule()[5]['status'], 'blocked')
        self.assertEqual(scheduled_control.read_phase(), 'shutdown_pending')
        self.blaster.send = original
        self.clock.value += 300
        self.run_schedule()
        self.assertFalse(self.running)
        self.assertEqual(scheduled_control.read_phase(), 'inactive')

    def test_lost_shutdown_ack_reconciles_observed_off_without_second_toggle(self):
        self.run_schedule()
        self.clock.value = 3600
        original = self.blaster.send
        def lose_ack(cmd):
            original(cmd)
            if cmd == 'power_toggle':
                raise TimeoutError('Synthetic lost acknowledgement after physical shutdown')
        self.blaster.send = lose_ack
        with self.assertRaises(TimeoutError):
            self.run_schedule()
        self.assertFalse(self.running)
        self.assertEqual(scheduled_control.read_phase(), 'shutdown_pending')
        count = len(self.commands)
        self.blaster.send = original
        self.clock.value += 300
        self.run_schedule()
        self.assertEqual(scheduled_control.read_phase(), 'inactive')
        self.assertEqual(len(self.commands), count)

    def test_new_block_supersedes_unfinished_shutdown(self):
        scheduled_control.write_phase('shutdown_pending')
        result = self.run_schedule()
        self.assertEqual(result[4].active_block, 'sleep')
        self.assertEqual(scheduled_control.read_phase(), 'active')
        self.assertTrue(self.running)
        self.assertEqual(self.commands, [])

    def test_started_sequence_finishes_across_boundary_then_next_run_shuts_down(self):
        self.actual_speed = 1
        # Schedule wall time crosses the end after the wake; physical display
        # timing continues normally for the rest of the endpoint sequence.
        original_now = self.clock.now
        def schedule_clock():
            now = original_now()
            return now.replace(hour=1) if self.commands else now
        with patch('scheduled_control.utcnow', schedule_clock):
            self.run_schedule()
        self.assertEqual(self.actual_speed, 4)
        self.assertEqual([cmd for _, cmd in self.commands], ['speed_up'] * 5)
        self.clock.value = 3600
        self.run_schedule()
        self.assertFalse(self.running)
        self.assertEqual(scheduled_control.read_phase(), 'inactive')

    def test_empty_schedule_is_inactive_and_malformed_state_blocks_delivery(self):
        self.schedule = replace(self.schedule, schedule_blocks=())
        self.assertEqual(self.run_schedule()[5]['status'], 'inactive')
        self.assertEqual(self.commands, [])
        with database.connect() as conn, conn:
            conn.execute('PRAGMA ignore_check_constraints=ON')
            conn.execute("INSERT INTO schedule_state VALUES(1,'bad-state','2026-10-20T00:00:00+00:00')")
        with self.assertRaises(ValueError):
            self.run_schedule()
        self.assertEqual(self.commands, [])

    def test_controller_routes_blocks_and_reports_local_effective_settings(self):
        with patch.object(controller, 'load_settings', return_value=self.schedule), \
             patch.object(controller, 'load_delivery_settings', return_value=self.delivery.settings), \
             patch.object(controller, 'get_bedroom', self.bedroom), \
             patch.object(controller, 'get_outdoor', self.outdoor), \
             patch('sys.argv', ['controller.py', '--unattended']), \
             patch('sys.stdout', new_callable=io.StringIO) as output:
            controller.main()
        self.assertIn('Schedule: sleep; automation=temperature', output.getvalue())
        self.assertIn('target=60°F, restart=62°F', output.getvalue())

    def test_controller_simulation_with_blocks_creates_no_database(self):
        self.assertFalse(database.DB_PATH.exists())
        with patch.object(controller, 'load_settings', return_value=self.schedule), \
             patch.object(controller, 'get_bedroom', self.bedroom), \
             patch.object(controller, 'get_outdoor', self.outdoor), \
             patch('sys.argv', ['controller.py', '--at', '23:00', '--previous', 'off']), \
             patch('sys.stdout', new_callable=io.StringIO) as output:
            controller.main()
        self.assertIn('Schedule: sleep', output.getvalue())
        self.assertFalse(database.DB_PATH.exists())


if __name__ == '__main__':
    unittest.main()
