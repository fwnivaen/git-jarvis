"""Vault edit regressions: isolated files and HTTP fixtures, no real vault/brain/network."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build
import note_store
import server
from test_offline import request, TEST_PORT


class NoteFixture:

    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.vault = self.root / 'notes'
        self.group = self.vault / 'Team'
        self.group.mkdir(parents=True)
        self.path = self.group / 'Plan.md'
        self.original = '# Plan\n\nA real task.\n'
        self.path.write_text(self.original, encoding='utf-8')
        self.path.chmod(0o640)
        self.note = build.read_notes(self.vault)[0]
        self.outside = self.root / 'outside.md'
        self.outside.write_text('Outside content', encoding='utf-8')

    def assert_no_temporary_files(self):
        self.assertEqual(list(self.root.rglob('.jarvis-edit-*')), [])


class NoteStoreTests(NoteFixture, unittest.TestCase):
    def test_crlf_and_mixed_newlines_preserve_exact_revisions_and_saved_bytes(self):
        original = '# Plan\r\n\r\nA Windows note.\nLast line\r\n'
        self.path.write_bytes(original.encode('utf-8'))
        current = note_store.read(self.note, self.vault)
        self.assertEqual(current['text'], original)
        self.assertEqual(current['revision'], note_store.revision(original, self.note['_path']))
        replacement = original + 'Verified edit.\r\n'
        saved = note_store.save(self.note, self.vault, replacement, current['revision'])
        self.assertEqual(saved['text'], replacement)
        self.assertEqual(self.path.read_bytes(), replacement.encode('utf-8'))
        self.assertEqual(saved['revision'], note_store.revision(replacement, self.note['_path']))

    def test_read_and_durable_verified_unicode_roundtrip(self):
        current = note_store.read(self.note, self.vault)
        self.assertEqual(current['text'], self.original)
        self.assertEqual(current['revision'], note_store.revision(self.original, self.note['_path']))
        edited = '# Plan\n\n- [x] Café 東京 🪐\n\n[[Another Note]]\n'
        with patch.object(note_store.os, 'fsync', wraps=os.fsync) as synced:
            result = note_store.save(self.note, self.vault, edited, current['revision'])
        self.assertGreaterEqual(synced.call_count, 1)
        self.assertEqual(result, note_store.read(self.note, self.vault))
        self.assertEqual(self.path.read_bytes(), edited.encode('utf-8'))
        self.assertEqual(result['revision'], note_store.revision(edited, self.note['_path']))
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o640)
        self.assertGreater(result['edited_at'], 0)
        self.assert_no_temporary_files()

    def test_stale_revision_refuses_without_touching_file(self):
        current = note_store.read(self.note, self.vault)
        self.path.write_text('Manual edit', encoding='utf-8')
        for expected in (current['revision'], None, 3, ''):
            with self.subTest(expected=expected), self.assertRaises(note_store.NoteError):
                note_store.save(self.note, self.vault, 'Overwrite', expected)
            self.assertEqual(self.path.read_text(), 'Manual edit')
        self.assert_no_temporary_files()

    def test_identical_content_in_another_note_cannot_accept_a_stale_note_token(self):
        current = note_store.read(self.note, self.vault)
        other_path = self.group / 'Earlier.md'
        other_path.write_text(self.original, encoding='utf-8')
        other = next(note for note in build.read_notes(self.vault) if note['_path'] == 'Team/Earlier.md')
        self.assertEqual(other['id'], self.note['id'])  # A rebuild has reassigned the old integer ID.
        self.assertNotEqual(note_store.read(other, self.vault)['revision'], current['revision'])
        with self.assertRaises(note_store.NoteError):
            note_store.save(other, self.vault, 'This edit belongs to Plan.', current['revision'])
        self.assertEqual(other_path.read_text(), self.original)
        self.assertEqual(self.path.read_text(), self.original)
        self.assert_no_temporary_files()

    def test_external_edit_while_preparing_replacement_is_not_overwritten(self):
        current = note_store.read(self.note, self.vault)
        real_sync = os.fsync
        def concurrent_edit(fd):
            real_sync(fd)
            self.path.write_text('Concurrent manual edit', encoding='utf-8')
        with patch.object(note_store.os, 'fsync', side_effect=concurrent_edit):
            with self.assertRaises(note_store.NoteError):
                note_store.save(self.note, self.vault, 'Old editor content', current['revision'])
        self.assertEqual(self.path.read_text(), 'Concurrent manual edit')
        self.assert_no_temporary_files()

    def test_outside_traversal_absolute_and_derived_paths_are_refused(self):
        for relative in ('../outside.md', str(self.outside), 'Team/../../outside.md'):
            note = dict(self.note, _path=relative)
            with self.subTest(path=relative), self.assertRaises(note_store.NoteError):
                note_store.read(note, self.vault)
            with self.assertRaises(note_store.NoteError):
                note_store.save(note, self.vault, 'Overwrite', note_store.revision('Outside content'))
        for note in (dict(self.note, capture_hub=True), {k:v for k,v in self.note.items() if k != '_path'}, dict(self.note, _path=None)):
            with self.assertRaises(note_store.NoteError):
                note_store.read(note, self.vault)
        self.assertEqual(self.outside.read_text(), 'Outside content')

    def test_file_and_parent_symlinks_are_refused_even_when_the_target_is_inside(self):
        for target in (self.path, self.outside):
            link = self.vault / 'Alias.md'
            link.symlink_to(target)
            with self.subTest(target=str(target)), self.assertRaises(note_store.NoteError):
                note_store.read(dict(self.note, _path='Alias.md'), self.vault)
            link.unlink()
        for target in (self.group, self.root):
            alias = self.vault / 'Alias'
            alias.symlink_to(target, target_is_directory=True)
            relative = 'Alias/Plan.md' if target == self.group else 'Alias/outside.md'
            with self.subTest(target=str(target)), self.assertRaises(note_store.NoteError):
                note_store.read(dict(self.note, _path=relative), self.vault)
            alias.unlink()
        self.assertEqual(self.path.read_text(), self.original)
        self.assertEqual(self.outside.read_text(), 'Outside content')

    def test_parent_symlink_swap_cannot_replace_an_outside_file(self):
        current = note_store.read(self.note, self.vault)
        external = self.root / 'external'
        external.mkdir()
        outside_note = external / self.path.name
        outside_note.write_text(self.original, encoding='utf-8')
        real_temporary = note_store.tempfile.NamedTemporaryFile
        def swap_parent(*args, **kwargs):
            self.group.rename(self.vault / 'OriginalTeam')
            self.group.symlink_to(external, target_is_directory=True)
            return real_temporary(*args, **kwargs)
        with patch.object(note_store.tempfile, 'NamedTemporaryFile', side_effect=swap_parent):
            with self.assertRaises(note_store.NoteError):
                note_store.save(self.note, self.vault, 'Edited private text', current['revision'])
        self.assertEqual(outside_note.read_text(), self.original)
        self.assert_no_temporary_files()

    def test_unicode_character_and_byte_limits_are_consistent(self):
        current = note_store.read(self.note, self.vault)
        maximum = '😀' * note_store.MAX_NOTE_CHARS
        self.assertEqual(len(maximum.encode('utf-8')), note_store.MAX_NOTE_BYTES)
        result = note_store.save(self.note, self.vault, maximum, current['revision'])
        self.assertEqual(result['text'], maximum)
        for invalid in (maximum + 'x', 'a' * (note_store.MAX_NOTE_CHARS + 1), None, 123):
            with self.subTest(kind=type(invalid).__name__), self.assertRaises(note_store.NoteError):
                note_store.save(self.note, self.vault, invalid, result['revision'])
        self.assertEqual(self.path.read_text(), maximum)
        self.assert_no_temporary_files()

    def test_read_refuses_oversize_file(self):
        self.path.write_bytes(b'x' * (note_store.MAX_NOTE_BYTES + 1))
        with self.assertRaises(note_store.NoteError):
            note_store.read(self.note, self.vault)

    def test_failed_replace_preserves_original_and_cleans_prepared_file(self):
        current = note_store.read(self.note, self.vault)
        with patch.object(note_store.os, 'replace', side_effect=OSError('Disk unavailable')):
            with self.assertRaises(OSError):
                note_store.save(self.note, self.vault, 'Replacement', current['revision'])
        self.assertEqual(self.path.read_text(), self.original)
        self.assert_no_temporary_files()


class NoteRouteTests(NoteFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.graph_path = self.root / 'graph.js'
        self.origin = {'Origin': f'http://127.0.0.1:{TEST_PORT}'}
        for target, name, value in ((build, 'NOTES_DIR', self.vault), (server, 'NOTES', [dict(self.note)])):
            patched = patch.object(target, name, value)
            patched.start()
            self.addCleanup(patched.stop)
        writer = build.write_graph
        patched = patch.object(build, 'write_graph', side_effect=lambda graph: writer(graph, self.graph_path))
        patched.start()
        self.addCleanup(patched.stop)
        for target in ('server.ask_brain', 'server.urllib.request.urlopen', 'server.subprocess.Popen'):
            patched = patch(target, side_effect=AssertionError('Real brain/network forbidden'))
            patched.start()
            self.addCleanup(patched.stop)

    def body(self, text='Edited note'):
        return {'id': self.note['id'], 'text': text,
                'revision': note_store.read(self.note, self.vault)['revision'], 'approved': True}

    def test_route_reads_and_exposes_no_server_path(self):
        status, _, raw = request(f'/note?id={self.note["id"]}')
        self.assertEqual(status, 200)
        result = json.loads(raw)
        self.assertEqual(result['text'], self.original)
        self.assertNotIn('_path', result)
        self.assertNotIn(str(self.root), raw.decode())
        for identifier in ('-1', 'x', '999'):
            self.assertEqual(request('/note?id=' + identifier)[0], 404)

    def test_route_requires_true_approval_and_same_origin_before_writing(self):
        body = self.body()
        denied = [(body, {}), (body, {'Origin': 'https://elsewhere.example'}),
                  (dict(body, approved=False), self.origin), (dict(body, approved=1), self.origin),
                  ({key:value for key,value in body.items() if key != 'approved'}, self.origin)]
        for payload, headers in denied:
            with self.subTest(payload=payload, headers=headers):
                status, _, _ = request('/note', 'POST', payload, headers)
                self.assertEqual(status, 403)
                self.assertEqual(self.path.read_text(), self.original)
                self.assertFalse(self.graph_path.exists())

    def test_route_approved_edit_updates_retrieval_and_graph_after_verified_save(self):
        body = self.body('# Plan\n\nNew detail with [[Another Note]].')
        status, _, raw = request('/note', 'POST', body, self.origin)
        self.assertEqual(status, 200)
        result = json.loads(raw)
        self.assertIs(result['verified'], True)
        self.assertEqual(result['note']['text'], self.path.read_text())
        self.assertEqual(result['note']['revision'], note_store.revision(body['text'], self.note['_path']))
        self.assertEqual(server.NOTES[0]['text'], body['text'])
        self.assertEqual(result['graph'], build.make_graph(server.NOTES))
        self.assertEqual(self.graph_path.read_bytes(), build.graph_bytes(result['graph']))
        self.assertNotIn('_path', result['graph']['nodes'][0])

    def test_route_conflict_and_client_path_cannot_write_elsewhere(self):
        body = self.body()
        self.path.write_text('Independent edit', encoding='utf-8')
        status, _, raw = request('/note', 'POST', dict(body, path=str(self.outside), _path='../outside.md'), self.origin)
        self.assertEqual(status, 409)
        self.assertIs(json.loads(raw)['error'], True)
        self.assertEqual(self.path.read_text(), 'Independent edit')
        self.assertEqual(self.outside.read_text(), 'Outside content')
        self.assertFalse(self.graph_path.exists())
        for identifier in (True, '0', None):
            self.assertEqual(request('/note', 'POST', dict(body, id=identifier), self.origin)[0], 400)

    def test_stale_tab_approval_cannot_edit_a_reassigned_id_with_identical_content(self):
        approved = self.body('An edit approved for Plan.')
        other_path = self.group / 'Earlier.md'
        other_path.write_text(self.original, encoding='utf-8')
        server.NOTES[:] = build.read_notes(self.vault)
        self.assertEqual(server.NOTES[0]['_path'], 'Team/Earlier.md')
        status, _, raw = request('/note', 'POST', approved, self.origin)
        self.assertEqual(status, 409)
        self.assertIs(json.loads(raw)['error'], True)
        self.assertEqual(other_path.read_text(), self.original)
        self.assertEqual(self.path.read_text(), self.original)
        self.assertFalse(self.graph_path.exists())

    def test_route_accepts_maximum_valid_unicode_text_with_json_envelope(self):
        maximum = '😀' * note_store.MAX_NOTE_CHARS
        status, _, raw = request('/note', 'POST', self.body(maximum), self.origin)
        self.assertEqual(status, 200, raw[:160])
        self.assertEqual(self.path.read_text(), maximum)
        self.assertIs(json.loads(raw)['verified'], True)

    def test_capture_heading_title_matches_the_next_rebuild_immediately(self):
        captures = self.vault / 'captures'
        captures.mkdir()
        path = captures / 'capture-001.md'
        path.write_text('# Before title\n\nBody', encoding='utf-8')
        capture = next(note for note in build.read_notes(self.vault) if note['group'] == 'captures')
        capture['id'] = len(server.NOTES)
        server.NOTES.append(capture)
        current = note_store.read(capture, self.vault)
        body = dict(id=capture['id'], text='# After title\n\nBody', revision=current['revision'], approved=True)
        status, _, raw = request('/note', 'POST', body, self.origin)
        self.assertEqual(status, 200)
        result = json.loads(raw)
        rebuilt = next(note for note in build.read_notes(self.vault) if note['group'] == 'captures')
        self.assertEqual(result['note']['title'], rebuilt['label'])
        self.assertEqual(next(note for note in server.NOTES if note['id'] == capture['id'])['label'], rebuilt['label'])
        self.assertEqual(next(note for note in result['graph']['nodes'] if note['id'] == capture['id'])['label'], rebuilt['label'])
