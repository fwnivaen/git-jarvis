"""Real LangGraph/checkpoint tests with a deterministic model boundary (zero spending)."""
import json
import queue
import sqlite3
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

import ultra
from codex_bridge import BridgeError, CodexBridge
from ultra_tools import Registry
from workflow_runtime import WorkflowService, WorkflowError, Redactor, RunStore
from task_limits import read_task_limit, parse_task_limit, DEFAULT_TASK_MAX_CONCURRENT
from execution_adapters import LangGraphAdapter

try:
    import langgraph.checkpoint.sqlite
    AVAILABLE = True
except ImportError:
    AVAILABLE = False

TIMEOUT_S = 8
POLL_S = .01
CLEANUP_RETRY_MAX_S = .04
CATALOG_RESPONSE_S = .5
NOTE = {'id': 1, 'title': 'Test budget', 'text': 'The budget is 100 dollars.', 'revision': 'test-revision'}


class TaskLimitTests(unittest.TestCase):
    def test_default_and_explicit_configuration(self):
        self.assertEqual(read_task_limit({}), (DEFAULT_TASK_MAX_CONCURRENT, None))
        self.assertEqual(DEFAULT_TASK_MAX_CONCURRENT, 2)
        for value in ('1', '2', '8'):
            self.assertEqual(read_task_limit({'TASK_MAX_CONCURRENT': value}), (int(value), None))

    def test_invalid_configuration_fails_closed_and_explicit_overrides_reject(self):
        for value in ('', '0', '9', '-1', '+2', '02', ' 2', '2 ', '2.0', 'true', True, 2.5):
            with self.subTest(value=value):
                limit, notice = read_task_limit({'TASK_MAX_CONCURRENT': value})
                self.assertEqual(limit, 1)
                self.assertTrue(notice)
                with self.assertRaises(ValueError):
                    parse_task_limit(value)


class Model:
    def __init__(self):
        self.calls = []
        self.active = self.peak = 0
        self.lock = threading.Lock()
        self.delay = .01
        self.failure = None
        self.failed = False
        self.tool = False
        self.closed = False

    def complete(self, **call):
        with self.lock:
            self.calls.append(call)
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            until = time.monotonic() + self.delay
            while time.monotonic() < until:
                if call['cancel_event'].wait(POLL_S):
                    raise BridgeError('cancelled')
                if time.monotonic() >= call['deadline']:
                    raise BridgeError('timeout')
            if self.failure and not self.failed:
                self.failed = True
                raise BridgeError(self.failure)
            if self.tool and 'tool_handler' in call:
                call['tool_handler']('note_read', {'id': 1}, call['deadline'])
            call['observer']({'inputTokens': 10, 'outputTokens': 4, 'totalTokens': 14})
            value = {'answer': 'The budget is 100 dollars.', 'nodes': [1], 'about_notes': True,
                'evidence': [{'node': 1, 'quote': 'The budget is 100 dollars.'}], 'calculations': []}
            if 'unresolved_issues' in call['schema']['properties']:
                value['unresolved_issues'] = []
            return json.dumps(value)
        finally:
            with self.lock:
                self.active -= 1

    def close(self):
        self.closed = True


class IsolatedModel(Model):
    """Simulate a bridge closing its whole process after one failed turn."""
    def complete(self, **call):
        if self.closed:
            raise BridgeError('disconnected')
        try:
            result = super().complete(**call)
        except BridgeError:
            self.close()
            raise
        if self.closed:
            raise BridgeError('disconnected')
        return result


class CleanupRetryTests(unittest.TestCase):
    """Process ownership recovery without graph dependencies or model calls."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = replace(ultra.Settings(), enabled=True)
        self.slots = threading.BoundedSemaphore(1)
        self.model_slots = threading.BoundedSemaphore(1)
        self.bridge = CodexBridge(None, model_slots=self.model_slots)
        self.process = Mock(pid=12345)
        self.process.poll.return_value = None
        self.bridge._process = self.process
        self.bridge._cleanup_pending = True
        self.assertTrue(self.model_slots.acquire(blocking=False))
        self.bridge._retained_model_slots = 1
        self.service = WorkflowService(lambda: self.settings, Registry(lambda: self.settings, {}),
            lambda: self.bridge, self.slots, 'test', root=Path(self.tmp.name)/'runs')
        self.service.store = RunStore(self.service.root)
        self.run_id = 'a' * 24
        now = time.time()
        self.service.store.save({'id': self.run_id, 'question': 'Budget analysis',
            'workflow': 'general', 'created_at': now, 'expires_at': now + TIMEOUT_S,
            'settings': self.settings.__dict__, 'status': 'complete', 'phase': 'complete',
            'cleanup': 'pending', 'agents': {}, 'tasks': [], 'events': [],
            'sequence': 0, 'tool_calls': 0, 'restarts': 0, 'result': None})
        executor = threading.Thread(target=lambda: None)
        executor.start(); executor.join(TIMEOUT_S)
        self.owner = {'thread': executor, 'cancel': threading.Event(),
            'bridge': self.bridge, 'bridge_cleanup_pending': True, 'bridge_lock': threading.Lock()}
        self.service.owners[self.run_id] = self.owner
        self.service.finished_executors.add(self.run_id)
        self.assertTrue(self.slots.acquire(blocking=False))
        initial = patch('workflow_runtime.BRIDGE_CLEANUP_RETRY_INITIAL_S', POLL_S)
        maximum = patch('workflow_runtime.BRIDGE_CLEANUP_RETRY_MAX_S', CLEANUP_RETRY_MAX_S)
        initial.start(); maximum.start()
        self.addCleanup(initial.stop); self.addCleanup(maximum.stop)
        self.addCleanup(self.finish)

    def finish(self):
        self.process.poll.return_value = 0
        self.service.close()

    def assert_capacity_released_once(self):
        for slots in (self.slots, self.model_slots):
            self.assertTrue(slots.acquire(blocking=False))
            self.assertFalse(slots.acquire(blocking=False))
            slots.release()

    def test_background_retry_recovers_capacity_without_blocking_catalog(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        attempts = []
        def terminate(*_):
            attempts.append(time.monotonic())
            if len(attempts) == 1:
                raise PermissionError
            entered.set()
            if not release.wait(TIMEOUT_S):
                raise PermissionError
            self.process.poll.return_value = 0
        with patch('codex_bridge.os.killpg', side_effect=terminate):
            self.service._schedule_cleanup_retry(self.run_id)
            worker = self.owner['cleanup_thread']
            self.service._schedule_cleanup_retry(self.run_id)
            self.assertIs(self.owner['cleanup_thread'], worker)
            self.assertTrue(entered.wait(TIMEOUT_S))
            self.assertEqual(len(self.service._cleanup_threads), 1)
            self.assertFalse(self.slots.acquire(blocking=False))
            self.assertFalse(self.model_slots.acquire(blocking=False))
            started = time.monotonic()
            catalog = self.service.catalog()
            self.assertLess(time.monotonic() - started, CATALOG_RESPONSE_S)
            self.assertEqual(catalog['active_ids'], [self.run_id])
            self.assertEqual(catalog['runs'][0]['cleanup'], 'pending')
            release.set()
            worker.join(TIMEOUT_S)
        self.assertFalse(worker.is_alive())
        self.assertGreaterEqual(attempts[1] - attempts[0], POLL_S)
        self.assertEqual(self.service.active_ids, [])
        self.assertEqual(self.service.detail(self.run_id)['cleanup'], 'complete')
        self.assert_capacity_released_once()

    def test_shutdown_stops_waiting_retries_but_keeps_unconfirmed_ownership(self):
        with patch('workflow_runtime.BRIDGE_CLEANUP_RETRY_INITIAL_S', TIMEOUT_S), \
                patch('codex_bridge.os.killpg', side_effect=PermissionError()) as terminate:
            self.service._schedule_cleanup_retry(self.run_id)
            worker = self.owner['cleanup_thread']
            self.service.close()
            self.assertEqual(terminate.call_count, 1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.service._cleanup_threads, set())
        self.assertEqual(self.service.active_ids, [self.run_id])
        self.assertEqual(self.service.store.get(self.run_id)['cleanup'], 'pending')
        self.assertFalse(self.slots.acquire(blocking=False))
        self.assertFalse(self.model_slots.acquire(blocking=False))
        self.process.poll.return_value = 0
        self.service.close()
        self.assertEqual(self.service.active_ids, [])
        self.assert_capacity_released_once()

    def test_shutdown_waits_for_active_retry_before_closing_catalog(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        failures = []
        def terminate(*_):
            entered.set()
            if not release.wait(TIMEOUT_S):
                raise PermissionError
            self.process.poll.return_value = 0
        def close():
            try:
                self.service.close()
            except Exception as error:
                failures.append(error)
        with patch('codex_bridge.os.killpg', side_effect=terminate):
            self.service._schedule_cleanup_retry(self.run_id)
            worker = self.owner['cleanup_thread']
            self.assertTrue(entered.wait(TIMEOUT_S))
            closer = threading.Thread(target=close)
            closer.start()
            self.assertTrue(self.service._cleanup_stop.wait(TIMEOUT_S))
            self.assertEqual(self.service.store.get(self.run_id)['cleanup'], 'pending')
            with self.assertRaises(WorkflowError):
                self.service.start('Cannot admit during shutdown')
            release.set()
            closer.join(TIMEOUT_S)
        self.assertFalse(closer.is_alive())
        self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(self.service._cleanup_threads, set())
        self.assertEqual(self.service.active_ids, [])
        self.assert_capacity_released_once()
        with self.assertRaises(sqlite3.ProgrammingError):
            self.service.store.get(self.run_id)


@unittest.skipUnless(AVAILABLE, 'Optional LangGraph dependencies not installed')
class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = replace(ultra.Settings(), enabled=True, terminal_monitor_enabled=False)
        self.model = Model()
        self.notes = dict(NOTE)
        self.registry = Registry(lambda: self.settings, {
            'notes_search': lambda query: {'notes': [dict(self.notes)]},
            'note_read': lambda id: dict(self.notes)})
        self.slots = threading.BoundedSemaphore(1)
        self.service = self.make_service()
        self.addCleanup(lambda: self.service.close())

    def make_service(self):
        return WorkflowService(lambda: self.settings, self.registry, lambda: self.model,
            self.slots, 'Use supplied evidence.', root=Path(self.tmp.name)/'runs')

    def wait(self, service=None):
        service = service or self.service
        service.thread.join(TIMEOUT_S)
        self.assertFalse(service.thread.is_alive(), 'Workflow exceeded test bound')
        return service.catalog()['runs'][0]

    def test_real_graph_parallel_tools_outputs_and_persistence(self):
        self.model.tool = True
        self.service.start('Analyze the budget', 'research')
        run = self.wait()
        self.assertEqual(run['status'], 'complete', run)
        self.assertEqual(self.model.peak, 3)
        self.assertEqual(len(self.model.calls), 4)
        self.assertEqual(run['total_tokens'], 56)
        self.assertEqual(run['tool_calls'], 4)
        reviewer=self.model.calls[-1]
        self.assertEqual(len(reviewer['payload']['confirmed_tool_calls']),3)
        self.assertTrue(all(item['tool']=='note_read' for item in reviewer['payload']['confirmed_tool_calls']))
        self.assertIn('Earlier workflow phases have already executed',reviewer['system'])
        self.assertEqual(run['result']['nodes'], [1])
        self.assertTrue(any(e['kind']=='tool_complete' for e in run['events']))
        self.assertTrue(any(e.get('node')=='review' for e in run['events']))
        self.service.close(); self.service=self.make_service()
        saved=self.service.catalog()['runs'][0]
        self.assertEqual(saved['result'],run['result'])
        for p in (Path(self.tmp.name)/'runs').glob('*.sqlite'):
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)

    def test_concurrency_is_bounded_and_shared_with_chat(self):
        self.model.delay = .15
        self.service.start('Analyze the budget')
        def launch(_):
            try:self.service.start('Another task')
            except WorkflowError as e:return e.status
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(list(pool.map(launch,range(20))), [429]*20)
        self.assertFalse(self.slots.acquire(blocking=False))
        self.assertEqual(self.wait()['status'], 'complete')
        self.assertTrue(self.slots.acquire(blocking=False));self.slots.release()

    def test_cancel_stops_active_model_and_releases_capacity(self):
        self.model.delay=1
        run=self.service.start('Analyze the budget')
        end=time.monotonic()+TIMEOUT_S
        while not self.model.calls and time.monotonic()<end:time.sleep(POLL_S)
        start=time.monotonic();self.service.cancel(run['id']);run=self.wait()
        self.assertEqual(run['status'],'cancelled')
        self.assertLess(time.monotonic()-start,.5)
        self.assertEqual(self.model.active,0)
        self.assertIsNone(run['result'])

    def test_retry_preserves_completed_workers_and_original_budget(self):
        self.model.failure='tool_failed'
        run=self.service.start('Analyze the budget')
        failed=self.wait();self.assertEqual(failed['status'],'failed')
        before=len(self.model.calls);expires=self.service.store.get(run['id'])['expires_at']
        completed=sum(a['status']=='complete' for a in failed['agents'])
        self.assertGreater(completed,0)
        self.service.start(resume=run['id']);result=self.wait()
        self.assertEqual(result['status'],'complete',result)
        self.assertEqual(len(self.model.calls)-before,4-completed)
        self.assertEqual(self.service.store.get(run['id'])['expires_at'],expires)

    def test_reopen_recovers_interrupted_run(self):
        self.model.failure='tool_failed';run=self.service.start('Analyze the budget');self.wait()
        self.service.change(run['id'],status='running');self.service.close();self.service=self.make_service()
        self.assertEqual(self.service.catalog()['runs'][0]['status'],'interrupted')
        self.service.start(resume=run['id']);self.assertEqual(self.wait()['status'],'complete')

    def test_deadline_and_expired_retry(self):
        self.settings=replace(self.settings,worker_timeout_s=1,request_timeout_s=1)
        self.model.delay=2;run=self.service.start('Analyze the budget');result=self.wait()
        self.assertEqual(result['status'],'failed')
        self.assertFalse(result['can_retry'])
        with self.assertRaises(WorkflowError):self.service.start(resume=run['id'])
        self.assertTrue(self.slots.acquire(blocking=False));self.slots.release()

    def test_transport_retry_and_usage(self):
        self.model.failure='disconnected';self.service.start('Analyze the budget','general');run=self.wait()
        self.assertEqual(run['status'],'complete')
        self.assertEqual(len(self.model.calls),3)
        self.assertFalse(run['usage_complete'])
        self.assertEqual(run['agents'][0]['attempts'],2)

    def test_failed_tool_is_visible(self):
        self.registry.callbacks['notes_search']=lambda query: (_ for _ in ()).throw(ValueError('private detail'))
        self.service.start('Analyze the budget');run=self.wait()
        self.assertEqual(run['status'],'failed')
        self.assertEqual(run['error'],'tool_failed')
        self.assertNotIn('private detail',json.dumps(run))
        self.assertTrue(any(e['kind']=='tool_failed' for e in run['events']))

    def test_changed_source_blocks_resume(self):
        self.model.failure='tool_failed';run=self.service.start('Analyze the budget');self.wait()
        self.notes['revision']='changed';self.service.start(resume=run['id']);run=self.wait()
        self.assertEqual(run['error'],'source_changed')

    def test_redaction_and_no_raw_trace(self):
        self.service.redactor=Redactor(['a-configured-secret'])
        self.service.start('Analyze api_key=hidden-value and sk-1234567890 and a-configured-secret')
        run=self.wait();public=json.dumps(run)
        for secret in ['hidden-value','sk-1234567890','a-configured-secret']:
            self.assertNotIn(secret,public)
        for event in run['events']:
            self.assertFalse(set(event)&{'payload','arguments','reasoning','system','response'})

    def test_invalid_input_and_disabled_flags(self):
        for question in ['',[], 'a'*2001]:
            with self.assertRaises(WorkflowError):self.service.start(question)
        with self.assertRaises(WorkflowError):self.service.start('task','untrusted_graph')
        with self.assertRaises(WorkflowError):self.service.start('task',[])
        self.settings=replace(self.settings,enabled=False)
        with self.assertRaises(WorkflowError):self.service.start('task')

    def test_slow_read_tool_cancel_is_bounded(self):
        gate=threading.Event()
        self.addCleanup(gate.set)
        self.registry.callbacks['notes_search']=lambda query: (gate.wait(TIMEOUT_S), {'notes':[NOTE]})[1]
        run=self.service.start('Analyze the budget')
        time.sleep(.05)
        started=time.monotonic();self.service.cancel(run['id']);result=self.wait()
        self.assertEqual(result['status'],'cancelled')
        self.assertLess(time.monotonic()-started,.5)
        gate.set()

    def test_retry_never_replenishes_attempts(self):
        self.settings=replace(self.settings,worker_retries=0)
        self.model.failure='disconnected';run=self.service.start('Analyze the budget','general')
        result=self.wait();self.assertFalse(result['can_retry'])
        with self.assertRaises(WorkflowError):self.service.start(resume=run['id'])
        self.assertEqual(len(self.model.calls),1)

    def test_recovery_source_check_is_visible_and_cancellable(self):
        self.model.failure='tool_failed'
        run=self.service.start('Analyze the budget');self.wait()
        gate,entered=threading.Event(),threading.Event()
        self.addCleanup(gate.set)
        def slow_read(id):
            entered.set();gate.wait(TIMEOUT_S);return dict(NOTE)
        self.registry.callbacks['note_read']=slow_read
        calls_before=len(self.model.calls)
        self.service.start(resume=run['id'])
        self.assertTrue(entered.wait(TIMEOUT_S))
        started=time.monotonic();self.service.cancel(run['id']);result=self.wait()
        self.assertEqual(result['status'],'cancelled')
        self.assertLess(time.monotonic()-started,.5)
        self.assertEqual(len(self.model.calls),calls_before)
        self.assertTrue(any(e.get('slot')=='recovery' and e['kind']=='tool_started' for e in result['events']))
        gate.set()

    def test_known_secret_is_removed_from_outputs(self):
        self.service.redactor=Redactor(['100 dollars'])
        self.service.start('Analyze the budget','general')
        result=self.wait()
        self.assertNotIn('100 dollars',json.dumps(result))
        self.assertIn('[redacted]',result['result']['answer'])

    def test_checkpoint_not_readable_as_user_supplied_path(self):
        self.service.initialize()
        with self.assertRaises(WorkflowError):self.service.detail('../../config.json')
        outside=Path(self.tmp.name)/'outside';outside.mkdir()
        linked=Path(self.tmp.name)/'link';linked.symlink_to(outside)
        with self.assertRaises(OSError):RunStore(linked)

    def parallel_service(self):
        self.service.close()
        self.slots = threading.BoundedSemaphore(2)
        self.service = WorkflowService(lambda: self.settings, self.registry, lambda: self.model,
            self.slots, 'Use supplied evidence.', root=Path(self.tmp.name)/'parallel-runs', max_concurrent=2)
        self.model.delay = .2
        return self.service

    def join_runs(self, threads):
        for thread in threads:
            thread.join(TIMEOUT_S)
            self.assertFalse(thread.is_alive(), 'Concurrent workflow exceeded test bound')

    def test_two_tasks_share_resource_bounds_and_release_both_slots(self):
        service = self.parallel_service()
        self.model.tool = True
        first = service.start('First budget analysis')
        first_thread = service.thread
        second = service.start('Second budget analysis')
        second_thread = service.thread
        catalog = service.catalog()
        self.assertEqual(catalog['active_ids'], [first['id'], second['id']])
        self.assertEqual(catalog['active'], first['id'])
        adapter = LangGraphAdapter(service)
        self.assertFalse(adapter.status(second['id'])['settled'])
        self.assertEqual(catalog['limits']['concurrent_runs'], 2)
        with self.assertRaises(WorkflowError) as full:
            service.start('Third task must wait')
        self.assertEqual(full.exception.status, 429)
        self.join_runs([first_thread, second_thread])
        self.assertEqual(service.detail(first['id'])['status'], 'complete')
        self.assertEqual(service.detail(second['id'])['status'], 'complete')
        self.assertLessEqual(self.model.peak, 3)
        self.assertGreater(self.model.peak, 1)
        self.assertEqual(service.active_ids, [])
        self.assertTrue(self.slots.acquire(blocking=False))
        self.assertTrue(self.slots.acquire(blocking=False))
        self.assertFalse(self.slots.acquire(blocking=False))
        self.slots.release(); self.slots.release()

    def test_retry_uses_its_own_owner_while_another_task_runs(self):
        service = self.parallel_service()
        self.model.failure = 'tool_failed'
        first = service.start('First budget analysis', 'general')
        self.join_runs([service.thread])
        self.assertEqual(service.detail(first['id'])['status'], 'failed')
        self.model.delay = .3
        other = service.start('Other budget analysis', 'general')
        other_thread = service.thread
        self.assertTrue(service.detail(first['id'])['can_retry'])
        resumed = service.start(resume=first['id'])
        retry_thread = service.thread
        self.assertEqual(resumed['id'], first['id'])
        self.assertEqual(set(service.active_ids), {first['id'], other['id']})
        self.join_runs([other_thread, retry_thread])
        self.assertEqual(service.detail(first['id'])['status'], 'complete')
        self.assertEqual(service.detail(other['id'])['status'], 'complete')

    def test_restart_recovers_both_interrupted_tasks_without_replaying(self):
        service = self.parallel_service()
        first = service.start('First budget analysis', 'general')
        first_thread = service.thread
        second = service.start('Second budget analysis', 'general')
        self.join_runs([first_thread, service.thread])
        for run in (first, second):
            service.change(run['id'], status='running')
        calls = len(self.model.calls)
        service.close()
        self.service = WorkflowService(lambda: self.settings, self.registry, lambda: self.model,
            self.slots, 'Use supplied evidence.', root=service.root, max_concurrent=2)
        catalog = self.service.catalog()
        self.assertEqual([row['status'] for row in catalog['runs']], ['interrupted', 'interrupted'])
        self.assertEqual(catalog['active_ids'], [])
        self.assertEqual(len(self.model.calls), calls)

    def test_cancelling_one_task_does_not_cancel_the_other(self):
        service = self.parallel_service()
        self.model.delay = .4
        first = service.start('First budget analysis', 'general')
        first_thread = service.thread
        second = service.start('Second budget analysis', 'general')
        second_thread = service.thread
        service.cancel(first['id'])
        self.join_runs([first_thread, second_thread])
        self.assertEqual(service.detail(first['id'])['status'], 'cancelled')
        self.assertEqual(service.detail(second['id'])['status'], 'complete')
        self.assertEqual(service.active_ids, [])

    def isolated_bridges(self, failure=None):
        service = self.parallel_service()
        models = []
        def factory():
            model = IsolatedModel()
            model.delay = .3
            if not models:
                model.failure = failure
            models.append(model)
            return model
        service.bridge_factory = factory
        first = service.start('First budget analysis', 'general')
        first_thread = service.thread
        end = time.monotonic() + TIMEOUT_S
        while (not models or not models[0].calls) and time.monotonic() < end:
            time.sleep(POLL_S)
        second = service.start('Second budget analysis', 'general')
        second_thread = service.thread
        while (len(models) < 2 or not models[1].calls) and time.monotonic() < end:
            time.sleep(POLL_S)
        self.assertEqual(len(models), 2)
        self.assertIsNot(models[0], models[1])
        return service, first, second, [first_thread, second_thread], models

    def test_cancelled_bridge_cannot_close_another_tasks_process(self):
        service, first, second, threads, models = self.isolated_bridges()
        service.cancel(first['id'])
        self.join_runs(threads)
        self.assertEqual(service.detail(first['id'])['status'], 'cancelled')
        self.assertEqual(service.detail(second['id'])['status'], 'complete')
        self.assertTrue(all(model.closed for model in models))
        self.assertEqual(service.active_ids, [])

    def test_failed_bridge_cannot_close_another_tasks_process(self):
        service, first, second, threads, models = self.isolated_bridges(failure='tool_failed')
        self.join_runs(threads)
        self.assertEqual(service.detail(first['id'])['status'], 'failed')
        self.assertEqual(service.detail(second['id'])['status'], 'complete')
        self.assertTrue(all(model.closed for model in models))
        self.assertEqual(service.active_ids, [])

    def test_failed_bridge_cleanup_retains_task_capacity_until_confirmed(self):
        service = self.parallel_service()
        model = Model()
        model.close = lambda: (_ for _ in ()).throw(OSError('Simulated cleanup failure'))
        service.bridge_factory = lambda: model
        run = service.start('Budget analysis', 'general')
        self.join_runs([service.thread])
        self.assertEqual(service.detail(run['id'])['status'], 'complete')
        self.assertEqual(service.detail(run['id'])['cleanup'], 'pending')
        self.assertEqual(service.active_ids, [run['id']])
        model.close = lambda: None
        service.close()
        self.assertEqual(service.active_ids, [])
        # close() deliberately closed the catalog, so replace it for test cleanup.
        self.service = self.make_service()

    def test_cancellation_is_prompt_while_waiting_for_shared_model_capacity(self):
        service = self.parallel_service()
        self.model.delay = .4
        first = service.start('First budget analysis', 'research')
        first_thread = service.thread
        end = time.monotonic() + TIMEOUT_S
        while self.model.active < 3 and time.monotonic() < end:
            time.sleep(POLL_S)
        self.assertEqual(self.model.active, 3)
        second = service.start('Second budget analysis', 'general')
        second_thread = service.thread
        started = time.monotonic()
        service.cancel(second['id'])
        self.join_runs([second_thread])
        self.assertLess(time.monotonic()-started, .5)
        self.assertEqual(service.detail(second['id'])['status'], 'cancelled')
        self.join_runs([first_thread])
        self.assertEqual(service.detail(first['id'])['status'], 'complete')

    def test_parallel_duplicate_admission_does_not_consume_second_slot(self):
        service = self.parallel_service()
        self.model.delay = .3
        key = 'c' * 24
        def launch(_):
            return service.start('Same budget task', 'general', request_key=key)['id']
        with ThreadPoolExecutor(max_workers=8) as pool:
            identifiers = list(pool.map(launch, range(20)))
        self.assertEqual(len(set(identifiers)), 1)
        first_thread = service.thread
        service.start('Different budget task', 'general')
        self.join_runs([first_thread, service.thread])
        self.assertEqual(len(service.catalog()['runs']), 2)

    def test_cancelled_slow_tool_retains_only_its_own_capacity(self):
        service = self.parallel_service()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def search(query):
            if query == 'Slow budget task':
                entered.set()
                release.wait(TIMEOUT_S)
            return {'notes': [dict(NOTE)]}
        self.registry.callbacks['notes_search'] = search
        slow = service.start('Slow budget task', 'general')
        slow_thread = service.thread
        self.assertTrue(entered.wait(TIMEOUT_S))
        fast = service.start('Fast budget task', 'general')
        fast_thread = service.thread
        service.cancel(slow['id'])
        self.join_runs([slow_thread, fast_thread])
        self.assertEqual(service.detail(slow['id'])['cleanup'], 'pending')
        self.assertEqual(service.active_ids, [slow['id']])
        self.assertFalse(service.detail(slow['id'])['can_retry'])
        replacement = service.start('Replacement budget task', 'general')
        self.join_runs([service.thread])
        self.assertEqual(service.detail(fast['id'])['status'], 'complete')
        self.assertEqual(service.detail(replacement['id'])['status'], 'complete')
        self.assertEqual(service.active_ids, [slow['id']])
        release.set()
        end = time.monotonic() + TIMEOUT_S
        while service.active_ids and time.monotonic() < end:
            time.sleep(POLL_S)
        self.assertEqual(service.active_ids, [])
        self.assertEqual(service.detail(slow['id'])['cleanup'], 'complete')


if __name__=='__main__':unittest.main()
