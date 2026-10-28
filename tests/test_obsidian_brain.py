"""Real local vault/FTS boundaries; no Obsidian app, model, or personal notes."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import build
import obsidian_brain as brain
import vault_files


class VaultFixture:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.vault = self.base / 'Vault'
        self.vault.mkdir()

    def write(self, relative, text):
        path = self.vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path


class VaultTests(VaultFixture, unittest.TestCase):
    def test_privacy_properties_fail_closed_for_common_quoted_commented_and_unknown_values(self):
        for value in ('true', 'true # personal', '"true"', "'true' # private", '', '!custom false', '[false]'):
            with self.subTest(value=value):
                self.assertTrue(vault_files.properties('\ufeff---\njarvis_private: '+value+'\n---\nPrivate source')['private'])
        for value in ('false', 'false # explicitly shared', '"false"', 'no', '0'):
            with self.subTest(value=value):
                self.assertFalse(vault_files.properties('---\njarvis_exclude: '+value+'\n---\nShared source')['private'])

    def test_shared_scope_excludes_hidden_private_frontmatter_and_links(self):
        self.write('Knowledge/Plan.MD', '---\naliases:\n  - Launch day\ntags: [work, cafe]\n---\n# Plan\nCafé 🪐')
        for relative in ('.obsidian/plugin.md', '.trash/deleted.md', 'Private/Diary.md', 'Secrets/Keys.md', 'node_modules/readme.md'):
            self.write(relative, 'Do not index this.')
        self.write('Hidden.md', '---\njarvis_private: true\n---\nDo not index this.')
        outside = self.base / 'outside.md'
        outside.write_text('Outside source', encoding='utf-8')
        (self.vault / 'Alias.md').symlink_to(outside)
        (self.vault / 'Other').symlink_to(self.base, target_is_directory=True)
        notes, _, info = vault_files.read_notes(self.vault)
        self.assertEqual([note['_path'] for note in notes], ['Knowledge/Plan.MD'])
        self.assertEqual(notes[0]['aliases'], ['Launch day'])
        self.assertEqual(notes[0]['tags'], ['work', 'cafe'])
        self.assertEqual(info['note_count'], 1)
        self.assertNotIn('Outside source', repr(notes))

    def test_unchanged_content_not_reread_edit_rename_delete_detected(self):
        path = self.write('Plan.md', 'Original')
        notes, cache, first = vault_files.read_notes(self.vault)
        with patch.object(vault_files, 'read_file', side_effect=AssertionError('Unchanged file reread')):
            same, cache, info = vault_files.read_notes(self.vault, cache)
        self.assertEqual(first['fingerprint'], info['fingerprint'])
        self.assertEqual(same, notes)
        path.write_text('Modified', encoding='utf-8')
        changed, cache, info = vault_files.read_notes(self.vault, cache)
        self.assertEqual(changed[0]['text'], 'Modified')
        self.assertEqual(info['content_reads'], 1)
        path.rename(self.vault / 'Renamed.md')
        changed, cache, _ = vault_files.read_notes(self.vault, cache)
        self.assertEqual(changed[0]['_path'], 'Renamed.md')
        self.assertNotIn('Plan.md', cache)
        (self.vault / 'Renamed.md').unlink()
        changed, cache, _ = vault_files.read_notes(self.vault, cache)
        self.assertEqual(changed, [])
        self.assertEqual(cache, {})

    def test_descriptor_reads_refuse_parent_and_final_symlinks(self):
        self.write('Folder/Plan.md', 'Safe')
        (self.vault / 'Folder').rename(self.vault / 'Original')
        (self.vault / 'Folder').symlink_to(self.vault / 'Original', target_is_directory=True)
        for relative in ('Folder/Plan.md', '../outside.md', '/tmp/anything.md', '.obsidian/secret.md'):
            with self.subTest(relative=relative), self.assertRaises((OSError, vault_files.VaultError)):
                vault_files.read_file(self.vault, relative)

    def test_bounded_scan_fails_without_returning_a_partial_vault(self):
        self.write('A.md', 'a')
        self.write('B.md', 'b')
        with patch.object(vault_files, 'MAX_NOTES', 1), self.assertRaises(vault_files.VaultError):
            vault_files.read_notes(self.vault)

    def test_fifo_note_is_rejected_without_blocking(self):
        os.mkfifo(self.vault / 'Pipe.md')
        with self.assertRaises(vault_files.VaultError):
            vault_files.read_notes(self.vault)

    def test_unverifiable_long_or_incomplete_properties_are_excluded(self):
        self.write('Long.md', '---\njarvis_private: true\n' + 'x' * (vault_files.MAX_FRONTMATTER_CHARS + 1) + '\n---\nPrivate')
        self.write('Incomplete.md', '---\naliases: [Unfinished]\n')
        self.assertEqual(vault_files.read_notes(self.vault)[0], [])
        with patch.object(vault_files, 'MAX_VAULT_BYTES', 1), self.assertRaises(vault_files.VaultError):
            vault_files.read_notes(self.vault)

    def test_graph_resolves_folder_wikilinks_and_unique_aliases(self):
        self.write('Projects/Plan.md', '---\naliases: [Launch]\n---\n# Plan')
        self.write('Inbox.md', 'See [[Projects/Plan#Next|the project]] and [[Launch]].')
        notes = build.read_notes(self.vault)
        self.assertEqual(build.make_graph(notes)['links'], [{'source': 0, 'target': 1}])


class BrainTests(VaultFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.store = brain.BrainService(self.base / 'private', self.vault)
        self.addCleanup(self.store.close)

    def activate(self):
        self.store.configure(vault_path=self.vault)
        self.assertEqual(self.store.activate(), self.vault.resolve())
        return self.store.refresh(force=True)

    def test_default_metadata_is_read_only_and_unconfigured(self):
        value = self.store.status()
        self.assertFalse(value['active'])
        self.assertFalse(value['configured'])
        self.assertEqual(value['metrics']['model_calls_avoided'], 0)
        self.assertFalse(self.store.root.exists())
        self.assertIsNone(self.store.direct_answer('Search my notes for anything', []))

    def test_private_setup_no_overwrite_and_explicit_restart(self):
        target = self.base / 'Jarvis Brain'
        result = self.store.prepare_vault(target)
        self.assertEqual(result['model_calls'], 0)
        self.assertEqual(len(list(target.rglob('*.md'))), len(brain.STARTER_FILES))
        self.assertEqual(target.stat().st_mode & 0o777, 0o700)
        for path in target.rglob('*.md'):
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(brain.BrainError):
            self.store.prepare_vault(target)
        status = self.store.configure(vault_path=target)
        self.assertTrue(status['restart_required'])
        self.assertFalse(status['active'])
        self.assertEqual(self.store.settings_path.stat().st_mode & 0o777, 0o600)

    def test_fts_alias_tags_and_late_passage_have_bounded_context(self):
        self.write('Plan.md', '---\naliases: [Moonshot]\ntags: [launch]\n---\n' + 'unrelated text ' * 500 + '\nThe zebra launch starts Friday.\n')
        notes = self.activate()
        results = self.store.search('zebra', notes)
        self.assertIn('zebra launch', results[0]['text'])
        self.assertLessEqual(sum(len(note['text']) for note in results), brain.DEFAULT_CONTEXT_CHARS)
        self.assertEqual(self.store.search('Moonshot', notes)[0]['id'], 0)
        self.assertEqual(self.store.search('launch', notes)[0]['id'], 0)
        self.assertNotIn(str(self.vault), json.dumps(results))
        self.assertGreater(self.store.metrics['matching_full_note_chars'], self.store.metrics['selected_source_chars'])

    def test_incremental_index_deletion_privacy_edit_and_id_reassignment(self):
        self.write('Z.md', 'Unique zebra')
        notes = self.activate()
        self.assertEqual(self.store.search('zebra', notes)[0]['id'], 0)
        self.store.refresh(force=True)
        self.assertEqual(self.store.index.last_sync['changed'], 0)
        self.write('A.md', 'Another topic')
        notes = self.store.refresh(force=True)
        self.assertEqual(self.store.search('zebra', notes)[0]['id'], 1)
        self.write('Z.md', '---\njarvis_private: true\n---\nUnique zebra')
        notes = self.store.refresh(force=True)
        self.assertEqual(self.store.search('zebra', notes), [])
        self.assertEqual(self.store.index.last_sync['unchanged'], 1)
        (self.vault / 'A.md').unlink()
        notes = self.store.refresh(force=True)
        self.assertEqual(self.store.index.indexed, 0)
        self.assertEqual(self.store.search('another', notes), [])

    def test_direct_lookup_is_explicit_verbatim_and_no_reasoning_interception(self):
        self.write('Knowledge/Plan.md', '---\naliases: [Launch]\n---\nThe launch starts Friday.')
        notes = self.activate()
        result = self.store.direct_answer('Search my notes for launch', notes)
        self.assertEqual(result['local_memory']['model_calls'], 0)
        self.assertIn('The launch starts Friday.', result['answer'])
        self.assertTrue(result['brain_sources'][0]['uri'].startswith('obsidian://open?path='))
        self.assertEqual(self.store.direct_answer('Read note Launch', notes)['nodes'], [0])
        for query in ('When should I launch?', 'Find a good launch strategy', 'Remember this launch', 'Search the web for launch'):
            self.assertIsNone(self.store.direct_answer(query, notes))
        self.assertEqual(self.store.metrics['model_calls_avoided'], 2)

    def test_fts_query_metacharacters_are_data_and_invalid_budgets_refused(self):
        self.write('Plan.md', 'A zebra plan')
        notes = self.activate()
        self.store.search('zebra\" OR * NOT near(foo)', notes)
        for budget in (True, 0, 999999, '6000'):
            with self.assertRaises(brain.BrainError):
                self.store.configure(context_chars=budget)
        self.assertEqual(self.store.search('zebra', notes)[0]['title'], 'Plan')

    def test_connection_change_does_not_swap_an_active_task_scope(self):
        self.write('Plan.md', 'Shared source')
        self.activate()
        self.store.disconnect()
        self.assertEqual(self.store.active_path, self.vault.resolve())
        self.assertTrue(self.store.status()['restart_required'])
        self.assertEqual(self.store.settings()['vault_path'], None)

    def test_data_use_boundary_refuses_recent_private_and_stale_edits(self):
        path = self.write('Plan.md', 'The zebra starts Friday.')
        notes = self.activate()
        path.write_text('---\njarvis_private: true\n---\nThe zebra starts Friday.', encoding='utf-8')
        self.assertIsNone(self.store.refresh())  # Metadata polling can be throttled.
        self.assertEqual(self.store.search('zebra', notes), [])
        with self.assertRaises(brain.BrainError):
            self.store.direct_answer('Read note Plan', notes)
        path.write_text('The zebra starts Monday.', encoding='utf-8')
        with self.assertRaises(brain.BrainError):
            self.store.search('zebra', notes)

    def test_unknown_settings_and_symlink_index_are_refused(self):
        self.store.root.mkdir()
        self.store.settings_path.write_text(json.dumps({'vault_path': str(self.vault), 'password': 'fixture'}))
        with self.assertRaises(brain.BrainError):
            self.store.status()
        index = brain.BrainIndex(self.base / 'alias.sqlite3')
        (self.base / 'outside').write_text('untouched')
        index.path.symlink_to(self.base / 'outside')
        with self.assertRaises(brain.BrainError):
            index.sync([])
        self.assertEqual((self.base / 'outside').read_text(), 'untouched')

    def test_fifo_settings_refused_and_history_source_window_bounded(self):
        self.store.root.mkdir(mode=0o700)
        os.mkfifo(self.store.settings_path, mode=0o600)
        with self.assertRaises(brain.BrainError):
            self.store.status()
        history = [{'question': 'old', 'answer': 'x' * 6000}, {'question': 'latest', 'answer': 'y' * 6000}]
        bounded = self.store.bounded_history(history)
        self.assertEqual(bounded[0]['question'], 'latest')
        self.assertLessEqual(sum(len(item['question']) + len(item['answer']) for item in bounded), brain.HISTORY_CHARS)


if __name__ == '__main__':
    unittest.main()
