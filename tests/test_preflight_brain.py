"""Checker selects local Brain safely without activating/indexing it or live HTTP."""
import contextlib
import io
import json
import tempfile
import unittest
import urllib.parse
from email.message import Message
from pathlib import Path
from unittest.mock import patch

import build
import note_store
import obsidian_brain
import preflight
import server


class PreflightBrainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='jarvis-preflight-brain-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.vault = self.root / 'Jarvis Brain'
        self.vault.mkdir()
        (self.vault / 'Plan.md').write_text('# Plan\nThe public launch is Friday.', encoding='utf-8')
        self.legacy = self.root / 'legacy'
        self.legacy.mkdir()
        (self.legacy / 'Old.md').write_text('# Old\nUnrelated old demo.', encoding='utf-8')
        self.brain = obsidian_brain.BrainService(self.root / 'private-settings', self.vault)
        self.addCleanup(self.brain.close)
        self.graph = build.make_graph(build.read_notes(self.vault))
        self.live = {'schema_version': 1, 'configured': True, 'active': True,
                     'restart_required': False, 'vault_name': self.vault.name}
        self.source_folder = self.vault
        self.calls = []
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(server, 'BRAIN', self.brain))
        self.stack.enter_context(patch.object(build, 'NOTES_DIR', self.legacy))
        self.stack.enter_context(patch.object(build, 'GRAPH_PATH', self.root / 'graph.js'))
        for target in ('obsidian_brain.BrainService.activate', 'obsidian_brain.BrainService.refresh',
                       'obsidian_brain.BrainIndex._open', 'server.configure_brain_startup',
                       'server.subprocess.Popen', 'server.urllib.request.urlopen'):
            self.stack.enter_context(patch(target, side_effect=AssertionError('Activation/live work forbidden')))
        self.harness = preflight.Harness('http://127.0.0.1:4700', True, allow_capture_cleanup=True)
        self.harness.graph = self.graph
        self.stack.enter_context(patch.object(self.harness, 'request', side_effect=self.request))

    def response(self, value, status=200):
        headers = Message()
        headers['Content-Type'] = 'application/json'
        return status, headers, json.dumps(value).encode()

    def request(self, path, body=None, timeout=None, method=None):
        self.calls.append((path, body, method))
        if path == '/brain':
            if self.live is None:
                return 404, Message(), b'Not found'
            return self.response(self.live)
        if path == '/note?id=0':
            return self.response({'stable_id': note_store.stable_id('Plan.md'),
                'obsidian_uri': 'obsidian://open?' + urllib.parse.urlencode({'path': str(self.source_folder / 'Plan.md')})})
        if path == '/graph-data.js':
            return 200, Message(), build.graph_bytes(self.graph)
        if path == '/':
            return 200, Message(), b'<html></html>'
        raise AssertionError('Unexpected HTTP boundary: ' + path)

    def configured(self):
        self.brain.configure(vault_path=self.vault)

    def assert_no_index(self):
        self.assertIsNone(self.brain.index)
        self.assertIsNone(self.brain.active_path)
        self.assertFalse((self.brain.root / 'indexes').exists())
        self.assertFalse(list(self.root.rglob('*.sqlite*')))

    def test_default_full_and_quick_checks_preserve_notes_without_capture_requests(self):
        for quick in (False, True):
            with self.subTest(quick=quick):
                harness = preflight.Harness('http://127.0.0.1:4700', quick)
                with patch.object(harness, 'request') as request, patch.object(harness, 'notes_directory') as scope:
                    with self.assertRaisesRegex(preflight.CheckWarning, '--allow-capture-cleanup'):
                        harness.capture()
                request.assert_not_called()
                scope.assert_not_called()
        self.assertEqual(len(list(self.vault.iterdir())), 1)
        self.assertFalse((self.vault / 'captures').exists())

    def test_capture_skip_is_reported_as_warning_instead_of_pass(self):
        harness = preflight.Harness('http://127.0.0.1:4700', False)
        with contextlib.redirect_stdout(io.StringIO()):
            harness.run('Capture', harness.capture)
        self.assertEqual(harness.counts, {'pass': 0, 'fail': 0, 'warn': 1})

    def test_selected_settings_and_live_scope_choose_vault_without_checker_activation(self):
        self.configured()
        before = self.brain.settings_path.read_bytes()
        self.assertEqual(self.harness.notes_directory(), self.vault)
        self.assertEqual(self.brain.settings_path.read_bytes(), before)
        self.assertEqual(build.NOTES_DIR, self.legacy)
        self.assert_no_index()
        self.assertEqual([call[0] for call in self.calls], ['/brain', '/note?id=0'])

    def test_unconfigured_new_and_legacy_404_servers_keep_legacy_scope_without_storage(self):
        for live in (None, {'schema_version': 1, 'configured': False, 'active': False,
                            'restart_required': False, 'vault_name': None}):
            with self.subTest(live=live):
                self.live = live
                self.assertEqual(self.harness.notes_directory(), self.legacy)
        self.assertFalse(self.brain.root.exists())
        self.assert_no_index()

    def test_pending_disconnected_mismatched_and_legacy_status_fail_before_capture_write(self):
        self.configured()
        cases = [dict(self.live, restart_required=True), dict(self.live, active=False),
                 dict(self.live, configured=False), dict(self.live, vault_name='Other'),
                 dict(self.live, active=1), None]
        for live in cases:
            with self.subTest(live=live):
                self.live = live
                self.calls.clear()
                with self.assertRaises(preflight.CheckFailure):
                    self.harness.capture()
                self.assertEqual([call[0] for call in self.calls], ['/brain'])
                self.assertFalse((self.vault / 'captures').exists())
        self.assert_no_index()

    def test_local_unconfigured_but_live_connected_fails_before_write(self):
        with self.assertRaises(preflight.CheckFailure):
            self.harness.capture()
        self.assertEqual([call[0] for call in self.calls], ['/brain'])
        self.assertFalse(self.brain.root.exists())

    def test_identically_named_different_live_vault_cannot_pass_scope_check(self):
        self.configured()
        self.source_folder = self.root / 'different-parent' / self.vault.name
        self.source_folder.mkdir(parents=True)
        (self.source_folder / 'Plan.md').write_text('Same name, different vault.', encoding='utf-8')
        with self.assertRaises(preflight.CheckFailure):
            self.harness.capture()
        self.assertEqual([call[0] for call in self.calls], ['/brain', '/note?id=0'])
        self.assertFalse((self.vault / 'captures').exists())
        self.assert_no_index()

    def test_missing_selected_folder_or_unverifiable_source_fail_closed(self):
        self.configured()
        for scenario in ('missing-folder', 'no-real-source', 'outside-uri'):
            with self.subTest(scenario=scenario):
                if scenario == 'missing-folder':
                    selected = self.vault.with_name('moved')
                    self.vault.rename(selected)
                    try:
                        with self.assertRaises(preflight.CheckFailure):
                            self.harness.notes_directory()
                    finally:
                        selected.rename(self.vault)
                elif scenario == 'no-real-source':
                    with patch.object(self.harness, 'graph', {'nodes': [{'id': 0, 'capture_hub': True}]}):
                        with self.assertRaises(preflight.CheckFailure):
                            self.harness.notes_directory()
                else:
                    self.source_folder = self.legacy
                    with self.assertRaises(preflight.CheckFailure):
                        self.harness.notes_directory()
        self.assert_no_index()

    def test_served_comparison_reads_selected_notes_without_mutating_legacy_default(self):
        self.configured()
        with patch.object(server, 'PUBLIC_FILES', {}), patch.object(build, 'read_notes', wraps=build.read_notes) as reader:
            result = self.harness.served_files()
        self.assertIn('live note index matches disk', result)
        reader.assert_called_once_with(self.vault)
        self.assertEqual(build.NOTES_DIR, self.legacy)
        self.assert_no_index()

    def test_real_capture_fixture_is_checked_and_cleaned_only_in_selected_folder(self):
        self.configured()
        original = self.request
        saved = []
        def request(path, body=None, timeout=None, method=None):
            if path == '/preflight/status':
                return self.response({'protocol': server.PREFLIGHT_PROTOCOL})
            if path == '/remember':
                token = body['preflight_token']
                folder = self.vault / 'captures'
                folder.mkdir()
                target = folder / ('Preflight ' + token + ' fixture.md')
                target.write_text('# Preflight ' + token + '\n\n' + body['question'], encoding='utf-8')
                saved.append(target)
                notes = build.read_notes(self.vault)
                self.graph = build.make_graph(notes)
                build.GRAPH_PATH.write_bytes(build.graph_bytes(self.graph))
                captured = next(n['id'] for n in notes if token in n['label'])
                return self.response({'captured': captured})
            if path == '/preflight/cleanup':
                saved[0].unlink()
                self.graph = build.make_graph(build.read_notes(self.vault))
                build.GRAPH_PATH.write_bytes(build.graph_bytes(self.graph))
                return self.response({'cleaned': True})
            return original(path, body, timeout, method)
        with patch.object(self.harness, 'request', side_effect=request):
            with self.assertRaises(preflight.CheckWarning):
                self.harness.capture()
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].parent, self.vault / 'captures')
        self.assertFalse(saved[0].exists())
        self.assertFalse((self.legacy / 'captures').exists())
        self.assert_no_index()


if __name__ == '__main__':
    unittest.main()
