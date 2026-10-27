"""Canonical task projections and genuine no-execution recipe previews."""
from dataclasses import replace
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock

import ultra
from task_api import TaskAPI
from ultra_tools import Registry
from ultra_workflows import workflow_template
from workflow_runtime import (WorkflowService, WorkflowError, Redactor, READ_TOOLS,
    WORKER_COUNTS, MAX_WORKERS, MAX_TOOL_CALLS, MAX_RESTARTS)

RUN_ID = 'a' * 24


class TaskPlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)/'must-not-be-created'
        self.settings = replace(ultra.Settings(), enabled=True)
        self.callbacks = {name: Mock(side_effect=AssertionError('Preview called a tool.')) for name in READ_TOOLS}
        self.registry = Registry(lambda: self.settings, self.callbacks)
        self.service = WorkflowService(lambda: self.settings, self.registry,
            Mock(side_effect=AssertionError('Preview created a model bridge.')),
            threading.BoundedSemaphore(2), 'Test', root=self.root, max_concurrent=2,
            redactor=Redactor(['private-marker']))
        self.service.initialize = Mock(side_effect=AssertionError('Preview initialized storage.'))
        self.service.start = Mock(side_effect=AssertionError('Preview admitted work.'))
        self.api = TaskAPI(self.service)

    def test_preview_uses_actual_role_counts_and_criteria_without_execution(self):
        for name, count in WORKER_COUNTS.items():
            with self.subTest(workflow=name):
                plan = self.api.plan('  Compare the supplied notes.  ', name)
                recipe = workflow_template(workflow=name)
                self.assertTrue(plan['dry_run'])
                self.assertFalse(plan['admitted'])
                self.assertEqual(plan['question'], 'Compare the supplied notes.')
                self.assertEqual(plan['roles'], recipe['roles'][:min(count, self.settings.max_workers, MAX_WORKERS)])
                self.assertEqual(plan['criteria'], recipe['criteria'])
                self.assertEqual(set(plan['permitted_tools']), READ_TOOLS)
                self.assertFalse(any(plan['side_effects']['preview'].values()))
                self.assertTrue(plan['side_effects']['execution']['private_checkpoints'])
                self.assertTrue(plan['checkpoint_storage']['contains_source_passages'])
        self.assertFalse(self.root.exists())
        self.assertIsNone(self.service.store)
        self.assertEqual(self.service.active_ids, [])
        self.service.initialize.assert_not_called()
        self.service.start.assert_not_called()
        self.service.bridge_factory.assert_not_called()
        for callback in self.callbacks.values():
            callback.assert_not_called()

    def test_preview_respects_configured_workers_budgets_and_disabled_features(self):
        self.settings = replace(self.settings, max_workers=1, worker_retries=1,
            request_timeout_s=120, worker_timeout_s=30)
        plan = self.api.plan('Analyze this.', 'research')
        self.assertEqual(len(plan['roles']), 1)
        self.assertEqual(plan['limits']['workers'], 1)
        self.assertEqual(plan['limits']['concurrent_tasks'], 2)
        self.assertEqual(plan['limits']['tool_calls_per_task'], MAX_TOOL_CALLS)
        self.assertEqual(plan['limits']['max_restarts'], MAX_RESTARTS)
        self.assertEqual(plan['limits']['request_timeout_s'], 120)
        self.assertEqual(plan['limits']['worker_timeout_s'], 30)
        self.assertEqual(plan['limits']['worker_retries'], 1)
        self.assertIsNone(plan['limits']['model_calls_per_task'])
        self.assertFalse(plan['limits']['adaptive_model_budget_enforced'])
        self.settings = replace(self.settings, adaptive_routing_enabled=True, model_call_limit=4,
            observed_token_limit=4000)
        adaptive = self.api.plan('Analyze this.', 'general')
        self.assertEqual(adaptive['limits']['model_calls_per_task'], 4)
        self.assertEqual(adaptive['limits']['observed_tokens_per_task'], 4000)
        self.assertTrue(adaptive['steps'][-1]['model_review_may_be_skipped'])
        self.settings = replace(self.settings, tools_enabled=False)
        disabled = self.api.plan('Analyze this.')
        self.assertFalse(disabled['execution_enabled'])
        self.assertEqual(disabled['permitted_tools'], [])
        self.assertFalse(disabled['prerequisites']['notes_search_registered'])
        self.assertFalse(self.root.exists())

    def test_unknown_tool_and_unavailable_optional_tools_are_never_offered(self):
        self.callbacks.pop('preferences_get')
        self.callbacks['notes_capture'] = Mock(side_effect=AssertionError('No writes.'))
        plan = self.api.plan('Read notes.')
        self.assertEqual(plan['permitted_tools'], ['note_read', 'notes_search'])
        self.assertFalse(plan['capabilities']['approve'])
        self.assertFalse(plan['capabilities']['execute_preview'])

    def test_invalid_input_is_rejected_before_settings_or_storage(self):
        self.service.settings = Mock(side_effect=AssertionError('Invalid plan read settings.'))
        for question, workflow in (('', 'research'), ('  ', 'research'), ([], 'research'),
                ('a' * 2001, 'general'), ('Valid', 'unknown'), ('Valid', []), ('Valid', True)):
            with self.subTest(question_type=type(question), workflow=workflow), self.assertRaises(WorkflowError):
                self.api.plan(question, workflow)
        self.service.settings.assert_not_called()
        self.assertFalse(self.root.exists())

    def test_question_redaction_and_preview_mutation_cannot_change_recipe(self):
        plan = self.api.plan('Review private-marker', 'research')
        self.assertNotIn('private-marker', plan['question'])
        original = workflow_template(workflow='research')
        plan['roles'][0]['assignment'] = 'Changed by client'
        plan['criteria'].clear()
        self.assertEqual(workflow_template(workflow='research'), original)


class TaskProjectionTests(unittest.TestCase):
    def setUp(self):
        self.run = {'id': RUN_ID, 'workflow': 'research', 'question': 'Public question',
            'status': 'running', 'phase': 'workers', 'cleanup': 'none', 'can_retry': False,
            'events': [{'id': 1, 'kind': 'started'}], 'result': None,
            'raw_private_state': 'Must not escape'}
        self.source = {'enabled': True, 'available': True, 'engine': 'LangGraph',
            'active_ids': [RUN_ID], 'limits': {'concurrent_runs': 2},
            'workflows': [{'id': 'research', 'label': 'Evidence research'}], 'runs': [self.run]}
        self.service = Mock()
        self.service.catalog.return_value = self.source
        self.service.detail.return_value = self.run
        self.service.settled.return_value = False
        self.api = TaskAPI(self.service)

    def test_catalog_uses_stable_existing_identity_and_actual_capabilities(self):
        catalog = self.api.catalog()
        self.assertEqual(catalog['active_ids'], [RUN_ID])
        self.assertEqual(catalog['limits'], {'concurrent_runs': 2})
        task = catalog['tasks'][0]
        self.assertEqual(task['id'], RUN_ID)
        self.assertEqual(task['status'], 'running')
        self.assertTrue(task['capabilities']['cancel'])
        self.assertFalse(task['capabilities']['resume'])
        self.assertFalse(task['capabilities']['approve'])
        self.assertNotIn('raw_private_state', task)
        self.assertFalse(task['settled'])
        self.service.start.assert_not_called()
        self.service.detail.assert_not_called()
        self.service.settled.assert_not_called()

    def test_detail_preserves_result_and_recovery_availability(self):
        self.run.update(status='failed', phase='review', can_retry=True,
            result={'answer': 'Verified output', 'nodes': [1]})
        self.service.settled.return_value = True
        task = self.api.detail(RUN_ID)
        self.assertEqual(task['result'], self.run['result'])
        self.assertTrue(task['capabilities']['resume'])
        self.assertFalse(task['capabilities']['cancel'])
        self.assertTrue(task['settled'])
        self.service.detail.assert_called_once_with(RUN_ID)
        task['result']['answer'] = 'Client change'
        self.assertEqual(self.run['result']['answer'], 'Verified output')

    def test_pending_cleanup_is_not_claimed_settled_or_cancellable(self):
        self.run.update(status='cancelled', cleanup='pending')
        task = self.api.detail(RUN_ID)
        self.assertFalse(task['settled'])
        self.assertFalse(task['capabilities']['cancel'])
        self.assertFalse(task['capabilities']['resume'])

    def test_unknown_detail_is_delegated_without_fabricating_task(self):
        self.service.detail.side_effect = WorkflowError('Unknown workflow run.', 404)
        with self.assertRaises(WorkflowError) as error:
            self.api.detail('unknown')
        self.assertEqual(error.exception.status, 404)
        self.service.settled.assert_not_called()


if __name__ == '__main__':
    unittest.main()
