"""Activity projections are bounded metadata reads, without a second event log."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from activity_feed import ActivityFeed, ActivityError, MAX_EVENTS_PER_RUN, MAX_ITEMS
from execution_control import ControlStore

NOW = 1010.0
PRIVATE = 'private-question /Users/someone/config.json sk-example-secret-12345'


class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.run = {'id': 'a'*24, 'workflow': 'research', 'status': 'complete',
            'phase': 'complete', 'created_at': 1000, 'total_tokens': 42, 'usage_complete': True,
            'question': PRIVATE, 'agents': [{'output': PRIVATE}], 'result': {'answer': PRIVATE},
            'events': [
                {'id': 1, 'kind': 'started', 'elapsed_s': 0, 'payload': PRIVATE},
                {'id': 2, 'kind': 'agent_complete', 'elapsed_s': 2, 'arguments': PRIVATE},
                {'id': 3, 'kind': 'complete', 'elapsed_s': 3}]}
        self.action = {'id': 'b'*24, 'status': 'applied', 'created_at': 1001,
            'decided_at': 1002, 'applied_at': 1003, 'updated_at': 1003,
            'title': PRIVATE, 'before': {'text': PRIVATE}, 'fingerprint': PRIVATE}
        self.tool = {'run_id': 'c'*24, 'id': 1, 'kind': 'tool_started', 'at': 1003,
            'arguments': PRIVATE, 'process_id': 12345}

    def feed(self, **changes):
        args = dict(workflows=lambda: {'runs': [self.run]},
            approvals=lambda: {'items': [self.action]}, tool_events=lambda: [self.tool], clock=lambda: NOW)
        args.update(changes)
        return ActivityFeed(**args)

    def test_sources_merge_stably_without_copying_user_text_or_process_data(self):
        before = copy.deepcopy([self.run, self.action, self.tool])
        first = self.feed().snapshot()
        second = self.feed().snapshot()
        self.assertEqual(first, second)
        self.assertEqual(len(first['items']), 7)
        self.assertEqual(first['sources'], dict.fromkeys(('workflows', 'approvals', 'tools'), 'available'))
        self.assertEqual([item['at'] for item in first['items']],
            sorted([item['at'] for item in first['items']], reverse=True))
        self.assertEqual(len({item['id'] for item in first['items']}), 7)
        serialized = json.dumps(first)
        for value in (PRIVATE, '"question":', '"arguments":', '"process_id":', '"fingerprint":', '"before":'):
            self.assertNotIn(value, serialized)
        self.assertEqual([self.run, self.action, self.tool], before)
        self.assertEqual(first['items'][-1]['total_tokens'], 42)

    def test_limit_is_strict_and_window_truncation_is_honest(self):
        feed = self.feed()
        result = feed.snapshot(limit=2)
        self.assertEqual(len(result['items']), 2)
        self.assertTrue(result['truncated'])
        for limit in (0, -1, True, '2', MAX_ITEMS+1):
            with self.assertRaises(ActivityError):
                feed.snapshot(limit)
        self.run['events'] = [{'id': i+1, 'kind': 'usage', 'elapsed_s': 0}
            for i in range(MAX_EVENTS_PER_RUN+1)]
        result = self.feed(approvals=None, tool_events=None).snapshot(MAX_ITEMS)
        self.assertEqual(len(result['items']), MAX_EVENTS_PER_RUN)
        self.assertTrue(result['truncated'])

    def test_bad_provider_isolated_without_private_error_details(self):
        def failed():
            raise OSError(PRIVATE)
        result = self.feed(workflows=failed, tool_events=None).snapshot()
        self.assertEqual(result['sources']['workflows'], 'unavailable')
        self.assertEqual(result['sources']['tools'], 'not_configured')
        self.assertEqual(result['sources']['approvals'], 'available')
        self.assertEqual(len(result['items']), 3)
        self.assertNotIn(PRIVATE, json.dumps(result))

    def test_malformed_identifiers_kinds_and_future_events_are_not_published(self):
        self.run['id'] = '../../config.json'
        self.action['status'] = PRIVATE
        self.tool['kind'] = PRIVATE
        self.assertEqual(self.feed().snapshot()['items'], [])
        self.tool = {'run_id': 'c'*24, 'id': 1, 'kind': 'tool_started', 'at': NOW+1000}
        self.assertEqual(self.feed().snapshot()['items'], [])
        result = self.feed(workflows=lambda: {'tasks': 'not a list'}).snapshot()
        self.assertEqual(result['sources']['workflows'], 'unavailable')

    def test_existing_control_events_project_without_reading_operation_payloads(self):
        with tempfile.TemporaryDirectory() as folder:
            store = ControlStore(Path(folder)/'private'/'control.sqlite', clock=lambda: 1000)
            try:
                run, _ = store.admit('request', conversation_id='conversation', project_id='project',
                    engine='fixture', versions={'engine': 'v1', 'model': 'fixture', 'tools': 'v1', 'behavior': 'v1'},
                    permissions=['note_read'], duration_s=60)
                store.transition(run['id'], 'running')
                token = store.issue(run['id'])
                store.reserve(token, run['id'], 'read', 'note_read', {'secret': PRIVATE}, 'observation')
                store.settle(token, run['id'], 'read', {'text': PRIVATE})
                feed = ActivityFeed(tool_events=lambda: store.events(run['id']), clock=lambda: NOW)
                result = feed.snapshot()
                self.assertEqual([item['kind'] for item in sorted(result['items'], key=lambda item: item['id'])].count('tool_started'), 1)
                self.assertEqual(len(result['items']), 4)
                self.assertNotIn(PRIVATE, json.dumps(result))
            finally:
                store.close()

    def test_preference_approvals_have_fixed_distinct_labels_without_values(self):
        preference = dict(self.action, kind='preferences', key=PRIVATE,
            before={'preferences': {'tone': PRIVATE}}, after={'preferences': {'tone': PRIVATE}},
            status='undone', undone_at=1004)
        source = [self.action, preference]
        result = self.feed(workflows=None, tool_events=None,
            approvals=lambda: {'items': source}).snapshot()
        self.assertEqual(len(result['items']), 7, 'same opaque subject in two stores cannot collapse events')
        events = [item for item in result['items'] if item['kind'].startswith('preference_')]
        self.assertEqual({item['kind'] for item in events}, {'preference_proposed',
            'preference_approved', 'preference_applied', 'preference_undone'})
        self.assertTrue(all('preference' in item['label'].casefold() for item in events))
        self.assertTrue(any(item['label'] == 'Approved note edit saved' for item in result['items']))
        self.assertNotIn(PRIVATE, json.dumps(result))


if __name__ == '__main__':
    unittest.main()
