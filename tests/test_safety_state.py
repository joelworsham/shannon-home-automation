"""State machine prevents retries and separates command intent from evidence."""
import unittest
from datetime import datetime, timedelta, timezone

from climate_policy import FanState
from safety_state import PendingCommand, PowerObservation, reconcile

T = datetime(2026, 1, 15, 23, 0, tzinfo=timezone.utc)
DESIRED = FanState('intake', 4)


class SafetyStateTests(unittest.TestCase):
    def test_known_off_ready_for_single_power_toggle(self):
        result = reconcile(desired=DESIRED, observed=PowerObservation(False, T, watts=1), now=T)
        self.assertEqual(result.status, 'ready')
        self.assertEqual(result.plan.command, 'power_toggle')
        self.assertTrue(result.plan.verify_after)

    def test_no_shelly_evidence_blocks(self):
        result = reconcile(desired=DESIRED, observed=PowerObservation(None), now=T)
        self.assertTrue(result.plan.blocked)
        self.assertIsNone(result.plan.command)

    def test_stale_measurement_even_if_desired_off_blocks_toggle(self):
        old = PowerObservation(True, T - timedelta(minutes=11), watts=11)
        result = reconcile(desired=FanState(), observed=old, now=T)
        self.assertEqual(result.diagnostic_code, 'stale_power_observation')
        self.assertIsNone(result.plan.command)

    def test_timestamp_in_future_blocks(self):
        result = reconcile(desired=FanState(), observed=PowerObservation(True, T + timedelta(seconds=1)), now=T)
        self.assertEqual(result.diagnostic_code, 'invalid_observation_time')

    def test_pending_never_retries_before_confirmation(self):
        pending = PendingCommand('power_toggle', T, expected_running=True)
        result = reconcile(desired=DESIRED, observed=PowerObservation(False, T + timedelta(seconds=10)),
                           pending=pending, now=T + timedelta(seconds=10))
        self.assertEqual(result.status, 'awaiting_confirmation')
        self.assertIsNone(result.plan.command)

    def test_missing_ir_fails_closed_after_timeout(self):
        pending = PendingCommand('power_toggle', T, expected_running=True)
        result = reconcile(desired=DESIRED, observed=PowerObservation(False, T + timedelta(seconds=100)),
                           pending=pending, now=T + timedelta(seconds=100))
        self.assertEqual(result.diagnostic_code, 'power_toggle_unconfirmed')
        self.assertTrue(result.plan.blocked)
        self.assertIsNone(result.plan.command)

    def test_only_fresh_meter_evidence_confirms_power(self):
        pending = PendingCommand('power_toggle', T, expected_running=True)
        stale = reconcile(desired=DESIRED, observed=PowerObservation(True, T),
                          pending=pending, now=T + timedelta(seconds=30))
        self.assertFalse(stale.power_confirmed)
        fresh = reconcile(desired=DESIRED, observed=PowerObservation(True, T + timedelta(seconds=30)),
                          pending=pending, now=T + timedelta(seconds=30))
        self.assertTrue(fresh.power_confirmed)
        self.assertIsNone(fresh.plan.command)

    def test_observed_running_cannot_prove_direction(self):
        result = reconcile(desired=DESIRED, observed=PowerObservation(True, T, watts=11), now=T)
        self.assertEqual(result.status, 'blocked')
        self.assertEqual(result.diagnostic_code, 'unknown_airflow')

    def test_speed_command_requires_manual_confirmation(self):
        pending = PendingCommand('set_high', T)
        result = reconcile(desired=DESIRED, observed=PowerObservation(True, T + timedelta(minutes=2),
                              direction='intake', speed=4), pending=pending,
                              now=T + timedelta(minutes=2))
        self.assertEqual(result.diagnostic_code, 'manual_verification_required')
        self.assertIsNone(result.plan.command)

    def test_potential_speed_change_with_explicit_verification(self):
        result = reconcile(desired=DESIRED,
                           observed=PowerObservation(True, T, direction='intake', speed=1), now=T)
        self.assertEqual(result.plan.command, 'set_high')

    def test_reject_naive_timestamps(self):
        with self.assertRaises(ValueError):
            PowerObservation(True, T.replace(tzinfo=None))
        with self.assertRaises(ValueError):
            PendingCommand('power_toggle', T.replace(tzinfo=None), True)


if __name__ == '__main__':
    unittest.main()
