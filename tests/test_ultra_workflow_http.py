"""Workflow HTTP integration: local metadata, explicit options and honest fallback."""
import json
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import server
import ultra
from test_offline import TEST_CONFIG, request

ORIGIN = "http://127.0.0.1:4700"
QUESTION = "Compare the supplied notes."
RUN_ID = "a" * 24
ANSWER = {"answer": "Ready.", "about_notes": False, "nodes": []}
TEST_WAIT_S = 2


class WorkflowHttpTests(unittest.TestCase):
    def test_busy_model_capacity_returns_a_structured_reply_and_releases_chat(self):
        task_slots=threading.BoundedSemaphore(2)
        chat_slots=threading.BoundedSemaphore(1)
        posture=dict(eyes_active=False,present=False,head_down=False,slouched=False)
        with patch.object(server,'MODEL_SLOTS',threading.BoundedSemaphore(0)), \
                patch.object(server,'CHAT_SLOTS',chat_slots), \
                patch.object(server,'BRAIN_SLOTS',task_slots), \
                patch.object(server.ultra.Settings,'read',return_value=(ultra.Settings(),None)), \
                patch.object(server.build,'NOTES_DIR',Path(__file__).parent), \
                patch.object(server,'retrieve',return_value=[]), \
                patch.object(server.SENSORS,'state',return_value=posture), \
                patch.object(server,'_ask_brain') as inference:
            status,_,raw=request('/chat','POST',{'question':QUESTION})
        result=json.loads(raw)
        self.assertEqual(status,503)
        self.assertTrue(result['error'])
        self.assertIn('model capacity',result['answer'])
        self.assertEqual(result['nodes'],[])
        inference.assert_not_called()
        self.assertTrue(chat_slots.acquire(blocking=False));chat_slots.release()
        self.assertTrue(task_slots.acquire(blocking=False))
        self.assertTrue(task_slots.acquire(blocking=False))
        self.assertFalse(task_slots.acquire(blocking=False))
        task_slots.release();task_slots.release()

    def test_chat_serialization_leaves_capacity_for_another_task(self):
        entered,release=threading.Event(),threading.Event();results=[]
        task_slots=threading.BoundedSemaphore(2)
        def answer(*_args,**_kwargs):
            entered.set();self.assertTrue(release.wait(TEST_WAIT_S));return ANSWER
        posture=dict(eyes_active=False,present=False,head_down=False,slouched=False)
        with patch.object(server,'CHAT_SLOTS',threading.BoundedSemaphore(1)), \
                patch.object(server,'BRAIN_SLOTS',task_slots), \
                patch.object(server.build,'NOTES_DIR',Path(__file__).parent), \
                patch.object(server,'retrieve',return_value=[]), \
                patch.object(server.SENSORS,'state',return_value=posture), \
                patch.object(server,'answer_request',side_effect=answer):
            worker=threading.Thread(target=lambda:results.append(request('/chat','POST',{'question':QUESTION})[0]))
            worker.start();self.assertTrue(entered.wait(TEST_WAIT_S))
            try:
                self.assertEqual(request('/chat','POST',{'question':'Another question'})[0],429)
                self.assertTrue(task_slots.acquire(blocking=False));task_slots.release()
            finally:
                release.set();worker.join(TEST_WAIT_S)
            self.assertEqual(results,[200]);self.assertFalse(worker.is_alive())

    def setUp(self):
        config = patch.object(server, "load_config", return_value=dict(TEST_CONFIG))
        config.start()
        self.addCleanup(config.stop)

    def test_workflow_metadata_uses_local_boundary(self):
        for path, method, value in (
                ("/agents/workflows", "workflow_catalog", {"enabled": True, "workflows": []}),
                ("/agents/runs", "run_summaries", {"enabled": True, "runs": []})):
            with self.subTest(path=path), patch.object(server.ULTRA, method, return_value=value) as getter:
                status, _, raw = request(path)
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(raw), value)
                getter.assert_called_once()
                getter.reset_mock()
                self.assertEqual(request(path, headers={"Origin": "https://outside.example"})[0], 403)
                getter.assert_not_called()
        self.assertEqual(request("/.local/ultra-runs/" + RUN_ID + ".json")[0], 404)

    def test_options_reach_runtime_without_altering_question(self):
        settings = replace(ultra.Settings(), enabled=True)
        with patch.object(server.ultra.Settings, "read", return_value=(settings, None)), \
                patch.object(server, "ACTIVE_BRAIN", None), \
                patch.object(server.ULTRA, "answer", return_value=ANSWER) as answer:
            result = server.answer_request("system", [], [], QUESTION, workflow="research", resume_run=RUN_ID)
            self.assertEqual(result, ANSWER)
            self.assertEqual(answer.call_args.kwargs["workflow"], "research")
            self.assertEqual(answer.call_args.kwargs["resume_run"], RUN_ID)

    def test_disabled_or_manual_route_does_not_silently_restart(self):
        for settings, manual in ((ultra.Settings(), None),
                (replace(ultra.Settings(), enabled=True), ("codex-cli", "gpt-6-astra"))):
            with self.subTest(manual=manual), \
                    patch.object(server.ultra.Settings, "read", return_value=(settings, None)), \
                    patch.object(server, "ACTIVE_BRAIN", manual), \
                    patch.object(server, "ask_brain") as legacy, \
                    patch.object(server.ULTRA, "answer") as runtime:
                self.assertTrue(server.answer_request("system", [], [], QUESTION, resume_run=RUN_ID)["error"])
                legacy.assert_not_called()
                runtime.assert_not_called()

    def test_chat_passes_explicit_options_and_validates_types(self):
        posture = dict(eyes_active=False, present=False, head_down=False, slouched=False)
        with patch.object(server.build, "NOTES_DIR", Path(__file__).parent), \
                patch.object(server, "retrieve", return_value=[]), \
                patch.object(server.SENSORS, "state", return_value=posture), \
                patch.object(server, "answer_request", return_value=ANSWER) as answer:
            status, _, _ = request("/chat", "POST", dict(question=QUESTION, workflow="research", resume_run=RUN_ID))
            self.assertEqual(status, 200)
            self.assertEqual(answer.call_args.kwargs, {"workflow": "research", "resume_run": RUN_ID})
            for field, value in (("workflow", True), ("resume_run", []), ("workflow", " research"),
                    ("resume_run", "x" * (server.MAX_RESUME_RUN_CHARS + 1))):
                self.assertEqual(request("/chat", "POST", {"question": QUESTION, field: value})[0], 400)
        self.assertEqual(request("/remember", "POST", dict(question=QUESTION, workflow="research"))[0], 400)


if __name__ == "__main__":
    unittest.main()
