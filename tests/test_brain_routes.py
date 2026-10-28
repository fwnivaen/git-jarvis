"""Real local service fixtures behind fake HTTP: no sockets, models or personal data."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

import activity_feed
import build
import daily_health
import obsidian_brain
import server
from test_offline import request


ORIGIN = {'Origin': 'http://127.0.0.1:4700'}
PRIVATE_MARKER = 'NEVER_RETURN_PRIVATE_ZEBRA'


class BrainRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='jarvis-brain-routes-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.brain = obsidian_brain.BrainService(self.root / 'brain-settings', self.root / 'Jarvis Brain')
        self.operations = daily_health.DailyHealthScheduler(self.root / 'operations',
            lambda: {'checks': []}, signal_provider=Mock(side_effect=AssertionError('OS probes forbidden')))
        self.activity = activity_feed.ActivityFeed(workflows=lambda: {'runs': []},
            approvals=lambda: {'items': []})
        self.addCleanup(self.brain.close)
        self.addCleanup(self.operations.stop)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.legacy = self.root / 'legacy-notes'
        self.legacy.mkdir()
        for target, name, value in (
                (server, 'ROOT', self.root), (server, 'BRAIN', self.brain),
                (server, 'OPERATIONS', self.operations), (server, 'ACTIVITY', self.activity),
                (server, 'NOTES', []), (server, 'PREFERENCES', Mock(read=Mock(return_value={}))),
                (server, 'NOTE_ACTION_PATH', self.root / 'legacy-actions.sqlite3'),
                (server, 'PREFERENCE_ACTION_PATH', self.root / 'legacy-preference-actions.sqlite3'),
                (build, 'NOTES_DIR', self.legacy),
                (server.WORKFLOWS, 'root', self.root / 'legacy-workflows')):
            self.stack.enter_context(patch.object(target, name, value))
        self.stack.enter_context(patch.object(build, 'write_graph', return_value=None))
        for target in ('server.ask_brain', 'server.answer_request',
                       'server.urllib.request.urlopen', 'server.subprocess.Popen',
                       'local_signals._run_probe'):
            self.stack.enter_context(patch(target, side_effect=AssertionError('External work forbidden')))

    def json_request(self, path, method='GET', body=None, headers=None):
        status, fields, raw = request(path, method, body, headers)
        return status, fields, json.loads(raw) if raw else None

    def activate(self):
        vault = self.root / 'shared-vault'
        vault.mkdir()
        (vault / 'Plan.md').write_text('# Plan\nThe public zebra launch starts Friday.', encoding='utf-8')
        (vault / 'Hidden.md').write_text('---\njarvis_private: true\n---\n' + PRIVATE_MARKER, encoding='utf-8')
        (vault / 'Private').mkdir()
        (vault / 'Private' / 'Diary.md').write_text(PRIVATE_MARKER, encoding='utf-8')
        self.brain.configure(vault_path=vault)
        self.brain.activate()
        build.NOTES_DIR = vault.resolve()
        server.refresh_brain_notes(force=True)
        return vault

    def test_default_status_reads_are_lazy_and_do_not_probe_or_create_storage(self):
        with patch.object(server, 'active_settings', return_value={'brain': 'codex-cli'}), \
                patch.object(server.ultra.Settings, 'read', return_value=(server.ultra.Settings(), None)), \
                patch.object(server, 'find_codex', return_value=None):
            values = {}
            for path in ('/brain', '/operations', '/activity', '/health'):
                status, _, values[path] = self.json_request(path)
                self.assertEqual(status, 200, path)
        self.assertFalse(values['/brain']['active'])
        self.assertFalse(values['/brain']['configured'])
        self.assertFalse(values['/operations']['signals']['collected'])
        self.assertEqual(values['/operations']['signals']['reason'], 'disabled')
        self.assertEqual(values['/activity']['items'], [])
        self.assertIsNone(self.brain.index)
        self.assertFalse(self.brain.root.exists())
        self.assertFalse(self.operations.store.root.exists())
        self.assertEqual(list(self.root.iterdir()), [self.legacy])

    def test_head_operations_never_collects_enabled_signals(self):
        self.operations.configure({'local_signals_enabled': True})
        status, _, raw = request('/operations', 'HEAD')
        self.assertEqual(status, 200)
        self.assertEqual(raw, b'')

    def test_local_service_reads_and_mutations_reject_foreign_origins(self):
        for path in ('/brain', '/operations', '/activity', '/health'):
            with self.subTest(path=path):
                self.assertEqual(request(path, headers={'Origin': 'https://outside.example'})[0], 403)
        bodies = {'/brain/setup': {'confirmed': True}, '/brain/disconnect': {'confirmed': True},
            '/brain/settings': {'context_chars': 2000}, '/brain/refresh': {},
            '/brain/search': {'query': 'zebra'}, '/operations/run': {},
            '/operations/settings': {'settings': {}, 'expected_revision': 'stale'},
            '/export/data': {'sections': ['notes']}}
        for path, body in bodies.items():
            for headers in ({}, {'Origin': 'https://outside.example'}):
                with self.subTest(path=path, headers=headers):
                    self.assertEqual(request(path, 'POST', body, headers)[0], 403)
        self.assertFalse(self.brain.root.exists())
        self.assertFalse(self.brain.default_vault.exists())
        self.assertFalse(self.operations.store.root.exists())

    def test_local_mutations_reject_extra_fields_and_nonobject_bodies(self):
        bodies = {'/brain/setup': {'confirmed': True, 'vault_path': '/unrequested'},
            '/brain/disconnect': {'confirmed': True, 'now': True},
            '/brain/settings': {'context_chars': 2000, 'activate': True},
            '/brain/refresh': {'force': True}, '/brain/search': {'query': 'zebra', 'model': True},
            '/operations/run': {'daily': True},
            '/operations/settings': {'settings': {}, 'expected_revision': '', 'force': True},
            '/export/data': {'sections': ['notes'], 'include_config': True}}
        for path, body in bodies.items():
            for invalid in ([], body):
                with self.subTest(path=path, invalid=invalid):
                    self.assertEqual(request(path, 'POST', invalid, ORIGIN)[0], 400)
        self.assertFalse(self.brain.default_vault.exists())

    def test_setup_requires_boolean_confirmation_and_only_configures_future_startup(self):
        for invalid in ({}, {'confirmed': False}, {'confirmed': 1}, {'confirmed': 'yes'}):
            self.assertEqual(request('/brain/setup', 'POST', invalid, ORIGIN)[0], 400)
        original_scope = build.NOTES_DIR, server.WORKFLOWS.root, server.NOTE_ACTION_PATH
        status, _, value = self.json_request('/brain/setup', 'POST', {'confirmed': True}, ORIGIN)
        self.assertEqual(status, 200)
        self.assertTrue(value['configured'])
        self.assertTrue(value['restart_required'])
        self.assertFalse(value['active'])
        self.assertIsNone(self.brain.active_path)
        self.assertIsNone(self.brain.index)
        self.assertEqual((build.NOTES_DIR, server.WORKFLOWS.root, server.NOTE_ACTION_PATH), original_scope)
        self.assertEqual(server.NOTES, [])
        starter = self.brain.default_vault / 'Knowledge' / 'About me.md'
        self.assertIn('Nivaen Giritharan', starter.read_text(encoding='utf-8'))
        self.assertEqual(request('/brain/setup', 'POST', {'confirmed': True}, ORIGIN)[0], 400)
        self.assertFalse((self.brain.root / 'indexes').exists())

    def test_settings_validate_types_and_disconnect_keeps_active_scope_until_restart(self):
        self.activate()
        for invalid in (None, True, '6000', 999, 12001):
            with self.subTest(context_chars=invalid):
                self.assertEqual(request('/brain/settings', 'POST', {'context_chars': invalid}, ORIGIN)[0], 400)
        status, _, value = self.json_request('/brain/settings', 'POST', {'context_chars': 2000}, ORIGIN)
        self.assertEqual(status, 200)
        self.assertEqual(value['context_chars'], 2000)
        original_scope, original_notes = self.brain.active_path, list(server.NOTES)
        status, _, value = self.json_request('/brain/disconnect', 'POST', {'confirmed': True}, ORIGIN)
        self.assertEqual(status, 200)
        self.assertFalse(value['configured'])
        self.assertTrue(value['active'])
        self.assertTrue(value['restart_required'])
        self.assertEqual(self.brain.active_path, original_scope)
        self.assertEqual(build.NOTES_DIR, original_scope)
        self.assertEqual(server.NOTES, original_notes)

    def test_search_refreshes_exclusions_and_returns_sources_without_model_calls(self):
        vault = self.activate()
        status, _, value = self.json_request('/brain/search', 'POST', {'query': 'zebra'}, ORIGIN)
        self.assertEqual(status, 200)
        self.assertEqual(value['model_calls'], 0)
        self.assertEqual([item['title'] for item in value['items']], ['Plan'])
        self.assertTrue(value['items'][0]['source']['uri'].startswith('obsidian://open?path='))
        self.assertNotIn(PRIVATE_MARKER, json.dumps(value))
        (vault / 'Plan.md').write_text('---\njarvis_private: true\n---\n' + PRIVATE_MARKER, encoding='utf-8')
        status, _, value = self.json_request('/brain/search', 'POST', {'query': 'zebra'}, ORIGIN)
        self.assertEqual(status, 200)
        self.assertEqual(value['items'], [])
        self.assertEqual(self.brain.index.indexed, 0)
        for invalid in (None, True, '', 'x' * (obsidian_brain.MAX_QUERY_CHARS + 1)):
            self.assertEqual(request('/brain/search', 'POST', {'query': invalid}, ORIGIN)[0], 400)

    def test_notes_export_calls_only_selected_provider_and_omits_private_data(self):
        self.activate()
        with patch.object(server, 'ultra_settings', side_effect=AssertionError('Unselected settings read')), \
                patch.object(server.PREFERENCES, 'read', side_effect=AssertionError('Unselected preferences read')), \
                patch.object(server.WORKFLOWS, 'catalog', side_effect=AssertionError('Unselected work read')), \
                patch.object(self.activity, 'snapshot', side_effect=AssertionError('Unselected activity read')), \
                patch.object(self.operations, 'status', side_effect=AssertionError('Unselected operations read')):
            status, fields, raw = request('/export/data', 'POST', {'sections': ['notes']}, ORIGIN)
        self.assertEqual(status, 200)
        self.assertIn('Content-Type: application/zip', fields)
        self.assertIn('Content-Disposition: attachment', fields)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            files = {name: archive.read(name) for name in archive.namelist()}
        self.assertEqual(set(files), {'notes/Plan.md', 'manifest.json'})
        self.assertNotIn(PRIVATE_MARKER, repr(files))
        for sections in ([], ['config'], ['notes', 'notes'], 'notes'):
            self.assertEqual(request('/export/data', 'POST', {'sections': sections}, ORIGIN)[0], 400)

    def test_operations_stale_revision_is_409_without_losing_current_choice(self):
        status, _, initial = self.json_request('/operations')
        self.assertEqual(status, 200)
        update = {'settings': {'quiet_hours_enabled': True}, 'expected_revision': initial['revision']}
        status, _, saved = self.json_request('/operations/settings', 'POST', update, ORIGIN)
        self.assertEqual(status, 200)
        self.assertTrue(saved['settings']['quiet_hours_enabled'])
        self.assertNotEqual(saved['revision'], initial['revision'])
        stale = {'settings': {'speak_background_updates': True}, 'expected_revision': initial['revision']}
        status, _, conflict = self.json_request('/operations/settings', 'POST', stale, ORIGIN)
        self.assertEqual(status, 409)
        self.assertEqual(conflict['current']['revision'], saved['revision'])
        self.assertFalse(self.operations.status()['settings']['speak_background_updates'])
        self.assertTrue(self.operations.status()['settings']['quiet_hours_enabled'])

    def test_startup_namespaces_each_private_store_before_scope_activation(self):
        vault = self.activate()
        self.brain.close()
        self.brain.active_path = None
        self.brain.index = None
        namespace = hashlib.sha256(str(vault.resolve()).encode()).hexdigest()
        private = self.root / '.local' / 'vaults' / namespace
        module = SimpleNamespace(WorkflowStore=Mock(return_value=object()))
        with patch.object(server.ultra_memory, 'HybridMemory', return_value=object()) as memory, \
                patch.object(server.ultra_memory, 'Preferences', return_value=object()) as preferences, \
                patch.object(server, 'MEMORY'), patch.object(server, 'PREFERENCES'), \
                patch.object(server.native_tool_routes, 'JOURNAL_DIR'), \
                patch.object(server.ultra_tools, 'JOURNAL_DIR'), \
                patch.object(server.ULTRA, '_workflow_module', return_value=module), \
                patch.object(server.ULTRA, '_store', None):
            self.assertTrue(server.configure_brain_startup())
            memory.assert_called_once_with(root=private / 'memory')
            preferences.assert_called_once_with(private / 'preferences.json')
            module.WorkflowStore.assert_called_once_with(private / 'runs')
            self.assertEqual(server.NOTE_ACTION_PATH, private / 'note-actions' / 'control.sqlite3')
            self.assertEqual(server.PREFERENCE_ACTION_PATH, private / 'preference-actions' / 'control.sqlite3')
            self.assertEqual(server.native_tool_routes.JOURNAL_DIR, private / 'native-tools')
            self.assertEqual(server.ultra_tools.JOURNAL_DIR, private / 'tools')
            self.assertEqual(server.WORKFLOWS.root, private / 'workflows')
            self.assertEqual(build.NOTES_DIR, vault.resolve())
        self.assertFalse(private.exists())

    def test_unconfigured_startup_and_search_keep_existing_scope_without_new_storage(self):
        original = build.NOTES_DIR, server.WORKFLOWS.root, server.NOTE_ACTION_PATH
        with patch.object(server.ultra_memory, 'HybridMemory', side_effect=AssertionError('Unexpected memory')), \
                patch.object(server.ultra_memory, 'Preferences', side_effect=AssertionError('Unexpected preferences')):
            self.assertFalse(server.configure_brain_startup())
        self.assertEqual((build.NOTES_DIR, server.WORKFLOWS.root, server.NOTE_ACTION_PATH), original)
        status, _, value = self.json_request('/brain/search', 'POST', {'query': 'zebra'}, ORIGIN)
        self.assertEqual(status, 400)
        self.assertTrue(value['error'])
        self.assertIsNone(self.brain.index)
        self.assertFalse(self.brain.root.exists())
        self.assertFalse((self.root / '.local').exists())


if __name__ == '__main__':
    unittest.main()
