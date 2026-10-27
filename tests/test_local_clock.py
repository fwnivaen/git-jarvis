"""Local time must follow New York's daylight saving and never guess another zone."""
from datetime import datetime, timezone
import unittest
from unittest.mock import patch
import local_clock
import server
from test_offline import request


class LocalClockTests(unittest.TestCase):
    def test_winter_and_summer_offsets(self):
        for month,expected in ((1,'7:00 AM'),(7,'8:00 AM')):
            with self.subTest(month=month):
                value=local_clock.answer('Jarvis, what time is it?',datetime(2026,month,15,12,tzinfo=timezone.utc))
                self.assertEqual(value['answer'],f"It's {expected} in New York, Sir.")
                self.assertEqual(value['nodes'],[])

    def test_local_date_crosses_utc_boundary(self):
        value=local_clock.answer("What's today’s date?",datetime(2026,1,1,2,tzinfo=timezone.utc))
        self.assertEqual(value['answer'],"It's Wednesday, December 31, 2025, Sir.")

    def test_other_places_and_contextual_questions_are_not_intercepted(self):
        for question in ('What time is it in London?','What time does the meeting start?',
                         'Remember what time it is','What is the date in the note?'):
            self.assertIsNone(local_clock.answer(question))

    def test_http_clock_answer_bypasses_models_and_missing_vault(self):
        expected={'answer':'The current local time.','about_notes':False,'nodes':[]}
        with patch.object(server.local_clock,'answer',return_value=expected), \
                patch.object(server,'answer_request') as model:
            status,_,raw=request('/chat','POST',{'question':'What time is it?'})
            self.assertEqual(status,200);self.assertIn(b'current local time',raw)
            model.assert_not_called()


if __name__=='__main__':unittest.main()
