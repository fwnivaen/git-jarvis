"""Task erasure is an explicit local action bound to one reviewed task state."""
import json
import unittest
from unittest.mock import patch

import server
from test_offline import request
from workflow_runtime import WorkflowError

ORIGIN = {'Origin': 'http://127.0.0.1:4700'}
RUN_ID = 'a' * 24
REVISION = 'b' * 64


class TaskPrivacyHttpTests(unittest.TestCase):
    def setUp(self):
        self.catalog = patch.object(server.WORKFLOWS, 'retention_catalog', create=True,
            return_value={'schema_version': 1, 'items': []}).start()
        self.plan = patch.object(server.WORKFLOWS, 'erase_plan', create=True,
            return_value={'schema_version': 1, 'id': RUN_ID, 'revision': REVISION}).start()
        self.erase = patch.object(server.WORKFLOWS, 'erase', create=True,
            return_value={'schema_version': 1, 'id': RUN_ID, 'state': 'erased', 'erased': True}).start()
        self.addCleanup(patch.stopall)

    def post(self, path, body, headers=ORIGIN):
        status, _, raw = request(path, 'POST', body, headers)
        return status, json.loads(raw)

    def test_read_and_head_never_plan_or_erase(self):
        self.assertEqual(json.loads(request('/workflows/retention')[2])['items'], [])
        self.assertEqual(request('/workflows/retention', 'HEAD')[2], b'')
        self.assertEqual(self.catalog.call_count, 2)
        self.plan.assert_not_called()
        self.erase.assert_not_called()
        self.assertEqual(request('/workflows/retention', headers={'Origin': 'https://outside.example'})[0], 403)
        self.assertEqual(self.catalog.call_count, 2)

    def test_preview_and_confirm_are_distinct_exact_calls(self):
        self.assertEqual(self.post('/workflows/erase-plan', {'id': RUN_ID})[0], 200)
        self.plan.assert_called_once_with(RUN_ID)
        self.erase.assert_not_called()
        self.assertEqual(self.post('/workflows/erase', {'id': RUN_ID,
            'expected_revision': REVISION, 'confirmed': True})[0], 200)
        self.erase.assert_called_once_with(RUN_ID, REVISION, True)

    def test_both_posts_require_explicit_same_origin(self):
        for path, body in (('/workflows/erase-plan', {'id': RUN_ID}),
                ('/workflows/erase', {'id': RUN_ID, 'expected_revision': REVISION, 'confirmed': True})):
            for headers in ({}, {'Origin': 'https://outside.example'}, {'Origin': 'null'}):
                self.assertEqual(self.post(path, body, headers)[0], 403)
        self.plan.assert_not_called()
        self.erase.assert_not_called()

    def test_unknown_scope_bad_ids_and_unconfirmed_erasure_cannot_reach_store(self):
        for body in ([], {}, {'id': '../outside'}, {'id': RUN_ID, 'execute': True},
                {'id': 1}, {'id': RUN_ID, 'path': '/tmp/other'}):
            self.assertEqual(self.post('/workflows/erase-plan', body)[0], 400)
        valid = {'id': RUN_ID, 'expected_revision': REVISION, 'confirmed': True}
        for body in ([], {'id': RUN_ID}, dict(valid, confirmed=1), dict(valid, confirmed=False),
                dict(valid, confirmed='true'), dict(valid, expected_revision='stale'),
                dict(valid, expected_revision=None), dict(valid, scope='all'), dict(valid, id='../outside')):
            self.assertEqual(self.post('/workflows/erase', body)[0], 400)
        self.plan.assert_not_called()
        self.erase.assert_not_called()

    def test_conflicts_pending_cleanup_and_missing_store_are_reported_honestly(self):
        body = {'id': RUN_ID, 'expected_revision': REVISION, 'confirmed': True}
        self.erase.side_effect = WorkflowError('The saved task changed after review.', 409)
        self.assertEqual(self.post('/workflows/erase', body)[0], 409)
        self.erase.side_effect = None
        self.erase.return_value = {'state': 'pending', 'erased': False, 'retryable': True}
        self.assertEqual(self.post('/workflows/erase', body)[0], 202)
        self.erase.side_effect = OSError('private path must not escape')
        status, value = self.post('/workflows/erase', body)
        self.assertEqual(status, 503)
        self.assertNotIn('private path', json.dumps(value))
        self.catalog.side_effect = WorkflowError('Privacy status is unavailable.', 503)
        self.assertEqual(request('/workflows/retention')[0], 503)


if __name__ == '__main__':
    unittest.main()
