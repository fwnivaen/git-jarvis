"""Native write compatibility and durable replay checks in a synthetic vault."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import build
import native_tool_routes
import note_store
import server
import ultra
import ultra_memory
from test_offline import request

ORIGIN = {'Origin': 'http://127.0.0.1:4700'}


class NativeGatewayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.vault = self.root / 'notes'; self.vault.mkdir()
        self.path = self.vault / 'Synthetic.md'; self.path.write_text('# Synthetic\n\nEvidence.')
        for target, name, value in (
            (build, 'NOTES_DIR', self.vault), (server, 'NOTES', build.read_notes(self.vault)),
            (native_tool_routes, 'JOURNAL_DIR', self.root / 'journals'),
            (server, 'PREFERENCES', ultra_memory.Preferences(self.root / 'preferences.json')),
            (server, 'PREFERENCE_ACTIONS', None),
            (server, 'PREFERENCE_ACTION_PATH', self.root / 'preference-actions' / 'control.sqlite3'),
        ):
            patched = patch.object(target, name, value); patched.start(); self.addCleanup(patched.stop)
        patched = patch.object(build, 'write_graph'); patched.start(); self.addCleanup(patched.stop)
        self.addCleanup(lambda: server.PREFERENCE_ACTIONS.close() if server.PREFERENCE_ACTIONS else None)

    def post(self, path, body):
        status, _, raw = request(path, 'POST', body, ORIGIN)
        return status, json.loads(raw)

    def test_capture_retry_writes_once_and_keeps_legacy_tools_disabled_behavior(self):
        body = {'question': 'remember that synthetic purple stars', 'operation_session': 'a' * 24}
        with patch.object(server, 'ultra_settings', return_value=ultra.Settings()):
            first = self.post('/remember', body); second = self.post('/remember', body)
        self.assertEqual(first, second); self.assertEqual(first[0], 200)
        self.assertEqual(len(list((self.vault / 'captures').glob('*.md'))), 1)
        self.assertIsNotNone(first[1]['parent']); self.assertNotIn('_source', first[1])
        self.assertEqual(self.post('/remember', dict(body, question='remember that changed'))[0], 400)

    def test_approved_edit_replay_resolves_reassigned_id_and_rejects_later_content_drift(self):
        original = note_store.read(server.NOTES[0], self.vault)
        body = {'id': 0, 'text': 'Revised synthetic evidence.', 'revision': original['revision'],
            'approved': True, 'operation_session': 'b' * 24}
        self.assertEqual(self.post('/note', body)[0], 200)
        (self.vault / 'Earlier.md').write_text('Other evidence.')
        server.NOTES[:] = build.read_notes(self.vault)
        status, result = self.post('/note', body)
        self.assertEqual(status, 200); self.assertEqual(result['note']['id'], 1)
        self.assertEqual(result['note']['text'], body['text'])
        self.path.write_text('Independent later edit.')
        self.assertEqual(self.post('/note', body)[0], 409)
        self.assertEqual(self.path.read_text(), 'Independent later edit.')
        self.assertEqual(self.post('/note', dict(body, approved=False))[0], 403)

    def test_preferences_and_uploaded_import_share_gateway_and_reject_changed_keys(self):
        settings = replace(ultra.Settings(), enabled=True, tools_enabled=False)
        body = {'key': 'tone', 'value': 'Concise', 'operation_session': 'c' * 24}
        with patch.object(server, 'ultra_settings', return_value=settings), \
                patch.object(server, 'propose_preference', wraps=server.propose_preference) as proposed:
            first = self.post('/memory/preferences', body)
            self.assertEqual(first[0], 200)
            self.assertEqual(self.post('/memory/preferences', body), first)
            self.assertEqual(proposed.call_count, 1)
            self.assertTrue(first[1]['approval_required'])
            self.assertFalse(first[1]['saved'])
            self.assertEqual(server.PREFERENCES.read(), {})
            self.assertEqual(self.post('/memory/preferences', dict(body, value='Changed'))[0], 400)
        upload = {'filename': 'synthetic.pdf', 'data': 'c3ludGhldGlj', 'operation_session': 'd' * 24}
        with patch.object(server, 'ultra_settings', return_value=settings), \
                patch.object(server, 'native_document_import', return_value={'imported': True}) as imported:
            self.assertEqual(self.post('/memory/import', upload)[0], 200)
            self.assertEqual(self.post('/memory/import', upload)[0], 200)
            self.assertEqual(imported.call_count, 1)
        with patch.object(server, 'ultra_settings', return_value=ultra.Settings()):
            self.assertEqual(self.post('/memory/preferences', body)[0], 400)
            self.assertEqual(self.post('/memory/import', upload)[0], 400)

    def test_uncertain_native_operation_never_repeats_callback(self):
        count = []
        def fail(text):
            count.append(text); raise OSError('Private failure detail.')
        for _ in range(2):
            with self.assertRaises(ValueError) as raised:
                native_tool_routes.invoke('notes_capture', {'text': 'synthetic'}, fail,
                    properties={'text': {'type': 'string'}}, body={'operation_session': 'e' * 24}, duration_s=5)
            self.assertNotIn('Private failure detail', str(raised.exception))
        self.assertEqual(count, ['synthetic'])


if __name__ == '__main__':
    unittest.main()
