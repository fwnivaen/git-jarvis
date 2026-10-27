"""Real private SQLite, native gateway journals and temporary Markdown notes."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import native_tool_routes
import note_actions
from note_actions import NoteActions, NoteActionError, APPROVAL_TTL_S, UNDO_TTL_S
import note_store

TIMEOUT_S = 5
REQUEST_KEY = 'a' * 24
ORIGINAL = '# Plan\n\nThe original evidence.'
PROPOSED = '# Plan\n\nThe reviewed replacement.'


class NoteActionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.vault = self.root / 'vault'
        self.vault.mkdir()
        self.path = self.vault / 'Plan.md'
        self.other_path = self.vault / 'Other.md'
        self.path.write_text(ORIGINAL)
        self.other_path.write_text(ORIGINAL)
        self.notes = {0: {'id': 0, 'group': 'test', '_path': 'Plan.md'},
            1: {'id': 1, 'group': 'test', '_path': 'Other.md'}}
        self.now = [1000.0]
        self.writes = []
        self.lock = threading.RLock()
        self.queue_path = self.root / 'private' / 'actions.sqlite'
        self.actions = self.open()

    def read(self, note_id):
        with self.lock:
            return note_store.read(self.notes[note_id], self.vault)

    def save(self, id, text, revision, _deadline=None):
        with self.lock:
            self.writes.append({'id': id, 'text': text, 'revision': revision})
            result = note_store.save(self.notes[id], self.vault, text, revision)
            return {'verified': True, 'note': result}

    def write(self, arguments, operation_session):
        return native_tool_routes.invoke('note_write', arguments, self.save,
            properties={'id': {'type': 'integer'}, 'text': {'type': 'string'},
                'revision': {'type': 'string'}},
            body=dict(arguments, approved=True, operation_session=operation_session),
            duration_s=TIMEOUT_S, root=self.root / 'journals')

    def open(self, path=None):
        actions = NoteActions(path or self.queue_path, self.read, self.write, clock=lambda: self.now[0])
        self.addCleanup(actions.close)
        return actions

    def propose(self, text=PROPOSED, **changes):
        args = dict(note_id=0, text=text, revision=self.read(0)['revision'], request_key=REQUEST_KEY)
        args.update(changes)
        return self.actions.propose(**args)

    def decide(self, action, decision='approve', **changes):
        args = dict(action_id=action['id'], fingerprint=action['fingerprint'], decision=decision)
        args.update(changes)
        return self.actions.decide(**args)

    def test_propose_is_read_only_then_exact_approval_and_undo_use_existing_gateway(self):
        action = self.propose()
        self.assertEqual(action['status'], 'pending')
        self.assertEqual(action['before'], {'text': ORIGINAL, 'revision': self.read(0)['revision']})
        self.assertEqual(action['after'], {'text': PROPOSED})
        self.assertEqual(self.writes, [])
        self.assertEqual(self.path.read_text(), ORIGINAL)
        self.assertFalse((self.root / 'journals').exists())
        summary = self.actions.catalog()
        self.assertEqual(summary['pending_count'], 1)
        self.assertNotIn('before', summary['items'][0])
        result = self.decide(action)
        self.assertTrue(result['result']['verified'])
        self.assertEqual(result['approval']['status'], 'applied')
        self.assertTrue(result['approval']['can_undo'])
        self.assertEqual(self.path.read_text(), PROPOSED)
        self.assertEqual(self.other_path.read_text(), ORIGINAL)
        self.assertEqual(len(self.writes), 1)
        self.assertEqual(self.actions.catalog()['pending_count'], 0)
        undone = self.actions.undo(action['id'], action['fingerprint'])
        self.assertEqual(undone['approval']['status'], 'undone')
        self.assertFalse(undone['approval']['can_undo'])
        self.assertEqual(self.path.read_text(), ORIGINAL)
        self.assertEqual(len(self.writes), 2)
        with self.assertRaises(NoteActionError):
            self.actions.undo(action['id'], action['fingerprint'])
        self.assertEqual(len(self.writes), 2)

    def test_pending_proposal_survives_restart_without_replay(self):
        action = self.propose()
        self.actions.close()
        self.actions = self.open()
        self.assertEqual(self.actions.get(action['id']), action)
        self.assertEqual(self.writes, [])
        self.decide(action)
        self.actions.close()
        self.actions = self.open()
        self.assertEqual(self.actions.get(action['id'])['status'], 'applied')
        self.assertEqual(len(self.writes), 1)
        self.actions.undo(action['id'], action['fingerprint'])
        self.assertEqual(self.path.read_text(), ORIGINAL)

    def test_reject_is_terminal_and_has_no_write(self):
        action = self.propose()
        result = self.decide(action, 'reject')
        self.assertEqual(result['approval']['status'], 'rejected')
        self.assertIsNone(result['result'])
        for decision in ('approve', 'reject'):
            with self.assertRaises(NoteActionError):
                self.decide(action, decision)
        self.assertEqual(self.writes, [])

    def test_mismatched_operation_and_changed_request_key_are_rejected(self):
        first = self.propose()
        self.assertEqual(self.propose(), first)
        second = self.propose('Different reviewed text.', request_key='b' * 24)
        with self.assertRaises(NoteActionError):
            self.decide(first, fingerprint=second['fingerprint'])
        with self.assertRaises(NoteActionError):
            self.propose('Changed after review.')
        self.assertEqual(self.actions.get(first['id'])['status'], 'pending')
        self.assertEqual(self.writes, [])
        self.decide(first)
        # Admission replay returns the durable record and never reopens approval.
        duplicate = self.actions.propose(0, PROPOSED, first['before']['revision'], REQUEST_KEY)
        self.assertEqual(duplicate['status'], 'applied')
        with self.assertRaises(NoteActionError):
            self.decide(first)
        self.assertEqual(len(self.writes), 1)

    def test_two_connections_can_decide_only_once_under_race(self):
        action = self.propose()
        other = self.open()
        def decide(index):
            service = self.actions if index % 2 else other
            try:
                return service.decide(action['id'], action['fingerprint'], 'approve')['approval']['status']
            except NoteActionError:
                return 'denied'
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(decide, range(20)))
        self.assertEqual(results.count('applied'), 1)
        self.assertEqual(results.count('denied'), 19)
        self.assertEqual(len(self.writes), 1)

    def test_expiration_cannot_be_extended_by_proposal_or_decision_replay(self):
        action = self.propose()
        self.now[0] = action['expires_at']
        with self.assertRaises(NoteActionError):
            self.decide(action)
        expired = self.actions.propose(0, PROPOSED, action['before']['revision'], REQUEST_KEY)
        self.assertEqual(expired['expires_at'], action['expires_at'])
        self.assertEqual(expired['status'], 'expired')
        self.assertEqual(self.actions.catalog()['pending_count'], 0)
        self.assertEqual(self.writes, [])

    def test_revision_drift_and_reassigned_identity_are_rejected(self):
        action = self.propose()
        self.path.write_text('Independent later edit.')
        with self.assertRaises(NoteActionError):
            self.decide(action)
        self.assertEqual(self.path.read_text(), 'Independent later edit.')
        self.assertEqual(self.actions.get(action['id'])['status'], 'stale')
        self.path.write_text(ORIGINAL)
        another = self.propose(request_key='b' * 24)
        self.notes[0] = dict(self.notes[1], id=0)
        with self.assertRaises(NoteActionError):
            self.decide(another)
        self.assertEqual(self.other_path.read_text(), ORIGINAL)
        self.assertEqual(self.writes, [])

    def test_scopes_and_stores_do_not_share_approval_authority(self):
        action = self.propose(scope='project-a')
        self.assertEqual(self.actions.catalog(scope='project-b')['items'], [])
        for call in (
            lambda: self.actions.get(action['id'], scope='project-b'),
            lambda: self.decide(action, scope='project-b'),
            lambda: self.actions.undo(action['id'], action['fingerprint'], scope='project-b'),
            lambda: self.open(self.root/'other'/'actions.sqlite').get(action['id'], scope='project-a'),
        ):
            with self.assertRaises(NoteActionError) as error:
                call()
            self.assertEqual(error.exception.status, 404)
        other = self.propose(scope='project-b')
        self.assertNotEqual(action['fingerprint'], other['fingerprint'])
        with self.assertRaises(NoteActionError):
            self.decide(other, fingerprint=action['fingerprint'], scope='project-b')
        self.assertEqual(self.writes, [])

    def test_undo_refuses_external_edits_and_expiration(self):
        action = self.propose()
        self.decide(action)
        self.path.write_text('A later independent edit.')
        with self.assertRaises(NoteActionError):
            self.actions.undo(action['id'], action['fingerprint'])
        self.assertEqual(self.path.read_text(), 'A later independent edit.')
        self.assertEqual(self.actions.get(action['id'])['status'], 'undo_stale')
        self.assertEqual(len(self.writes), 1)
        later = self.propose('Another approved edit.', request_key='b' * 24)
        self.decide(later)
        self.now[0] += UNDO_TTL_S
        self.assertFalse(self.actions.get(later['id'])['can_undo'])
        with self.assertRaises(NoteActionError):
            self.actions.undo(later['id'], later['fingerprint'])
        self.assertEqual(len(self.writes), 2)

    def test_failed_callback_after_effect_is_uncertain_and_never_replayed(self):
        action = self.propose()
        save = self.save
        def interrupted(**arguments):
            save(**arguments)
            raise OSError('A private path or credential must never reach the error.')
        self.save = interrupted
        with self.assertRaises(NoteActionError) as error:
            self.decide(action)
        self.assertEqual(error.exception.status, 503)
        self.assertNotIn('private path', str(error.exception))
        self.assertEqual(self.path.read_text(), PROPOSED)
        self.assertEqual(self.actions.get(action['id'])['status'], 'uncertain')
        self.actions.close()
        self.actions = self.open()
        with self.assertRaises(NoteActionError):
            self.decide(action)
        with self.assertRaises(NoteActionError):
            self.actions.undo(action['id'], action['fingerprint'])
        self.assertEqual(len(self.writes), 1)

    def test_restart_fences_claimed_apply_and_undo_without_running_callbacks(self):
        action = self.propose()
        self.actions._claim(action['id'], action['fingerprint'], 'local')
        self.actions.close()
        self.actions = self.open()
        self.assertEqual(self.actions.get(action['id'])['status'], 'uncertain')
        with self.assertRaises(NoteActionError):
            self.decide(action)
        self.assertEqual(self.writes, [])
        other = self.propose('Undo recovery example.', request_key='b' * 24)
        self.decide(other)
        self.actions._claim(other['id'], other['fingerprint'], 'local', undo=True)
        self.actions.close()
        self.actions = self.open()
        self.assertEqual(self.actions.get(other['id'])['status'], 'undo_uncertain')
        with self.assertRaises(NoteActionError):
            self.actions.undo(other['id'], other['fingerprint'])
        self.assertEqual(len(self.writes), 1)

    def test_queue_count_storage_and_pending_limits_fail_closed(self):
        with patch.object(note_actions, 'MAX_PENDING', 1):
            first = self.propose()
            with self.assertRaises(NoteActionError) as error:
                self.propose('A second edit.', request_key='b' * 24)
            self.assertEqual(error.exception.status, 429)
            self.decide(first, 'reject')
            self.propose('A second edit.', request_key='b' * 24)
        with patch.object(note_actions, 'MAX_ACTIONS', 2):
            with self.assertRaises(NoteActionError) as error:
                self.propose('A third edit.', request_key='c' * 24)
            self.assertEqual(error.exception.status, 503)
        with patch.object(note_actions, 'MAX_QUEUE_BYTES', self.queue_path.stat().st_size):
            with self.assertRaises(NoteActionError):
                self.propose('A third edit.', request_key='c' * 24)
        self.assertEqual(len(self.actions.catalog()['items']), 2)
        self.assertEqual(self.writes, [])

    def test_invalid_input_and_private_storage_do_not_expose_internal_authority(self):
        for changes in ({'note_id': True}, {'note_id': -1}, {'revision': 'not-a-revision'},
                {'text': []}, {'text': '\ud800'}, {'text': 'x'*(note_store.MAX_NOTE_CHARS+1)},
                {'request_key': '../path'}, {'scope': '../scope'}):
            with self.subTest(changes=list(changes)), self.assertRaises(NoteActionError):
                self.propose(**changes)
        action = self.propose()
        self.assertEqual(self.queue_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.queue_path.parent.stat().st_mode & 0o777, 0o700)
        serialized = str(action) + str(self.actions.catalog())
        for private in ('operation_session', 'undo_session', str(self.root), 'request_fingerprint'):
            self.assertNotIn(private, serialized)
        with self.assertRaises(NoteActionError):
            self.decide(action, decision=True)
        self.assertEqual(self.writes, [])


if __name__ == '__main__':
    unittest.main()
