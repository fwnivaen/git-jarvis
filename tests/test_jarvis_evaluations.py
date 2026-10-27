"""Evaluation artifacts must preserve pending checks and actual failures."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import evaluate_jarvis as evaluation


class EvaluationTests(unittest.TestCase):
    def test_representative_cases_have_unique_requests_oracles_and_honest_modes(self):
        cases = evaluation.load_cases()
        self.assertEqual(len(cases), 30)
        self.assertEqual(len({case['id'] for case in cases}), 30)
        self.assertTrue(all(case['request'] and case['oracle'] for case in cases))
        categories = {case['category'] for case in cases}
        self.assertTrue({'clock', 'http', 'planning', 'tasks', 'recovery', 'notes',
            'documents', 'voice', 'background', 'accessibility'} <= categories)

    def test_offline_checks_run_real_boundaries_and_keep_manual_work_pending(self):
        report = evaluation.evaluate()
        self.assertEqual(report['summary'], {'pass': 25, 'fail': 0, 'manual': 5, 'not_run': 0})
        self.assertFalse(report['complete'])
        self.assertFalse(report['end_to_end_model_task_success_measured'])
        self.assertFalse(report['live_services_accessed'])
        self.assertFalse(report['private_config_accessed'])
        self.assertEqual(report['model_calls'], 0)
        self.assertTrue(all(row['evidence'] for row in report['cases']))

    def test_failed_boundary_is_reported_without_exception_payload_or_false_completion(self):
        case = next(case for case in evaluation.load_cases() if case['mode'] == 'offline')
        def fail():
            raise AssertionError('Do not disclose synthetic-private-payload')
        with patch.dict(evaluation.CHECKS, {case['check']: fail}):
            report = evaluation.evaluate([case])
        self.assertEqual(report['summary']['fail'], 1)
        self.assertFalse(report['complete'])
        self.assertNotIn('synthetic-private-payload', json.dumps(report))

    def test_unregistered_check_and_duplicate_case_are_rejected(self):
        data = json.loads(evaluation.FIXTURE.read_text())
        for mutation in ('unknown-check', 'duplicate-id'):
            candidate = copy.deepcopy(data)
            if mutation == 'unknown-check':
                candidate['cases'][0]['check'] = 'arbitrary_code'
            else:
                candidate['cases'][1]['id'] = candidate['cases'][0]['id']
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory)/'cases.json'
                path.write_text(json.dumps(candidate))
                with self.assertRaises(ValueError):
                    evaluation.load_cases(path)


if __name__ == '__main__':
    unittest.main()
