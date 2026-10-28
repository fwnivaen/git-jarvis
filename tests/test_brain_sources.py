"""Bind answer citations to supplied vault identities/revisions, using temporary files."""
import contextlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import build
import note_store
import obsidian_brain
import server


ORIGINAL = '# Zebra plan\n\nThe zebra launch starts Friday.\n'


class BrainSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='jarvis-brain-sources-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.vault = self.root / 'Vault'
        self.vault.mkdir()
        self.brain = obsidian_brain.BrainService(self.root / 'private', self.vault)
        self.addCleanup(self.brain.close)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(server, 'BRAIN', self.brain))
        self.stack.enter_context(patch.object(server, 'NOTES', []))
        self.stack.enter_context(patch.object(build, 'NOTES_DIR', self.vault.resolve()))
        writer = build.write_graph
        self.stack.enter_context(patch.object(build, 'write_graph',
            side_effect=lambda graph: writer(graph, self.root / 'graph.js')))
        for target in ('server.ask_brain', 'server.answer_request',
                       'server.urllib.request.urlopen', 'server.subprocess.Popen'):
            self.stack.enter_context(patch(target, side_effect=AssertionError('Live work forbidden')))

    def write(self, name, text):
        path = self.vault / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode('utf-8'))
        return path

    def activate(self):
        self.brain.configure(vault_path=self.vault)
        self.brain.activate()
        server.refresh_brain_notes(force=True)

    def supplied(self, query='zebra'):
        return [dict(note) for note in server.retrieve(query, [])]

    def test_unconfigured_binding_does_not_refresh_or_mutate_the_response(self):
        value = {'answer': 'A local response', 'nodes': [3], 'about_notes': True}
        before = dict(value)
        with patch.object(server, 'refresh_brain_notes', side_effect=AssertionError('Unconfigured refresh')):
            self.assertIs(server.bind_brain_sources(value, []), value)
        self.assertEqual(value, before)
        self.assertFalse(self.brain.root.exists())

    def test_graph_id_reassignment_remaps_the_supplied_identity_and_revision(self):
        self.write('Zebra.md', ORIGINAL)
        self.activate()
        supplied = self.supplied()
        self.assertEqual(supplied[0]['id'], 0)
        expected_stable, expected_revision = supplied[0]['stable_id'], supplied[0]['revision']
        self.write('Aardvark.md', '# Aardvark\nAn unrelated subject.')
        result = server.bind_brain_sources({'answer': 'Friday.', 'nodes': [0], 'about_notes': True}, supplied)
        self.assertEqual(result['nodes'], [1])
        self.assertFalse(result['sources_changed'])
        self.assertEqual(result['brain_sources'][0]['id'], 1)
        self.assertEqual(result['brain_sources'][0]['title'], 'Zebra')
        self.assertIn('Zebra.md', result['brain_sources'][0]['uri'])
        current = next(note for note in server.NOTES if note['_path'] == 'Zebra.md')
        self.assertEqual(note_store.stable_id(current['_path']), expected_stable)
        self.assertEqual(note_store.revision(current['text'], current['_path']), expected_revision)
        self.assertEqual(supplied[0]['id'], 0, 'Binding must not mutate the supplied snapshot.')
        self.assertNotIn('Aardvark', json.dumps(result))

    def test_changed_deleted_and_newly_private_sources_drop_links(self):
        path = self.write('Zebra.md', ORIGINAL)
        self.activate()
        for mutation in ('changed', 'deleted', 'private'):
            with self.subTest(mutation=mutation):
                self.write('Zebra.md', ORIGINAL)
                supplied = self.supplied()
                if mutation == 'changed':
                    path.write_bytes(ORIGINAL.replace('Friday', 'Monday').encode('utf-8'))
                elif mutation == 'deleted':
                    path.unlink()
                else:
                    path.write_bytes(('---\njarvis_private: true\n---\n' + ORIGINAL).encode('utf-8'))
                result = server.bind_brain_sources({'answer': 'The original answer.',
                    'nodes': [supplied[0]['id']], 'about_notes': True}, supplied)
                self.assertEqual(result['nodes'], [])
                self.assertEqual(result['brain_sources'], [])
                self.assertTrue(result['sources_changed'])
                self.assertTrue(result['error'])
                self.assertNotIn('The original answer.', result['answer'])
                self.assertIn('Please ask again', result['answer'])

    def test_source_not_actually_supplied_cannot_borrow_a_current_graph_reference(self):
        self.write('Aardvark.md', '# Aardvark\nAn unrelated subject.')
        self.write('Zebra.md', ORIGINAL)
        self.activate()
        supplied = self.supplied('aardvark')
        self.assertEqual([note['id'] for note in supplied], [0])
        result = server.bind_brain_sources({'answer': 'An unsupported citation.', 'nodes': [1]}, supplied)
        self.assertEqual(result['nodes'], [])
        self.assertEqual(result['brain_sources'], [])
        self.assertTrue(result['sources_changed'])

    def test_marked_mixed_revision_source_cannot_bind_to_its_latest_revision(self):
        self.write('Zebra.md', ORIGINAL)
        self.activate()
        old = self.supplied()[0]
        self.write('Zebra.md', ORIGINAL.replace('Friday', 'Monday'))
        latest = self.supplied()[0]
        self.assertNotEqual(old['revision'], latest['revision'])
        mixed = dict(latest, text=old['text'] + '\n' + latest['text'], _source_conflict=True)
        result = server.bind_brain_sources({'answer': 'A mixed answer.', 'nodes': [mixed['id']]}, [mixed])
        self.assertEqual(result['nodes'], [])
        self.assertEqual(result['brain_sources'], [])
        self.assertTrue(result['sources_changed'])

    def test_crlf_search_and_note_editor_share_exact_text_and_revision(self):
        text = '# Zebra plan\r\n\r\nThe zebra café launch starts Friday.\r\n'
        path = self.write('Zebra.md', text)
        self.activate()
        indexed = next(note for note in server.NOTES if note['_path'] == 'Zebra.md')
        self.assertEqual(indexed['text'], text)
        editor = note_store.read(indexed, self.vault)
        found = server.tool_notes_search('zebra')['notes']
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['text'], text)
        self.assertEqual(found[0]['stable_id'], editor['stable_id'])
        self.assertEqual(found[0]['revision'], editor['revision'])
        self.assertEqual(editor['text'], text)
        self.assertEqual(editor['revision'], note_store.revision(text, 'Zebra.md'))
        self.assertNotEqual(editor['revision'], note_store.revision(text.replace('\r\n', '\n'), 'Zebra.md'))
        result = server.bind_brain_sources({'answer': 'Friday.', 'nodes': [found[0]['id']]}, found)
        self.assertFalse(result['sources_changed'])
        self.assertEqual(result['nodes'], [indexed['id']])
        self.assertEqual(path.read_bytes(), text.encode('utf-8'))

    def test_duplicate_graph_ids_with_disagreeing_metadata_fail_closed_in_both_orders(self):
        self.write('Zebra.md', ORIGINAL)
        self.activate()
        old = self.supplied()[0]
        self.write('Zebra.md', ORIGINAL.replace('Friday', 'Monday'))
        latest = self.supplied()[0]
        different_identity = dict(latest, stable_id=note_store.stable_id('Unrelated.md'))
        for duplicate in (old, different_identity):
            for snapshot in ([duplicate, latest], [latest, duplicate]):
                with self.subTest(snapshot=snapshot):
                    result = server.bind_brain_sources({'answer': 'Ambiguous source.', 'nodes': [latest['id']]}, snapshot)
                    self.assertEqual(result['nodes'], [])
                    self.assertEqual(result['brain_sources'], [])
                    self.assertTrue(result['sources_changed'])

    def test_identical_duplicate_metadata_cannot_hide_a_conflict_marker(self):
        self.write('Zebra.md', ORIGINAL)
        self.activate()
        clean = self.supplied()[0]
        conflicted = dict(clean, _source_conflict=True)
        for snapshot in ([clean, conflicted], [conflicted, clean]):
            with self.subTest(snapshot=snapshot):
                result = server.bind_brain_sources({'answer': 'Conflicted source.', 'nodes': [clean['id']]}, snapshot)
                self.assertEqual(result['nodes'], [])
                self.assertEqual(result['brain_sources'], [])
                self.assertTrue(result['sources_changed'])
        result = server.bind_brain_sources({'answer': 'Same source.', 'nodes': [clean['id']]}, [clean, dict(clean)])
        self.assertEqual(result['nodes'], [clean['id']])
        self.assertEqual(len(result['brain_sources']), 1)
        self.assertFalse(result['sources_changed'])


if __name__ == '__main__':
    unittest.main()
