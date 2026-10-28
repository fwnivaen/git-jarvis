"""Exports use real hostile temporary vaults and remain entirely in memory."""
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

from activity_feed import ActivityFeed
from daily_health import OperationsSettings
import user_data_export
from user_data_export import UserDataExport, ExportError

NOW = 1000.0
TOKEN = 'sk-example-secret-1234567890'
PRIVATE = '/Users/private-person/config.json'


class UserDataExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.vault = self.root/'vault'
        (self.vault/'Team').mkdir(parents=True)
        (self.vault/'Team'/'Plan.md').write_text('# Plan\n\nUser-owned evidence. 😀')
        (self.vault/'Credentials.md').write_text('User note with api_key=' + TOKEN)
        (self.vault/'.obsidian').mkdir()
        (self.vault/'.obsidian'/'hidden.md').write_text('HIDDEN_PRIVATE')
        (self.vault/'.secret.md').write_text('HIDDEN_PRIVATE')
        (self.vault/'config.json').write_text(json.dumps({'api_key': TOKEN}))
        self.outside = self.root/'outside.md'
        self.outside.write_text('OUTSIDE_PRIVATE')
        (self.vault/'link.md').symlink_to(self.outside)
        (self.vault/'linked').symlink_to(self.root, target_is_directory=True)
        os.mkfifo(self.vault/'pipe.md')
        (self.vault/'binary.md').write_bytes(b'\xffinvalid')
        self.notes = [{'_path': path} for path in ('Team/Plan.md', 'Credentials.md',
            '.obsidian/hidden.md', '.secret.md', 'config.json', '../outside.md',
            str(self.outside), 'link.md', 'linked/outside.md', 'pipe.md', 'binary.md',
            './Team/Plan.md', 'Team//Plan.md', 'Team/../Credentials.md', 'C:/secret.md')]
        self.notes += [{'capture_hub': True}, {'_path': 'Team/Plan.md'}]
        self.preferences = {'tone': 'Concise', 'api_key': TOKEN, 'contact': 'User-authored preference',
            'example': 'Bearer abcdefghijklmnop'}
        self.settings = {'enabled': True, 'max_workers': 3, 'quick_model': 'gpt-6-luna',
            'api_key': TOKEN, 'config_path': PRIVATE, 'process_id': 1234,
            'brain_model': TOKEN, 'worker_retries': True, 'unknown_future_field': PRIVATE}
        self.workflows = {'runs': [{'id': 'a'*24, 'workflow': 'general', 'status': 'complete',
            'created_at': 900, 'question': 'Question with password=do-not-export',
            'result': {'answer': 'Saved answer with ' + TOKEN, 'private': PRIVATE},
            'agents': [{'raw': PRIVATE}], 'process_id': 1234, 'events': []}]}
        self.activity = ActivityFeed(workflows=lambda: self.workflows, clock=lambda: NOW).snapshot()
        self.activity['items'][0].update(label=PRIVATE, arguments=TOKEN, process_id=1234)
        self.calls = {key: Mock(return_value=value) for key, value in (
            ('indexed_notes', self.notes), ('preferences', self.preferences),
            ('settings', self.settings), ('workflows', self.workflows), ('activity', self.activity))}
        self.exporter = UserDataExport(self.vault, **self.calls, clock=lambda: NOW)

    def unpack(self, sections):
        result = self.exporter.create(sections)
        self.assertIsInstance(result, bytes)
        with zipfile.ZipFile(io.BytesIO(result)) as archive:
            self.assertIsNone(archive.testzip())
            for info in archive.infolist():
                self.assertEqual(info.external_attr >> 16, 0o600)
                self.assertFalse(info.filename.startswith('/'))
                self.assertNotIn('..', Path(info.filename).parts)
            return {name: archive.read(name) for name in archive.namelist()}

    def test_explicit_full_export_is_allowlisted_sanitized_and_has_no_disk_output(self):
        before = set(self.root.rglob('*'))
        files = self.unpack(['notes', 'preferences', 'settings', 'workflows', 'activity'])
        self.assertEqual(set(self.root.rglob('*')), before)
        self.assertEqual(set(files), {'notes/Team/Plan.md', 'notes/Credentials.md',
            'preferences.json', 'settings.json', 'workflows.json', 'activity.json', 'manifest.json'})
        self.assertIn('😀', files['notes/Team/Plan.md'].decode())
        combined = b'\n'.join(files.values()).decode()
        for excluded in (TOKEN, PRIVATE, 'HIDDEN_PRIVATE', 'OUTSIDE_PRIVATE', 'do-not-export',
                'abcdefghijklmnop', 'process_id', 'unknown_future_field'):
            self.assertNotIn(excluded, combined)
        self.assertEqual(json.loads(files['settings.json']),
            {'enabled': True, 'max_workers': 3, 'quick_model': 'gpt-6-luna'})
        self.assertNotIn('api_key', json.loads(files['preferences.json']))
        self.assertEqual(json.loads(files['activity.json'])['items'][0]['label'], 'Work status: complete')
        manifest = json.loads(files['manifest.json'])
        self.assertEqual(manifest['counts']['notes'], 2)
        self.assertEqual(manifest['skipped_notes'], len(self.notes)-2)
        self.assertEqual(manifest['skipped_preferences'], 1)
        self.assertGreaterEqual(manifest['redactions'], 4)
        for entry in manifest['files']:
            self.assertEqual(entry['bytes'], len(files[entry['name']]))
            self.assertEqual(entry['sha256'], hashlib.sha256(files[entry['name']]).hexdigest())

    def test_unselected_sources_are_never_called_or_opened(self):
        self.exporter.vault = self.root/'nonexistent-vault'
        for key in ('indexed_notes', 'preferences', 'workflows', 'activity'):
            self.calls[key].side_effect = AssertionError('Unselected source was read')
        with patch.object(user_data_export.os, 'open', side_effect=AssertionError('Unexpected filesystem access')):
            files = self.unpack(['settings'])
        self.assertEqual(set(files), {'settings.json', 'manifest.json'})
        self.calls['settings'].assert_called_once_with()
        for key in ('indexed_notes', 'preferences', 'workflows', 'activity'):
            self.calls[key].assert_not_called()

    def test_sections_are_explicit_unique_and_cannot_select_configs_or_processes(self):
        for sections in ([], None, 'notes', ['config'], ['credentials'], ['processes'],
                ['notes', 'notes'], ['settings', True], ['../private']):
            with self.subTest(sections=sections), self.assertRaises(ExportError):
                self.exporter.create(sections)
        for provider in self.calls.values():
            provider.assert_not_called()

    def test_index_is_the_only_discovery_source_and_hidden_files_are_not_opened(self):
        self.calls['indexed_notes'].return_value = [{'_path': '.obsidian/hidden.md'},
            {'_path': '.secret.md'}, {'_path': 'config.json'}, {'_path': '../outside.md'}]
        original = user_data_export.os.open
        opened = []
        def opening(path, *args, **kwargs):
            opened.append(str(path))
            return original(path, *args, **kwargs)
        with patch.object(user_data_export.os, 'open', side_effect=opening):
            files = self.unpack(['notes'])
        self.assertEqual(set(files), {'manifest.json'})
        self.assertEqual(opened, [str(self.vault)])

    def test_symlink_vault_and_oversized_or_changed_notes_fail_closed(self):
        linked = self.root/'vault-link'
        linked.symlink_to(self.vault, target_is_directory=True)
        self.exporter.vault = linked
        with self.assertRaises(ExportError):
            self.exporter.create(['notes'])
        self.exporter.vault = self.vault
        self.calls['indexed_notes'].return_value = [{'_path': 'Team/Plan.md'}]
        with patch.object(user_data_export.note_store, 'MAX_NOTE_BYTES', 1):
            files = self.unpack(['notes'])
        self.assertEqual(json.loads(files['manifest.json'])['counts']['notes'], 0)
        self.assertEqual(json.loads(files['manifest.json'])['skipped_notes'], 1)

    def test_count_and_size_limits_return_no_partial_archive(self):
        with patch.object(user_data_export, 'MAX_NOTES', 1):
            with self.assertRaises(ExportError) as error:
                self.exporter.create(['notes'])
            self.assertEqual(error.exception.status, 413)
        with patch.object(user_data_export, 'MAX_TOTAL_BYTES', 1):
            with self.assertRaises(ExportError) as error:
                self.exporter.create(['settings'])
            self.assertEqual(error.exception.status, 413)
        with patch.object(user_data_export, 'MAX_ARCHIVE_BYTES', 1):
            with self.assertRaises(ExportError):
                self.exporter.create(['settings'])

    def test_bad_sources_and_errors_never_expose_paths_or_tokens(self):
        self.calls['settings'].side_effect = OSError(PRIVATE + TOKEN)
        with self.assertRaises(ExportError) as error:
            self.exporter.create(['settings'])
        self.assertNotIn(PRIVATE, str(error.exception))
        self.assertNotIn(TOKEN, str(error.exception))
        self.assertEqual(error.exception.status, 503)
        self.exporter.providers['activity'] = None
        with self.assertRaises(ExportError):
            self.exporter.create(['activity'])
        self.calls['settings'].side_effect = None
        self.calls['settings'].return_value = None
        with self.assertRaises(ExportError):
            self.exporter.create(['settings'])

    def test_incomplete_private_keys_and_secret_filenames_do_not_escape(self):
        (self.vault/'Key.md').write_text('Before\n-----BEGIN ' + 'PRIVATE KEY-----\nPRIVATE_KEY_MATERIAL')
        (self.vault/(TOKEN+'.md')).write_text('Filename should not expose a token.')
        self.calls['indexed_notes'].return_value = [{'_path': 'Key.md'}, {'_path': TOKEN+'.md'}]
        files = self.unpack(['notes'])
        self.assertEqual(files['notes/Key.md'], b'Before\n[redacted]')
        self.assertNotIn(TOKEN, str(files))
        self.assertNotIn('PRIVATE_KEY_MATERIAL', str(files))

    def test_stale_index_cannot_export_newly_private_notes_or_excluded_folders(self):
        private_paths = ('Private/Personal.md', 'Team/Secrets/Personal.md',
            'node_modules/Package.md', 'Team/private/Personal.md')
        for relative in private_paths:
            path = self.vault/relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('EXCLUDED_FOLDER_CONTENT')
        self.calls['indexed_notes'].return_value = [{'_path': path} for path in private_paths]
        self.calls['indexed_notes'].return_value += [{'_path': 'Team/Plan.md'}]
        # The index still contains the previously public note. Recheck its fresh
        # frontmatter rather than exporting its indexed or current private text.
        for marker in ('jarvis_private: true', 'jarvis_exclude: yes'):
            (self.vault/'Team'/'Plan.md').write_text('---\n' + marker + '\n---\nNEW_PRIVATE_CONTENT')
            with self.subTest(marker=marker):
                files = self.unpack(['notes'])
                self.assertEqual(set(files), {'manifest.json'})
                self.assertEqual(json.loads(files['manifest.json'])['counts']['notes'], 0)
                self.assertNotIn('NEW_PRIVATE_CONTENT', str(files))
                self.assertNotIn('EXCLUDED_FOLDER_CONTENT', str(files))

    def test_optional_settings_export_is_allowlisted_and_selected_only(self):
        brain = Mock(return_value={'schema_version': 1, 'context_chars': 6000,
            'vault_path': PRIVATE, 'api_key': TOKEN})
        operations = Mock(return_value=OperationsSettings(daily_health_enabled=True,
            quiet_hours_enabled=True, speak_background_updates=True))
        self.exporter.optional_settings = {'brain': brain, 'operations': operations}
        files = self.unpack(['preferences'])
        brain.assert_not_called()
        operations.assert_not_called()
        self.assertNotIn('brain-settings.json', files)
        files = self.unpack(['settings'])
        brain.assert_called_once_with()
        operations.assert_called_once_with()
        self.assertEqual(json.loads(files['brain-settings.json']), {'context_chars': 6000})
        settings = json.loads(files['operations-settings.json'])
        self.assertEqual(settings['timezone'], 'America/New_York')
        self.assertEqual(settings['quiet_start'], '22:00')
        self.assertIs(settings['daily_health_enabled'], True)
        self.assertIs(settings['speak_background_updates'], True)
        self.assertEqual(len(settings), 11)
        self.assertNotIn(PRIVATE, str(files))
        self.assertNotIn(TOKEN, str(files))
        self.assertNotIn('vault_path', str(files))

    def test_optional_settings_reject_malformed_values_and_hide_runtime_fields(self):
        self.exporter.optional_settings = {
            'brain': lambda: {'context_chars': True, 'vault_path': PRIVATE},
            'operations': lambda: {'daily_health_enabled': 1, 'daily_health_time': '25:61',
                'quiet_start': '9:00', 'quiet_end': '08:00\n', 'timezone': PRIVATE,
                'minimum_battery_percent': True, 'last_result': {'path': PRIVATE},
                'process_id': 1234, 'running': True}}
        files = self.unpack(['settings'])
        self.assertEqual(json.loads(files['brain-settings.json']), {})
        self.assertEqual(json.loads(files['operations-settings.json']), {})
        for excluded in (PRIVATE, 'vault_path', 'last_result', 'process_id', 'running'):
            self.assertNotIn(excluded, str(files))
        for invalid in (999, 12001, -1, 6000.0, '6000'):
            self.exporter.optional_settings['brain'] = lambda: {'context_chars': invalid}
            self.assertEqual(json.loads(self.unpack(['settings'])['brain-settings.json']), {})

    def test_optional_settings_provider_failure_returns_no_archive_or_private_error(self):
        self.exporter.optional_settings['brain'] = Mock(side_effect=OSError(PRIVATE + TOKEN))
        with self.assertRaises(ExportError) as error:
            self.exporter.create(['settings'])
        self.assertEqual(error.exception.status, 503)
        self.assertNotIn(PRIVATE, str(error.exception))
        self.assertNotIn(TOKEN, str(error.exception))


if __name__ == '__main__':
    unittest.main()
