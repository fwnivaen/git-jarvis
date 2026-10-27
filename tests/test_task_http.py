"""Canonical task and dry-run boundaries: no network, login, or model calls."""
import json
import unittest
from unittest.mock import patch

import server
from workflow_runtime import WorkflowError
from test_offline import request

ORIGIN = {"Origin": "http://127.0.0.1:4700"}
RUN_ID = "a" * 24


class TaskHttpTests(unittest.TestCase):
    def test_catalog_and_detail_are_canonical_adapter_reads(self):
        with patch.object(server.TASK_API, "catalog", return_value={"tasks": []}) as catalog, \
                patch.object(server.TASK_API, "detail", return_value={"id": RUN_ID}) as detail:
            value = json.loads(request("/stage2/tasks")[2])
            self.assertEqual(value['tasks'], [])
            self.assertIn('note_actions', value)
            catalog.assert_called_once_with()
            self.assertEqual(json.loads(request("/stage2/tasks/task?id=" + RUN_ID)[2]), {"id": RUN_ID})
            detail.assert_called_once_with(RUN_ID)
            self.assertEqual(request("/stage2/tasks", headers={"Origin": "https://outside.example"})[0], 403)
            self.assertEqual(catalog.call_count, 1)

    def test_preview_calls_only_dry_run_and_requires_same_origin(self):
        body = {"question": "Compare notes", "workflow": "research"}
        with patch.object(server.TASK_API, "plan", return_value={"dry_run": True}) as plan, \
                patch.object(server.WORKFLOWS, "start", side_effect=AssertionError("preview admitted work")):
            status, _, raw = request("/stage2/tasks/plan", "POST", body, ORIGIN)
            self.assertEqual(status, 200)
            self.assertTrue(json.loads(raw)["dry_run"])
            plan.assert_called_once_with("Compare notes", "research")
            for headers in ({}, {"Origin": "https://outside.example"}):
                self.assertEqual(request("/stage2/tasks/plan", "POST", body, headers)[0], 403)
            self.assertEqual(plan.call_count, 1)

    def test_preview_rejects_extra_fields_and_surfaces_validation(self):
        with patch.object(server.TASK_API, "plan", side_effect=WorkflowError("Supply a short question.")) as plan:
            for body in ([], {"question": "q", "execute": True}):
                self.assertEqual(request("/stage2/tasks/plan", "POST", body, ORIGIN)[0], 400)
            plan.assert_not_called()
            self.assertEqual(request("/stage2/tasks/plan", "POST", {"question": ""}, ORIGIN)[0], 400)

    def test_local_clock_does_not_need_config_or_model(self):
        with patch.object(server, "load_config", side_effect=AssertionError("clock read config")), \
                patch.object(server, "answer_request", side_effect=AssertionError("clock used model")):
            status, _, raw = request("/chat", "POST", {"question": "What time is it?"})
            self.assertEqual(status, 200)
            self.assertIn("New York", json.loads(raw)["answer"])


if __name__ == "__main__":
    unittest.main()
