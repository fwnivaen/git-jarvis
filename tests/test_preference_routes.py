"""Real private preference approvals through offline HTTP; no live model or state."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import preference_actions
import server
import ultra_memory
from test_offline import request

ORIGIN = {'Origin': 'http://127.0.0.1:4700'}


class PreferenceRouteTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.preferences = ultra_memory.Preferences(self.root / 'private' / 'preferences.json')
        for name, value in (('PREFERENCES', self.preferences), ('PREFERENCE_ACTIONS', None),
                ('PREFERENCE_ACTION_PATH', self.root / 'reviews' / 'control.sqlite3')):
            change = patch.object(server, name, value)
            change.start()
            self.addCleanup(change.stop)
        self.addCleanup(lambda: server.PREFERENCE_ACTIONS.close() if server.PREFERENCE_ACTIONS else None)

    def post(self, path, body, headers=ORIGIN):
        status, _, raw = request(path, 'POST', body, headers)
        return status, json.loads(raw)

    def proposal(self):
        revision = json.loads(request('/preferences')[2])['revision']
        return {'operation': 'set', 'key': 'writing style', 'value': 'Brief and clear',
            'revision': revision, 'request_key': 'a'*24}

    def test_reads_are_lazy_and_proposal_does_not_write_preference(self):
        self.assertEqual(json.loads(request('/preferences')[2])['preferences'], {})
        self.assertEqual(json.loads(request('/preference-actions')[2])['pending_count'], 0)
        self.assertFalse(server.PREFERENCE_ACTION_PATH.exists())
        self.assertFalse(self.preferences.path.exists())
        status, action = self.post('/preference-actions/propose', self.proposal())
        self.assertEqual(status, 200)
        self.assertEqual(action['kind'], 'preferences')
        self.assertFalse(self.preferences.path.exists())
        self.assertEqual(action['before']['preferences'], {})
        self.assertEqual(action['after']['preferences'], {'writing style': 'Brief and clear'})
        summary = request('/preference-actions')[2].decode()
        self.assertNotIn('Brief and clear', summary)

    def test_exact_approval_replay_and_undo_roundtrip(self):
        _, action = self.post('/preference-actions/propose', self.proposal())
        detail = json.loads(request('/preference-actions?id='+action['id'])[2])
        self.assertEqual(detail['fingerprint'], action['fingerprint'])
        body = {'id': action['id'], 'fingerprint': action['fingerprint'], 'decision': 'approve'}
        status, applied = self.post('/preference-actions/decide', body)
        self.assertEqual(status, 200)
        self.assertTrue(applied['result']['verified'])
        self.assertEqual(self.preferences.read(), {'writing style': 'Brief and clear'})
        self.assertEqual(self.post('/preference-actions/decide', body)[0], 409)
        status, undone = self.post('/preference-actions/undo', {k:body[k] for k in ('id', 'fingerprint')})
        self.assertEqual(status, 200)
        self.assertEqual(undone['approval']['status'], 'undone')
        self.assertEqual(self.preferences.read(), {})

    def test_origin_fields_stale_revision_and_credentials_fail_closed(self):
        proposal = self.proposal()
        for headers in ({}, {'Origin':'https://outside.example'}):
            self.assertEqual(self.post('/preference-actions/propose', proposal, headers)[0], 403)
        self.assertFalse(server.PREFERENCE_ACTION_PATH.exists())
        self.assertEqual(self.post('/preference-actions/propose', dict(proposal, scope='outside'))[0], 400)
        self.assertEqual(self.post('/preference-actions/propose', dict(proposal, value='password=do-not-save'))[0], 400)
        self.preferences.set('other', 'An external explicit preference')
        self.assertEqual(self.post('/preference-actions/propose', proposal)[0], 409)
        self.assertEqual(self.preferences.read(), {'other':'An external explicit preference'})

    def test_model_callback_queues_review_using_current_vault_store(self):
        result = server.propose_preference('tone', 'Direct')
        self.assertFalse(result['saved'])
        self.assertTrue(result['approval_required'])
        self.assertEqual(self.preferences.read(), {})
        self.assertEqual(result['approval']['after']['preferences'], {'tone':'Direct'})
        with patch.object(server.TASK_API, 'catalog', return_value={'tasks': []}), patch.object(server, 'note_action_catalog', return_value={'items':[], 'pending_count':0}):
            catalog = json.loads(request('/stage2/tasks')[2])['action_approvals']
            self.assertEqual(catalog['pending_count'], 1)
            self.assertEqual(catalog['items'][0]['kind'], 'preferences')
            self.assertNotIn('Direct', json.dumps(catalog))


if __name__ == '__main__':
    unittest.main()
