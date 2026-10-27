import unittest
from unittest.mock import Mock, patch
import json

import server
import task_status
from test_offline import request


class TaskStatusTests(unittest.TestCase):
    def test_unrelated_question_does_not_read_tasks(self):
        api = Mock()
        self.assertIsNone(task_status.answer('What tasks are mentioned in my notes?', api))
        api.catalog.assert_not_called()

    def test_status_counts_actual_owners_including_cleanup(self):
        api = Mock()
        api.catalog.return_value = {'enabled': True, 'available': True, 'active_ids': ['a', 'b'],
            'tasks': [{'id': 'a', 'status': 'running'}, {'id': 'b', 'status': 'failed'},
                {'id': 'c', 'status': 'complete'}]}
        result = task_status.answer('Jarvis, what are you working on?', api)
        self.assertIn('1 background workflow is running', result['answer'].lower())
        self.assertIn('1 workflow is finishing cleanup', result['answer'])
        self.assertFalse(result['about_notes'])

    def test_disabled_unavailable_and_idle_are_distinct(self):
        api = Mock()
        for enabled, available, expected in ((False, False, 'disabled'),
                (True, False, "can't read"), (True, True, 'No background workflows')):
            api.catalog.return_value = {'enabled': enabled, 'available': available, 'active_ids': [], 'tasks': []}
            self.assertIn(expected, task_status.answer('What tasks are running?', api)['answer'])

    def test_voice_chat_status_uses_no_model_or_private_config(self):
        with patch.object(server.TASK_API, 'catalog', return_value={
                'enabled': True, 'available': True, 'active_ids': [], 'tasks': []}), \
                patch.object(server, 'load_config', side_effect=AssertionError('read private config')), \
                patch.object(server, 'answer_request', side_effect=AssertionError('used model')):
            status, _, body = request('/chat', 'POST', {'question': 'What are you working on?'})
            self.assertEqual(status, 200)
            self.assertIn('No background workflows', json.loads(body)['answer'])


if __name__ == '__main__':
    unittest.main()
