"""Real private preference JSON and SQLite approvals, with no model or note reads."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import note_actions
import preference_actions
from preference_actions import PreferenceActions, PreferenceActionError, PreferenceState
from ultra_memory import Preferences

TIMEOUT_S = 5
BLOCK_CHECK_S = 0.03
REQUEST_KEY = 'a' * 24
TOKEN = 'sk-example-do-not-store-123456789'


class PreferenceActionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.preferences = Preferences(self.root/'memory'/'preferences.json')
        self.preferences.set('tone', 'Concise')
        self.now = [1000.0]
        self.queue = self.root/'actions'/'control.sqlite3'
        self.actions = self.open()

    def open(self, path=None, preferences=None):
        service = PreferenceActions(path or self.queue, preferences or self.preferences,
            clock=lambda: self.now[0])
        self.addCleanup(service.close)
        return service

    def propose(self, operation='set', key='tone', value='Detailed', **changes):
        arguments = dict(operation=operation, key=key, value=value,
            revision=self.actions.snapshot()['revision'], request_key=REQUEST_KEY)
        arguments.update(changes)
        return self.actions.propose(**arguments)

    def decide(self, action, **changes):
        arguments = dict(action_id=action['id'], fingerprint=action['fingerprint'], decision='approve')
        arguments.update(changes)
        return self.actions.decide(**arguments)

    def test_review_then_set_and_exact_state_undo_without_note_or_prompt_reads(self):
        before = self.preferences.path.read_bytes()
        with patch.object(Preferences, 'read', side_effect=AssertionError('Permissive read must not be used')):
            action = self.propose()
        self.assertEqual(self.preferences.path.read_bytes(), before)
        self.assertEqual(action['before']['preferences'], {'tone': 'Concise'})
        self.assertEqual(action['after']['preferences'], {'tone': 'Detailed'})
        self.assertEqual(action['tool'], 'preferences_set')
        summary = self.actions.catalog()
        self.assertEqual(summary['pending_count'], 1)
        self.assertNotIn('before', summary['items'][0])
        self.assertNotIn('after', summary['items'][0])
        self.assertNotIn('Detailed', str(summary))
        result = self.decide(action)
        self.assertEqual(result['approval']['status'], 'applied')
        self.assertTrue(result['result']['verified'])
        self.assertEqual(result['result']['preferences'], {'tone': 'Detailed'})
        self.assertEqual(self.preferences.read(), {'tone': 'Detailed'})
        self.assertEqual(self.actions.catalog()['pending_count'], 0)
        undone = self.actions.undo(action['id'], action['fingerprint'])
        self.assertEqual(undone['approval']['status'], 'undone')
        self.assertEqual(self.preferences.read(), {'tone': 'Concise'})
        self.assertFalse(undone['approval']['can_undo'])
        with self.assertRaises(PreferenceActionError):
            self.actions.undo(action['id'], action['fingerprint'])
        self.assertFalse((self.root/'notes').exists())

    def test_forget_reviews_the_whole_map_and_undo_restores_only_exact_state(self):
        self.preferences.set('address', 'Sir')
        action = self.propose('forget', 'tone', None)
        self.assertEqual(action['tool'], 'preferences_forget')
        self.assertEqual(action['before']['preferences'], {'tone': 'Concise', 'address': 'Sir'})
        self.assertEqual(action['after']['preferences'], {'address': 'Sir'})
        self.decide(action)
        self.assertEqual(self.preferences.read(), {'address': 'Sir'})
        self.actions.undo(action['id'], action['fingerprint'])
        self.assertEqual(self.preferences.read(), {'tone': 'Concise', 'address': 'Sir'})

    def test_unrelated_preference_change_invalidates_approval_and_undo(self):
        action = self.propose()
        self.preferences.set('address', 'Sir')
        with self.assertRaises(PreferenceActionError):
            self.decide(action)
        self.assertEqual(self.actions.get(action['id'])['status'], 'stale')
        self.assertEqual(self.preferences.read(), {'tone': 'Concise', 'address': 'Sir'})
        other = self.propose(request_key='b'*24)
        self.decide(other)
        self.preferences.set('address', 'Nivaen')
        with self.assertRaises(PreferenceActionError):
            self.actions.undo(other['id'], other['fingerprint'])
        self.assertEqual(self.actions.get(other['id'])['status'], 'undo_stale')
        self.assertEqual(self.preferences.read(), {'tone': 'Detailed', 'address': 'Nivaen'})

    def test_request_replay_survives_restart_without_a_second_effect(self):
        action = self.propose()
        self.actions.close()
        self.actions = self.open()
        self.assertEqual(self.actions.get(action['id']), action)
        self.decide(action)
        self.actions.close()
        self.actions = self.open()
        replay = self.propose(revision=action['before']['revision'])
        self.assertEqual(replay['id'], action['id'])
        self.assertEqual(replay['status'], 'applied')
        with self.assertRaises(PreferenceActionError):
            self.propose(value='Changed replay', revision=action['before']['revision'])
        with self.assertRaises(PreferenceActionError):
            self.decide(action)
        self.assertEqual(self.preferences.read(), {'tone': 'Detailed'})

    def test_mismatched_fingerprint_scope_and_other_store_are_denied(self):
        first = self.propose(scope='vault-a')
        other = self.propose(value='Short', request_key='b'*24, scope='vault-a')
        with self.assertRaises(PreferenceActionError):
            self.decide(first, fingerprint=other['fingerprint'], scope='vault-a')
        self.assertEqual(self.actions.catalog('vault-b')['pending_count'], 0)
        for call in (lambda: self.actions.get(first['id'], 'vault-b'),
                lambda: self.decide(first, scope='vault-b'),
                lambda: self.open(self.root/'other'/'control.sqlite').get(first['id'], 'vault-a')):
            with self.assertRaises(PreferenceActionError) as error:
                call()
            self.assertEqual(error.exception.status, 404)
        self.assertEqual(self.preferences.read(), {'tone': 'Concise'})

    def test_private_map_identity_cannot_move_to_another_preference_store(self):
        action = self.propose()
        self.actions.close()
        different = Preferences(self.root/'different'/'preferences.json')
        different.set('tone', 'Concise')
        self.actions = self.open(preferences=different)
        with self.assertRaises(PreferenceActionError):
            self.decide(action)
        self.assertEqual(different.read(), {'tone': 'Concise'})
        self.assertEqual(self.actions.get(action['id'])['status'], 'stale')

    def test_reject_expiry_and_undo_expiry_are_terminal(self):
        action = self.propose()
        rejected = self.decide(action, decision='reject')
        self.assertIsNone(rejected['result'])
        self.assertEqual(rejected['approval']['status'], 'rejected')
        with self.assertRaises(PreferenceActionError):
            self.decide(action)
        expiring = self.propose(request_key='b'*24)
        self.now[0] = expiring['expires_at']
        with self.assertRaises(PreferenceActionError):
            self.decide(expiring)
        self.assertEqual(self.actions.get(expiring['id'])['status'], 'expired')
        applied = self.propose(request_key='c'*24)
        self.decide(applied)
        self.now[0] += note_actions.UNDO_TTL_S
        self.assertFalse(self.actions.get(applied['id'])['can_undo'])
        with self.assertRaises(PreferenceActionError):
            self.actions.undo(applied['id'], applied['fingerprint'])
        self.assertEqual(self.preferences.read(), {'tone': 'Detailed'})

    def test_two_connections_approve_once_under_race(self):
        action = self.propose()
        other = self.open()
        original = self.preferences.path.read_bytes()
        count = []
        atomic = preference_actions.atomic_json
        def writing(*arguments):
            count.append(True)
            atomic(*arguments)
        def decide(index):
            service = self.actions if index % 2 else other
            try:
                return service.decide(action['id'], action['fingerprint'], 'approve')['approval']['status']
            except PreferenceActionError:
                return 'denied'
        with patch.object(preference_actions, 'atomic_json', side_effect=writing):
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(decide, range(20)))
        self.assertEqual(results.count('applied'), 1)
        self.assertEqual(results.count('denied'), 19)
        self.assertEqual(len(count), 1)
        self.assertNotEqual(self.preferences.path.read_bytes(), original)

    def test_approval_claim_and_undo_claim_are_fenced_on_restart(self):
        action = self.propose()
        self.actions._engine._claim(action['id'], action['fingerprint'], 'local')
        self.actions.close()
        self.actions = self.open()
        self.assertEqual(self.actions.get(action['id'])['status'], 'uncertain')
        with self.assertRaises(PreferenceActionError):
            self.decide(action)
        self.assertEqual(self.preferences.read(), {'tone': 'Concise'})
        other = self.propose(request_key='b'*24)
        self.decide(other)
        self.actions._engine._claim(other['id'], other['fingerprint'], 'local', undo=True)
        self.actions.close()
        self.actions = self.open()
        self.assertEqual(self.actions.get(other['id'])['status'], 'undo_uncertain')
        with self.assertRaises(PreferenceActionError):
            self.actions.undo(other['id'], other['fingerprint'])
        self.assertEqual(self.preferences.read(), {'tone': 'Detailed'})

    def test_callback_lost_after_effect_is_uncertain_and_not_replayed(self):
        action = self.propose()
        callback = self.actions._engine.write_note
        def lost(arguments, operation_session):
            callback(arguments, operation_session)
            raise OSError('PRIVATE_STORAGE_PATH ' + TOKEN)
        self.actions._engine.write_note = lost
        with self.assertRaises(PreferenceActionError) as error:
            self.decide(action)
        self.assertEqual(error.exception.status, 503)
        self.assertNotIn(TOKEN, str(error.exception))
        self.assertNotIn('PRIVATE_STORAGE_PATH', str(error.exception))
        self.assertEqual(self.preferences.read(), {'tone': 'Detailed'})
        self.assertEqual(self.actions.get(action['id'])['status'], 'uncertain')
        self.actions.close()
        self.actions = self.open()
        with self.assertRaises(PreferenceActionError):
            self.decide(action)

    def test_invalid_and_credential_inputs_cannot_enter_queue(self):
        for changes in ({'key': 'api_key', 'value': 'private'}, {'value': TOKEN},
                {'value': 'password=private'}, {'key': ''}, {'value': ''}, {'value': None},
                {'value': '\ud800'}, {'value': 'x'*2001}, {'revision': 'wrong'},
                {'operation': 'infer'}, {'operation': 'forget', 'value': 'extra'},
                {'operation': 'forget', 'key': 'missing', 'value': None}, {'request_key': '../path'}):
            with self.subTest(changes=list(changes)), self.assertRaises(PreferenceActionError):
                self.propose(**changes)
        self.assertEqual(self.actions.catalog()['items'], [])
        self.assertEqual(self.preferences.read(), {'tone': 'Concise'})
        self.assertNotIn(TOKEN.encode(), self.queue.read_bytes())

    def test_corrupt_credential_symlink_and_fifo_state_fail_without_overwrite(self):
        for content in ('corrupt', json.dumps({'tone': TOKEN})):
            self.preferences.path.write_text(content)
            before = self.preferences.path.read_bytes()
            with self.assertRaises(PreferenceActionError):
                self.propose()
            self.assertEqual(self.preferences.path.read_bytes(), before)
        self.preferences.path.unlink()
        outside = self.root/'outside.json'
        outside.write_text('{"tone":"Outside"}')
        self.preferences.path.symlink_to(outside)
        with self.assertRaises(PreferenceActionError):
            self.actions.snapshot()
        self.assertEqual(outside.read_text(), '{"tone":"Outside"}')
        self.preferences.path.unlink()
        os.mkfifo(self.preferences.path)
        with self.assertRaises(PreferenceActionError):
            self.actions.snapshot()
        self.assertTrue(self.preferences.path.is_fifo())

    def test_whole_map_capacity_and_pending_limits_are_bounded(self):
        with patch.object(preference_actions, 'MAX_PREFERENCES', 1):
            with self.assertRaises(PreferenceActionError):
                self.propose(key='another')
        with patch.object(note_actions, 'MAX_PENDING', 1):
            self.propose()
            with self.assertRaises(PreferenceActionError) as error:
                self.propose(key='another', request_key='b'*24)
            self.assertEqual(error.exception.status, 429)
        self.assertEqual(self.actions.catalog()['pending_count'], 1)

    def test_compare_replace_holds_existing_preference_lock(self):
        state = PreferenceState(self.preferences)
        revision = state.snapshot()['revision']
        entered, release, other_done = threading.Event(), threading.Event(), threading.Event()
        atomic = preference_actions.atomic_json
        def blocked(*arguments):
            entered.set()
            if not release.wait(TIMEOUT_S):
                raise TimeoutError
            atomic(*arguments)
        def external_set():
            self.preferences.set('address', 'Sir')
            other_done.set()
        with ThreadPoolExecutor(max_workers=2) as pool:
            with patch.object(preference_actions, 'atomic_json', side_effect=blocked):
                replacement = pool.submit(state.compare_and_replace, {'tone': 'Detailed'}, revision)
                self.assertTrue(entered.wait(TIMEOUT_S))
                other = pool.submit(external_set)
                try:
                    self.assertFalse(other_done.wait(BLOCK_CHECK_S))
                finally:
                    release.set()
                self.assertTrue(replacement.result(TIMEOUT_S)['verified'])
                other.result(TIMEOUT_S)
        self.assertEqual(self.preferences.read(), {'tone': 'Detailed', 'address': 'Sir'})
        with self.assertRaises(PreferenceActionError):
            state.compare_and_replace({'tone': 'Old'}, revision)

    def test_close_waits_for_claimed_write_and_denies_new_work(self):
        action = self.propose()
        entered, release, closed = threading.Event(), threading.Event(), threading.Event()
        callback = self.actions._engine.write_note
        def blocked(*arguments):
            entered.set()
            if not release.wait(TIMEOUT_S):
                raise TimeoutError
            return callback(*arguments)
        def close():
            self.actions.close()
            closed.set()
        self.actions._engine.write_note = blocked
        with ThreadPoolExecutor(max_workers=2) as pool:
            applying = pool.submit(self.decide, action)
            self.assertTrue(entered.wait(TIMEOUT_S))
            closing = pool.submit(close)
            try:
                self.assertFalse(closed.wait(BLOCK_CHECK_S))
            finally:
                release.set()
            self.assertEqual(applying.result(TIMEOUT_S)['approval']['status'], 'applied')
            closing.result(TIMEOUT_S)
        with self.assertRaises(PreferenceActionError) as error:
            self.actions.catalog()
        self.assertEqual(error.exception.status, 503)
        self.actions = self.open()
        self.assertEqual(self.actions.get(action['id'])['status'], 'applied')

    def test_queue_is_private_and_public_results_do_not_expose_internal_authority(self):
        action = self.propose()
        self.assertEqual(self.queue.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.queue.parent.stat().st_mode & 0o777, 0o700)
        serialized = str(action) + str(self.actions.catalog())
        for hidden in ('note_id', 'operation_session', 'undo_session', str(self.root), 'before_text', 'request_fingerprint'):
            self.assertNotIn(hidden, serialized)


if __name__ == '__main__':
    unittest.main()
