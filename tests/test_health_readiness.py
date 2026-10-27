"""Readiness reports facts without activating services or reading private data."""
import copy
import hashlib
import json
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import patch

import health_readiness as health

SCHEMA = {'type': 'object', 'additionalProperties': False,
    'properties': {'answer': {'type': 'string'}, 'about_notes': {'type': 'boolean'},
        'nodes': {'type': 'array', 'items': {'type': 'integer'}}},
    'required': ['answer', 'about_notes', 'nodes']}
PRIVATE_MARKER = 'DO-NOT-RETURN-PRIVATE-FIXTURE-CONTENTS'


class HealthReadinessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'project'
        self.root.mkdir()
        (self.root / 'viewer').mkdir()
        for name in ('server.py', 'viewer/index.html', 'viewer/graph-data.js'):
            (self.root / name).write_text(PRIVATE_MARKER)
        (self.root / 'notes').mkdir()
        (self.root / 'notes' / (PRIVATE_MARKER + '.md')).write_text(PRIVATE_MARKER)
        self.config = self.root / 'config.json'
        self.config.write_text(json.dumps({'key': PRIVATE_MARKER}))
        self.config.chmod(0o600)
        self.store = self.root / '.local' / 'graph-workflows'
        self.store.mkdir(parents=True, mode=0o700)
        self.catalog = self.store / 'catalog.sqlite'
        # Deliberately not a valid database: the report must describe only a
        # header, never pretend that a metadata check proves DB integrity.
        self.catalog.write_bytes(health.SQLITE_HEADER + PRIVATE_MARKER.encode())
        self.catalog.chmod(0o600)
        self.command = self.root / 'fixture-command'
        self.command.write_text('#!/bin/sh\nexit 91\n')
        self.command.chmod(0o700)
        self.bundle = self.root / '.local' / 'Jarvis Desktop.app'
        macos = self.bundle / 'Contents' / 'MacOS'
        macos.mkdir(parents=True)
        (macos / 'JarvisDesktop').write_text('This fixture must never execute.')
        (macos / 'JarvisDesktop').chmod(0o700)
        self.info = self.bundle / 'Contents' / 'Info.plist'
        self.info.write_bytes(plistlib.dumps({'CFBundleExecutable': 'JarvisDesktop',
            'CFBundleIdentifier': 'local.fwnivaen.jarvis.desktop'}))
        self.arguments = dict(server_running=True, backend='codex-cli',
            gates={name: True for name in health.GATE_NAMES}, answer_schema=SCHEMA,
            tool_paths={name: self.command for name in health.TOOL_NAMES})

    def snapshot(self, **changes):
        arguments = dict(self.arguments, **changes)
        return health.snapshot(self.root, **arguments)

    def checks(self, result):
        return {check['id']: check for check in result['checks']}

    def tree(self):
        return {str(path.relative_to(self.root)): (
            path.lstat().st_mode, path.lstat().st_mtime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None)
            for path in self.root.rglob('*')}

    def test_actual_metadata_and_contract_are_reported_without_claiming_live_success(self):
        result = self.snapshot()
        checks = self.checks(result)
        for identifier in ('project', 'server', 'build', 'answer_schema', 'private_config',
                'notes_directory', 'backend', 'tool_codex', 'work_store', 'native_artifact'):
            self.assertEqual(checks[identifier]['status'], 'ok', identifier)
        self.assertEqual(result['capabilities']['work_store'], 'present')
        self.assertTrue(result['capabilities']['included_model_route_configured'])
        self.assertTrue(result['capabilities']['work_enabled'])
        for name in ('model_login_verified', 'model_allowance_verified', 'native_running_verified',
                'live_voice_verified', 'battery_signal_collected', 'network_signal_collected'):
            self.assertFalse(result['capabilities'][name])
        self.assertFalse(result['first_run']['complete'])
        self.assertEqual(checks['voice_permissions']['status'], 'not_checked')
        self.assertIn('No database was opened', checks['work_store']['detail'])
        self.assertTrue(all(set(item) == {'id', 'title', 'status', 'detail', 'action', 'required'}
            for item in result['checks']))

    def test_no_execution_network_database_private_reads_or_mutations(self):
        before = self.tree()
        forbidden = AssertionError('Readiness activated a capability')
        with patch('subprocess.Popen', side_effect=forbidden), \
                patch('subprocess.run', side_effect=forbidden), \
                patch('socket.create_connection', side_effect=forbidden), \
                patch('urllib.request.urlopen', side_effect=forbidden), \
                patch('sqlite3.connect', side_effect=forbidden), \
                patch.object(Path, 'read_text', side_effect=forbidden):
            result = self.snapshot()
        self.assertFalse(any(result['side_effects'].values()))
        self.assertEqual(self.tree(), before)
        encoded = json.dumps(result)
        self.assertNotIn(PRIVATE_MARKER, encoded)
        self.assertNotIn(str(self.root), encoded)
        self.assertNotIn(str(self.command), encoded)

    def test_absent_project_or_store_never_creates_anything(self):
        missing = Path(self.tmp.name) / 'not-created'
        result = health.snapshot(missing, tool_paths={})
        self.assertEqual(self.checks(result)['project']['status'], 'blocked')
        self.assertFalse(missing.exists())
        self.catalog.unlink()
        self.store.rmdir()
        result = self.snapshot()
        self.assertEqual(result['capabilities']['work_store'], 'not_initialized')
        self.assertEqual(self.checks(result)['work_store']['status'], 'not_checked')
        self.assertFalse(self.store.exists())

    def test_private_config_and_notes_symlinks_are_not_followed(self):
        self.config.unlink()
        self.config.symlink_to(self.command)
        notes = self.root / 'notes'
        for child in notes.iterdir():
            child.unlink()
        notes.rmdir()
        notes.symlink_to(self.root / 'viewer', target_is_directory=True)
        result = self.snapshot()
        self.assertEqual(self.checks(result)['private_config']['status'], 'blocked')
        self.assertEqual(self.checks(result)['notes_directory']['status'], 'blocked')

    def test_bad_permissions_and_store_headers_produce_bounded_guidance(self):
        self.config.chmod(0o644)
        self.store.chmod(0o755)
        result = self.snapshot()
        checks = self.checks(result)
        self.assertEqual(checks['private_config']['status'], 'warning')
        self.assertEqual(checks['work_store']['status'], 'warning')
        self.assertEqual(result['status'], 'attention')
        self.store.chmod(0o700)
        self.catalog.write_bytes(b'bad-header' + PRIVATE_MARKER.encode())
        result = self.snapshot()
        self.assertEqual(result['capabilities']['work_store'], 'unverified')
        self.assertNotIn(PRIVATE_MARKER, json.dumps(result))

    def test_only_header_and_bounded_native_manifest_are_read(self):
        with patch.object(health, '_read_prefix', wraps=health._read_prefix) as read:
            self.snapshot()
        self.assertEqual([(Path(call.args[0]).name, call.args[1]) for call in read.call_args_list],
            [('catalog.sqlite', len(health.SQLITE_HEADER)), ('Info.plist', health.MAX_PLIST_BYTES + 1)])
        self.info.write_bytes(b'x' * (health.MAX_PLIST_BYTES + 1))
        with patch.object(health, '_read_prefix', wraps=health._read_prefix) as read:
            result = self.snapshot()
        self.assertEqual(read.call_count, 1)
        self.assertFalse(result['capabilities']['native_artifact_present'])

    def test_supplied_native_permission_facts_are_normalized_and_never_probe(self):
        result = self.snapshot(native_status={'microphone': 'allowed', 'speech': 'not requested',
            'on_device_model_ready': True})
        self.assertTrue(result['capabilities']['voice_readiness_supplied'])
        self.assertFalse(result['capabilities']['live_voice_verified'])
        blocked = self.snapshot(native_status={'microphone': 'denied', 'speech': 'allowed'})
        self.assertEqual(self.checks(blocked)['voice_permissions']['status'], 'warning')
        modern = self.snapshot(native_status={'microphone': 'allowed', 'speech': 'denied',
            'on_device_model_ready': True})
        self.assertEqual(self.checks(modern)['voice_permissions']['status'], 'ok')

    def test_bad_inputs_and_parser_errors_cannot_reflect_sensitive_values(self):
        for changes in ({'gates': {'enabled': PRIVATE_MARKER}}, {'gates': {PRIVATE_MARKER: True}},
                {'backend': PRIVATE_MARKER}, {'native_status': {'microphone': []}},
                {'native_status': {PRIVATE_MARKER: True}}, {'workflow_root': []},
                {'native_bundle': []}, {'answer_schema': PRIVATE_MARKER},
                {'settings_notice': PRIVATE_MARKER}, {'tool_paths': {'codex': []}}):
            with self.subTest(changes=tuple(changes)):
                result = self.snapshot(**changes)
                self.assertNotIn(PRIVATE_MARKER, json.dumps(result))
        self.info.write_bytes(b'<plist><broken>')
        self.assertFalse(self.snapshot()['capabilities']['native_artifact_present'])
        with patch.object(Path, 'lstat', side_effect=PermissionError(PRIVATE_MARKER)):
            result = self.snapshot()
        self.assertEqual(self.checks(result)['project']['status'], 'blocked')
        self.assertNotIn(PRIVATE_MARKER, json.dumps(result))
        self.assertEqual(self.checks(health.snapshot([], tool_paths={}))['project']['status'], 'blocked')

    def test_invalid_contract_disabled_work_and_disallowed_backends_are_distinct(self):
        schema = copy.deepcopy(SCHEMA)
        schema['additionalProperties'] = True
        invalid = self.snapshot(answer_schema=schema)
        self.assertEqual(self.checks(invalid)['answer_schema']['status'], 'blocked')
        disabled = self.snapshot(gates={'enabled': False})
        self.assertFalse(disabled['capabilities']['work_enabled'])
        self.assertEqual(self.checks(disabled)['work_activation']['status'], 'disabled')
        paid = self.snapshot(backend='openai-api')
        self.assertEqual(self.checks(paid)['backend']['status'], 'blocked')
        self.assertFalse(paid['capabilities']['included_model_route_configured'])
        unknown = self.snapshot(backend=None)
        self.assertEqual(self.checks(unknown)['backend']['status'], 'not_checked')


if __name__ == '__main__':
    unittest.main()
