from dataclasses import asdict, replace
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import daily_health as health

NOW = datetime(2026, 1, 2, 14, tzinfo=timezone.utc)
PRIVATE = 'PRIVATE-HEALTH-FIXTURE-CONTENT'


class DailyHealthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / '.local' / 'operations'
        self.factory = Mock(return_value={'checks': [{'status': 'ok', 'detail': PRIVATE},
            {'status': 'warning', 'title': PRIVATE}]})
        self.scheduler = health.DailyHealthScheduler(self.root, self.factory, clock=lambda: NOW)
        self.addCleanup(self.scheduler.stop)

    def enabled(self, **changes):
        self.scheduler.settings = health.OperationsSettings.from_values({'daily_health_enabled': True, **changes})
        self.scheduler.store.write('settings.json', asdict(self.scheduler.settings))

    def test_disabled_construction_status_start_and_due_check_have_no_writes_or_thread(self):
        self.assertFalse(self.root.exists())
        self.assertFalse(self.scheduler.start())
        self.assertEqual(self.scheduler.run_pending()['status'], 'disabled')
        self.assertFalse(self.scheduler.status()['scheduled'])
        self.assertFalse(self.root.exists())
        self.factory.assert_not_called()

    def test_explicit_manual_check_saves_only_private_aggregate_summary(self):
        result = self.scheduler.run_now()
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['result']['status'], 'attention')
        self.assertEqual(result['result']['counts']['ok'], 1)
        self.assertNotIn(PRIVATE, json.dumps(self.scheduler.status()))
        self.assertNotIn(PRIVATE, (self.root / 'health.json').read_text())
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.root / 'health.json').stat().st_mode & 0o777, 0o600)
        self.assertFalse(self.scheduler.status()['scheduled'])

    def test_due_time_calendar_day_and_restart_are_durable(self):
        self.enabled()
        self.assertEqual(self.scheduler.run_pending(NOW - timedelta(seconds=1))['status'], 'not_due')
        self.assertEqual(self.scheduler.run_pending(NOW)['status'], 'complete')
        second = health.DailyHealthScheduler(self.root, self.factory)
        self.addCleanup(second.stop)
        self.assertEqual(second.run_pending(NOW + timedelta(hours=12))['status'], 'already_checked')
        self.assertEqual(second.run_pending(NOW + timedelta(days=1))['status'], 'complete')
        self.assertEqual(self.factory.call_count, 2)

    def test_dst_daily_intervals_gaps_and_repeated_hour(self):
        self.assertEqual(health.daily_due(date(2026, 3, 8)) - health.daily_due(date(2026, 3, 7)), timedelta(hours=23))
        self.assertEqual(health.daily_due(date(2026, 11, 1)) - health.daily_due(date(2026, 10, 31)), timedelta(hours=25))
        self.assertEqual(health.daily_due(date(2026, 3, 8), '02:30').hour, 7)
        self.assertEqual(health.daily_due(date(2026, 11, 1), '01:30').hour, 5)

    def test_failed_or_interrupted_attempt_backs_off_without_saving_error_text(self):
        self.enabled()
        self.factory.side_effect = RuntimeError(PRIVATE)
        self.assertEqual(self.scheduler.run_pending(NOW)['status'], 'failed')
        self.assertEqual(self.scheduler.run_pending(NOW + timedelta(seconds=299))['status'], 'backoff')
        self.assertNotIn(PRIVATE, json.dumps(self.scheduler.status()))
        self.factory.side_effect = None
        self.assertEqual(self.scheduler.run_pending(NOW + timedelta(seconds=300))['status'], 'complete')
        state = self.scheduler._read_state()
        state.update(last_success_date=None, last_outcome='running')
        self.scheduler.store.write('health.json', state)
        restarted = health.DailyHealthScheduler(self.root, self.factory)
        self.assertEqual(restarted.run_pending(NOW + timedelta(seconds=301))['status'], 'backoff')

    def test_same_process_and_two_instances_do_not_overlap(self):
        entered, release, outcomes = threading.Event(), threading.Event(), []
        def snapshot():
            entered.set()
            self.assertTrue(release.wait(2))
            return {'checks': [{'status': 'ok'}]}
        self.scheduler.snapshot_factory = snapshot
        thread = threading.Thread(target=lambda: outcomes.append(self.scheduler.run_now()))
        thread.start()
        self.assertTrue(entered.wait(2))
        self.assertEqual(self.scheduler.run_now()['status'], 'busy')
        other = health.DailyHealthScheduler(self.root, Mock(side_effect=AssertionError('Overlap')))
        self.assertEqual(other.run_now()['status'], 'busy')
        self.assertTrue(self.scheduler.status()['running'])
        release.set()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(outcomes[0]['status'], 'complete')

    def test_resource_deferral_and_unknown_required_signal_are_honest(self):
        self.enabled(local_signals_enabled=True, external_power_required=True)
        self.scheduler.signal_provider = Mock(return_value=None)
        self.assertEqual(self.scheduler.run_pending(NOW), {'status': 'deferred', 'reason': 'power_unknown'})
        self.assertFalse((self.root / 'health.json').exists())
        self.factory.assert_not_called()
        self.scheduler.signal_provider.side_effect = RuntimeError(PRIVATE)
        self.assertEqual(self.scheduler.run_pending(NOW)['reason'], 'power_unknown')
        # Explicit manual health is lightweight and bypasses daily resource gates.
        self.assertEqual(self.scheduler.run_now(NOW)['status'], 'complete')

    def test_corrupt_unsafe_or_linked_state_never_invokes_callback(self):
        self.root.mkdir(parents=True, mode=0o700)
        target = self.root.parent / 'outside.json'
        target.write_text(PRIVATE)
        (self.root / 'health.json').symlink_to(target)
        self.assertEqual(self.scheduler.run_now()['status'], 'unavailable')
        self.assertEqual(target.read_text(), PRIVATE)
        self.factory.assert_not_called()
        (self.root / 'health.json').unlink()
        self.scheduler.store.write('health.json', {'secret': PRIVATE})
        self.assertEqual(self.scheduler.status()['notice'], 'invalid_health_state')
        self.assertEqual(self.scheduler.run_now()['status'], 'unavailable')
        self.assertNotIn(PRIVATE, json.dumps(self.scheduler.status()))

    def test_settings_strict_validation_partial_updates_and_invalid_file_fail_closed(self):
        for values in ({'daily_health_enabled': 1}, {'timezone': 'UTC'}, {'daily_health_time': '9:00'},
                {'minimum_battery_percent': True}, {'minimum_battery_percent': 101}, {'unknown': True},
                {'external_power_required': True}, {'quiet_start': '08:00'}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                health.OperationsSettings.from_values(values)
        with patch.object(self.scheduler, 'start'):
            self.scheduler.configure({'daily_health_enabled': True})
            self.scheduler.configure({'quiet_hours_enabled': True})
        self.assertTrue(self.scheduler.settings.daily_health_enabled)
        self.assertTrue(self.scheduler.settings.quiet_hours_enabled)
        before = (self.root / 'settings.json').read_bytes()
        with self.assertRaises(ValueError):
            self.scheduler.configure({'unknown': PRIVATE})
        self.assertEqual((self.root / 'settings.json').read_bytes(), before)
        self.scheduler.store.write('settings.json', {'unknown': PRIVATE})
        invalid = health.DailyHealthScheduler(self.root, self.factory)
        self.assertFalse(invalid.start())
        self.assertEqual(invalid.status()['notice'], 'invalid_operations_settings')

    def test_enabled_server_thread_checks_once_then_stops(self):
        self.enabled()
        checked = threading.Event()
        def snapshot():
            checked.set()
            return {'checks': [{'status': 'ok'}]}
        self.scheduler.snapshot_factory = snapshot
        with patch.object(health, 'POLL_SECONDS', 0.02):
            self.assertTrue(self.scheduler.start())
            self.assertTrue(checked.wait(2))
            self.assertFalse(self.scheduler.start())
            self.assertTrue(self.scheduler.stop())
        self.assertFalse(self.scheduler.status()['scheduled'])

    def test_nonregular_state_does_not_block_and_clock_failure_does_not_restart_loop(self):
        self.root.mkdir(parents=True, mode=0o700)
        os.mkfifo(self.root / 'health.json', 0o600)
        self.assertEqual(self.scheduler.run_now()['status'], 'unavailable')
        self.factory.assert_not_called()
        (self.root / 'health.json').unlink()
        self.enabled()
        attempted = threading.Event()
        def failed_clock():
            attempted.set()
            raise RuntimeError(PRIVATE)
        self.scheduler.clock = failed_clock
        with patch.object(health, 'POLL_SECONDS', 0.02):
            self.assertTrue(self.scheduler.start())
            self.assertTrue(attempted.wait(2))
            self.assertTrue(self.scheduler.stop())
        self.assertNotIn(PRIVATE, json.dumps(self.scheduler.status()))
        self.assertEqual(self.scheduler.notice, 'health_scheduler_unavailable')
