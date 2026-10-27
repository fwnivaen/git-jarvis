"""Same-origin decisions through actual private SQLite and temporary notes."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import note_store
from note_actions import NoteActions
import server
from test_offline import request

ORIGIN = {'Origin': 'http://127.0.0.1:4700'}


class LocalServicesHttpTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.vault = self.root / 'vault'
        self.vault.mkdir()
        self.file = self.vault / 'Plan.md'
        self.file.write_text('# Plan\nOriginal private note.')
        self.note = {'id': 0, 'group': 'test', '_path': 'Plan.md'}
        self.writes = []
        def read(identifier):
            if identifier != 0:
                raise ValueError
            return note_store.read(self.note, self.vault)
        def write(arguments, operation_session):
            self.writes.append(operation_session)
            return {'verified': True, 'note': note_store.save(self.note, self.vault,
                arguments['text'], arguments['revision'])}
        self.service = NoteActions(self.root/'private'/'actions.sqlite', read, write)
        self.addCleanup(self.service.close)
        patcher = patch.object(server, 'NOTE_ACTIONS', self.service)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.proposal = {'id': 0, 'text': '# Plan\nReviewed private note.',
            'revision': read(0)['revision'], 'request_key': 'a'*24}

    def post(self, path, body, headers=ORIGIN):
        status, _, data = request(path, 'POST', body, headers)
        return status, json.loads(data)

    def test_propose_review_approve_and_undo_are_real_and_revision_bound(self):
        status, action = self.post('/approvals/propose', self.proposal)
        self.assertEqual(status, 200)
        self.assertEqual(self.writes, [])
        status, _, raw = request('/approvals')
        summary = json.loads(raw)
        self.assertEqual(summary['pending_count'], 1)
        self.assertNotIn('private note', raw.decode())
        detail = json.loads(request('/approvals?id='+action['id'])[2])
        self.assertEqual(detail['after']['text'], self.proposal['text'])
        body = {'id': action['id'], 'fingerprint': action['fingerprint'], 'decision': 'approve'}
        status, applied = self.post('/approvals/decide', body)
        self.assertEqual(status, 200)
        self.assertTrue(applied['result']['verified'])
        self.assertEqual(self.file.read_text(), self.proposal['text'])
        self.assertEqual(self.post('/approvals/decide', body)[0], 409)
        self.assertEqual(len(self.writes), 1)
        status, undone = self.post('/approvals/undo', {key: body[key] for key in ('id','fingerprint')})
        self.assertEqual(status, 200)
        self.assertEqual(undone['approval']['status'], 'undone')
        self.assertEqual(self.file.read_text(), '# Plan\nOriginal private note.')

    def test_origin_extra_fields_and_mismatched_receipts_cannot_authorize_writes(self):
        for headers in ({}, {'Origin': 'https://outside.example'}):
            self.assertEqual(self.post('/approvals/propose', self.proposal, headers)[0], 403)
        for body in ([], dict(self.proposal, scope='another-project')):
            self.assertEqual(self.post('/approvals/propose', body)[0], 400)
        _, action = self.post('/approvals/propose', self.proposal)
        body = {'id': action['id'], 'fingerprint': 'f'*64, 'decision': 'approve'}
        self.assertEqual(self.post('/approvals/decide', body)[0], 409)
        self.assertEqual(self.writes, [])
        self.assertEqual(request('/approvals', headers={'Origin':'https://outside.example'})[0], 403)

    def test_canonical_status_exposes_only_pending_metadata_and_clears_rejected(self):
        _, action = self.post('/approvals/propose', self.proposal)
        with patch.object(server.TASK_API, 'catalog', return_value={'tasks': []}):
            value = json.loads(request('/stage2/tasks')[2])
            self.assertEqual(value['note_actions'], {'pending_count':1,
                'items':[{'id':action['id'],'status':'pending'}]})
            self.assertNotIn('private note', json.dumps(value))
            self.post('/approvals/decide', {'id':action['id'],
                'fingerprint':action['fingerprint'],'decision':'reject'})
            value = json.loads(request('/stage2/tasks')[2])
            self.assertEqual(value['note_actions'], {'pending_count':0,'items':[]})

    def test_health_is_read_only_and_available_without_model_or_note_access(self):
        value = {'schema_version':1, 'checks':[], 'first_run':{'complete':False,'guidance':[]}}
        with patch.object(server, 'readiness_snapshot', return_value=value) as snapshot, \
                patch.object(server, 'answer_request', side_effect=AssertionError('model called')):
            self.assertEqual(json.loads(request('/health')[2]), value)
            self.assertEqual(request('/health', 'HEAD')[2], b'')
            self.assertEqual(snapshot.call_count, 2)
            self.assertEqual(request('/health', headers={'Origin':'https://outside.example'})[0],403)
            self.assertEqual(snapshot.call_count, 2)

    def test_empty_catalog_does_not_create_a_store(self):
        with patch.object(server,'NOTE_ACTIONS',None), patch.object(server,'NOTE_ACTION_PATH',self.root/'absent'/'store.sqlite'):
            self.assertEqual(json.loads(request('/approvals')[2])['items'],[])
            self.assertFalse((self.root/'absent').exists())


if __name__ == '__main__':
    unittest.main()
