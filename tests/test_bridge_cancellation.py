"""Verify transport cancellation interrupts the actual turn protocol boundary."""
import threading
import tempfile
import time
import unittest
import subprocess
from types import SimpleNamespace
from unittest.mock import patch, Mock
from codex_bridge import CodexBridge, BridgeError

MODEL='gpt-6-luna'
TEST_WAIT_S = 2
CONCURRENT_WORKERS = 3


class CancellationTests(unittest.TestCase):
    def arguments(self):
        return dict(model=MODEL, effort='high', system='test', payload={}, schema={},
            deadline=time.monotonic() + TEST_WAIT_S, observer=lambda _: None)

    def test_unconfirmed_cleanup_retains_each_concurrent_model_slot(self):
        slots = threading.BoundedSemaphore(CONCURRENT_WORKERS)
        bridge = CodexBridge(None, model_slots=slots)
        process = Mock(pid=12345)
        process.poll.return_value = None
        bridge._process = process
        entered = threading.Barrier(CONCURRENT_WORKERS)
        failures = []
        def fail_cleanup(**_):
            entered.wait(TEST_WAIT_S)
            bridge.close()
        def invoke():
            try:
                bridge.complete(**self.arguments())
            except BridgeError as error:
                failures.append(error.code)
        with patch.object(bridge, '_complete', side_effect=fail_cleanup), \
                patch('codex_bridge.os.killpg', side_effect=PermissionError()):
            workers = [threading.Thread(target=invoke) for _ in range(CONCURRENT_WORKERS)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(TEST_WAIT_S)
                self.assertFalse(worker.is_alive())
        self.assertEqual(failures, ['unavailable'] * CONCURRENT_WORKERS)
        self.assertEqual(bridge._retained_model_slots, CONCURRENT_WORKERS)
        self.assertFalse(slots.acquire(blocking=False))
        process.poll.return_value = 0
        bridge.close()
        bridge.close()  # Repeated confirmation must not release a token twice.
        self.assertEqual(bridge._retained_model_slots, 0)
        for _ in range(CONCURRENT_WORKERS):
            self.assertTrue(slots.acquire(blocking=False))
        self.assertFalse(slots.acquire(blocking=False))
        for _ in range(CONCURRENT_WORKERS):
            slots.release()

    def test_process_exit_and_complete_race_release_one_slot(self):
        for exit_before_return in (False, True):
            with self.subTest(exit_before_return=exit_before_return):
                slots = threading.BoundedSemaphore(1)
                bridge = CodexBridge(None, model_slots=slots)
                process = Mock(pid=12345)
                process.poll.return_value = None
                bridge._process = process
                entered, shutdown, finish_call, finish_exit = [threading.Event() for _ in range(4)]
                failures = []
                def complete(**_):
                    entered.set()
                    if not finish_call.wait(TEST_WAIT_S):
                        raise AssertionError('synthetic call did not finish')
                    return {'answer': 'ready'}
                def wait(timeout):
                    shutdown.set()
                    if not finish_exit.wait(TEST_WAIT_S):
                        raise subprocess.TimeoutExpired('synthetic', timeout)
                    process.poll.return_value = 0
                    return 0
                def invoke(action):
                    try:
                        action()
                    except Exception as error:
                        failures.append(error)
                process.wait.side_effect = wait
                with patch.object(bridge, '_complete', side_effect=complete), \
                        patch('codex_bridge.os.killpg'):
                    worker = threading.Thread(target=invoke,
                        args=(lambda: bridge.complete(**self.arguments()),))
                    closer = threading.Thread(target=invoke, args=(bridge.close,))
                    worker.start()
                    self.assertTrue(entered.wait(TEST_WAIT_S))
                    closer.start()
                    self.assertTrue(shutdown.wait(TEST_WAIT_S))
                    if exit_before_return:
                        finish_exit.set()
                        closer.join(TEST_WAIT_S)
                        self.assertFalse(slots.acquire(blocking=False))
                        finish_call.set()
                    else:
                        finish_call.set()
                        worker.join(TEST_WAIT_S)
                        self.assertEqual(bridge._retained_model_slots, 1)
                        self.assertFalse(slots.acquire(blocking=False))
                        finish_exit.set()
                    worker.join(TEST_WAIT_S)
                    closer.join(TEST_WAIT_S)
                self.assertFalse(worker.is_alive())
                self.assertFalse(closer.is_alive())
                self.assertEqual(failures, [])
                self.assertEqual(bridge._retained_model_slots, 0)
                self.assertTrue(slots.acquire(blocking=False))
                self.assertFalse(slots.acquire(blocking=False))
                slots.release()
                bridge.close()

    def test_new_completion_retries_cleanup_before_waiting_for_retained_capacity(self):
        slots = threading.BoundedSemaphore(1)
        bridge = CodexBridge(None, model_slots=slots)
        process = Mock(pid=12345)
        process.poll.return_value = None
        bridge._process = process
        with patch.object(bridge, '_complete', side_effect=lambda **_: bridge.close()), \
                patch('codex_bridge.os.killpg', side_effect=PermissionError()):
            with self.assertRaises(BridgeError):
                bridge.complete(**self.arguments())
        self.assertFalse(slots.acquire(blocking=False))
        process.poll.return_value = 0
        with patch.object(bridge, '_complete', return_value={'answer': 'ready'}) as complete:
            self.assertEqual(bridge.complete(**self.arguments()), {'answer': 'ready'})
            complete.assert_called_once()
        self.assertEqual(bridge._retained_model_slots, 0)
        self.assertTrue(slots.acquire(blocking=False))
        slots.release()

    def test_failed_process_cleanup_keeps_ownership_for_retry(self):
        for failure in (PermissionError(), subprocess.TimeoutExpired('synthetic', TEST_WAIT_S)):
            with self.subTest(failure=type(failure).__name__):
                bridge = CodexBridge(None)
                process = Mock(pid=12345)
                process.poll.return_value = None
                process.wait.side_effect = failure
                scratch = Mock()
                bridge._process, bridge._scratch = process, scratch
                with patch('codex_bridge.os.killpg', side_effect=PermissionError() if isinstance(failure, PermissionError) else None):
                    with self.assertRaises(BridgeError):
                        bridge.close()
                self.assertIs(bridge._process, process)
                self.assertIs(bridge._scratch, scratch)
                self.assertTrue(bridge._cleanup_pending)
                scratch.cleanup.assert_not_called()
                process.stdin.close.assert_not_called()
                # A subsequent confirmed exit must release the retained owner.
                process.poll.return_value = 0
                bridge.close()
                self.assertIsNone(bridge._process)
                self.assertIsNone(bridge._scratch)
                self.assertFalse(bridge._cleanup_pending)
                scratch.cleanup.assert_called_once()

    def test_restart_cannot_reuse_process_with_unconfirmed_cleanup(self):
        bridge = CodexBridge(None)
        bridge._cleanup_pending = True
        with patch.object(bridge, 'close', side_effect=BridgeError('unavailable')) as close:
            with self.assertRaises(BridgeError):
                bridge.start(time.monotonic() + TEST_WAIT_S)
            close.assert_called_once()
        bridge._cleanup_pending = False

    def test_shared_model_capacity_wait_can_cancel_without_starting(self):
        slots=threading.BoundedSemaphore(1)
        first,second=CodexBridge(None,model_slots=slots),CodexBridge(None,model_slots=slots)
        entered,release=threading.Event(),threading.Event()
        cancelled=threading.Event();failures=[]
        arguments=dict(model=MODEL,effort='high',system='test',payload={},schema={},
            deadline=time.monotonic()+TEST_WAIT_S,observer=lambda _:None)
        def hold(**_):
            entered.set()
            self.assertTrue(release.wait(TEST_WAIT_S))
            return {'answer':'ready'}
        def waiting():
            try:second.complete(**arguments,cancel_event=cancelled)
            except BridgeError as error:failures.append(error.code)
        with patch.object(first,'_complete',side_effect=hold),patch.object(second,'_complete') as start:
            owner=threading.Thread(target=lambda:first.complete(**arguments));owner.start()
            self.assertTrue(entered.wait(TEST_WAIT_S))
            waiter=threading.Thread(target=waiting);waiter.start();cancelled.set()
            waiter.join(TEST_WAIT_S)
            self.assertFalse(waiter.is_alive());self.assertEqual(failures,['cancelled'])
            start.assert_not_called()
            release.set();owner.join(TEST_WAIT_S)
            self.assertFalse(owner.is_alive())
        self.assertTrue(slots.acquire(blocking=False));slots.release()

    def test_shared_model_capacity_releases_after_failure(self):
        slots=threading.BoundedSemaphore(1);bridge=CodexBridge(None,model_slots=slots)
        with patch.object(bridge,'_complete',side_effect=BridgeError('protocol_failure')):
            with self.assertRaises(BridgeError):
                bridge.complete(model=MODEL,effort='high',system='test',payload={},schema={},
                    deadline=time.monotonic()+TEST_WAIT_S,observer=lambda _:None)
        self.assertTrue(slots.acquire(blocking=False));slots.release()

    def test_interrupt_ack_requires_terminal_event_or_process_termination(self):
        for confirmed in (False,True):
            with self.subTest(confirmed=confirmed):
                bridge=CodexBridge(None)
                bridge._scratch=SimpleNamespace(name='synthetic')
                bridge._process=SimpleNamespace()
                bridge._models={MODEL:{'supportedReasoningEfforts':[{'reasoningEffort':'high'}]}}
                cancelled=threading.Event()
                def rpc(method,params,deadline):
                    if method=='account/read':return {'account':{'type':'chatgpt'}}
                    if method=='account/rateLimits/read':return {'ordinaryUsageAllowed':True,'rateLimits':{'primary':{'usedPercent':0}}}
                    if method=='thread/start':return {'thread':{'id':'test-thread'}}
                    if method=='turn/start':cancelled.set();return {'turn':{'id':'test-turn'}}
                    if method=='turn/interrupt' and confirmed:bridge._calls['test-thread'].put(('done','interrupted'))
                    return {}
                with patch.object(bridge,'start'),patch.object(bridge,'_rpc',side_effect=rpc),patch.object(bridge,'close') as close,patch('codex_bridge.INTERRUPT_TIMEOUT_S',.02):
                    with self.assertRaises(BridgeError):
                        bridge.complete(model=MODEL,effort='high',system='test',payload={},schema={},
                            deadline=time.monotonic()+1,observer=lambda _:None,cancel_event=cancelled)
                    self.assertEqual(close.called,not confirmed)
                bridge._process=bridge._scratch=None

    def test_cancel_interrupts_inflight_turn(self):
        bridge=CodexBridge(None)
        bridge._scratch=tempfile.TemporaryDirectory()
        self.addCleanup(bridge.close)
        bridge._models={MODEL:{'supportedReasoningEfforts':[{'reasoningEffort':'high'}]}}
        cancelled=threading.Event();methods=[]
        def rpc(method,params,deadline):
            methods.append(method)
            if method=='account/read':return {'account':{'type':'chatgpt'}}
            if method=='account/rateLimits/read':return {'ordinaryUsageAllowed':True,'rateLimits':{'primary':{'usedPercent':0}}}
            if method=='thread/start':return {'thread':{'id':'test-thread'},'model':MODEL}
            if method=='turn/start':
                cancelled.set()
                return {'turn':{'id':'test-turn'}}
            return {}
        with patch.object(bridge,'start'),patch.object(bridge,'_rpc',side_effect=rpc):
            with self.assertRaises(BridgeError) as failure:
                bridge.complete(model=MODEL,effort='high',system='test',payload={},schema={},
                    deadline=time.monotonic()+1,observer=lambda _:None,cancel_event=cancelled)
        self.assertEqual(failure.exception.code,'cancelled')
        self.assertEqual(methods,['account/read','account/rateLimits/read','thread/start','turn/start','turn/interrupt','thread/unsubscribe'])
        self.assertFalse(bridge._calls)

    def test_precancelled_turn_never_starts(self):
        bridge=CodexBridge(None);cancelled=threading.Event();cancelled.set()
        with patch.object(bridge,'start') as start:
            with self.assertRaises(BridgeError):
                bridge.complete(model=MODEL,effort='high',system='test',payload={},schema={},
                    deadline=time.monotonic()+1,observer=lambda _:None,cancel_event=cancelled)
            start.assert_not_called()


if __name__=='__main__':unittest.main()
