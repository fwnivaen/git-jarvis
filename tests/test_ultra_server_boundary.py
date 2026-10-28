"""Real server/Ultra locking boundary; scripted bridge, no live inference/state."""
import contextlib
import json
import queue
import threading
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import server
import ultra

THREAD_TIMEOUT_S = 2
FRAME = b'\xff\xd8\xff\x00\xff\xd9'
NOTE = {'id': 7, 'title': 'Budget', 'text': 'The budget is 120 pounds.'}


def answer_for(call):
    notes = call['payload']['notes']
    value = {'answer': call['payload']['question'], 'about_notes': bool(notes),
        'nodes': [notes[0]['id']] if notes else [],
        'evidence': [{'node': notes[0]['id'], 'quote': notes[0]['text']}] if notes else [],
        'calculations': []}
    return {'route': 'direct', 'job': 'general', 'response': value} if call['schema'] is ultra.DISPATCH_SCHEMA else value


class ScriptedBridge:
    def __init__(self, handler=answer_for):
        self.calls, self.handler = [], handler

    def complete(self, **call):
        self.calls.append(call)
        call['observer']({'inputTokens': 10, 'outputTokens': 4, 'totalTokens': 14})
        return json.dumps(self.handler(call))

    def close(self):
        pass


class UltraServerBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.settings = replace(ultra.Settings(), enabled=True, terminal_monitor_enabled=False,
            crew_profiles_enabled=False, collaboration_enabled=False, structured_reports_enabled=False,
            workflows_enabled=False, checkpoint_enabled=False, evaluation_enabled=False,
            memory_enabled=False, tools_enabled=False, preferences_enabled=False)
        self.bridge = ScriptedBridge()
        self.runtime = ultra.UltraJarvis(lambda: self.bridge)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(server, 'ULTRA', self.runtime))
        self.stack.enter_context(patch.object(server, 'ACTIVE_BRAIN', None))
        self.stack.enter_context(patch.object(server, 'BRAIN', SimpleNamespace(active_path=None)))
        self.read_settings = self.stack.enter_context(patch.object(ultra.Settings, 'read',
            return_value=(self.settings, None)))
        for target in ('server.ask_brain', 'server.subprocess.Popen', 'server.urllib.request.urlopen'):
            self.stack.enter_context(patch(target, side_effect=AssertionError('Live boundary forbidden')))

    def launch(self, callback):
        result = queue.Queue(maxsize=1)
        def invoke():
            try:
                result.put((True, callback()))
            except BaseException as error:
                result.put((False, error))
        thread = threading.Thread(target=invoke, daemon=True)
        thread.start()
        return thread, result

    def finish(self, thread, result):
        thread.join(THREAD_TIMEOUT_S)
        self.assertFalse(thread.is_alive(), 'server.answer_request deadlocked before returning')
        okay, value = result.get_nowait()
        if not okay:
            raise value
        return value

    def request(self, question, notes=None, **options):
        return server.answer_request('Answer only from supplied data.', notes or [], [], question, **options)

    def test_ordinary_ultra_request_returns_through_actual_server_source_wrapper(self):
        thread, result = self.launch(lambda: self.request('Budget answer', [NOTE]))
        value = self.finish(thread, result)
        self.assertEqual(value['answer'], 'Budget answer')
        self.assertEqual(value['nodes'], [NOTE['id']])
        self.assertEqual(len(self.bridge.calls), 1)
        self.assertIs(self.bridge.calls[0]['schema'], ultra.DISPATCH_SCHEMA)
        self.assertFalse(self.runtime.monitor.snapshot()['active'])

    def test_image_request_with_chat_routing_off_still_completes_cheap_vision_branch(self):
        self.read_settings.return_value = (replace(self.settings, routing_enabled=False,
                                                  cheap_vision_enabled=True), None)
        thread, result = self.launch(lambda: self.request('Frame answer', image=FRAME))
        value = self.finish(thread, result)
        self.assertEqual(value['answer'], 'Frame answer')
        self.assertEqual(value['nodes'], [])
        self.assertFalse(value['about_notes'])
        self.assertEqual(len(self.bridge.calls), 1)
        self.assertEqual(self.bridge.calls[0]['image'], FRAME)
        self.assertEqual(self.bridge.calls[0]['model'], self.settings.quick_model)
        self.assertIs(self.bridge.calls[0]['schema'], ultra.CHECKED_SCHEMA)
        self.assertFalse(self.runtime.monitor.snapshot()['active'])

    def test_concurrent_calls_remain_serialized_until_each_source_snapshot_is_bound(self):
        entered, release, second_started = threading.Event(), threading.Event(), threading.Event()
        snapshots = []
        first_bound = threading.Event()
        other_note = {'id': 8, 'title': 'Later', 'text': 'A distinct later source.'}
        def handle(call):
            if call['payload']['question'] == 'First':
                entered.set()
                if not release.wait(THREAD_TIMEOUT_S):
                    raise AssertionError('fixture release was not delivered')
            else:
                self.assertTrue(first_bound.is_set(), 'Another request replaced sources before binding')
            return answer_for(call)
        self.bridge.handler = handle
        def bind(value, supplied):
            snapshots.append((value['answer'], [row['id'] for row in supplied]))
            if value['answer'] == 'First':
                first_bound.set()
            return value
        with patch.object(server, 'bind_brain_sources', side_effect=bind):
            first = self.launch(lambda: self.request('First', [NOTE]))
            self.assertTrue(entered.wait(THREAD_TIMEOUT_S))
            def second_request():
                second_started.set()
                return self.request('Second', [other_note])
            second = self.launch(second_request)
            try:
                self.assertTrue(second_started.wait(THREAD_TIMEOUT_S))
            finally:
                release.set()
            first_value, second_value = self.finish(*first), self.finish(*second)
        self.assertEqual(first_value['nodes'], [7])
        self.assertEqual(second_value['nodes'], [8])
        self.assertEqual(snapshots, [('First', [7]), ('Second', [8])])
        self.assertEqual(len(self.bridge.calls), 2)


if __name__ == '__main__':
    unittest.main()
