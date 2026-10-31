"""Speech ownership, cache integrity and HTTP boundaries; no models or network."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

from local_speech import LocalSpeech
import server
from test_offline import request

ROOT = Path(__file__).resolve().parents[1]
REQUEST_ID = 'voice-test-request-0001'


class SpeechTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.voice = LocalSpeech(ROOT, worker_command=['unused-test-worker'],
                                 cache_directory=Path(self.scratch.name))
        self.addCleanup(self.voice.close)

    def cached(self, text):
        _, key = self.voice._key(text)
        target = self.voice.cache / (key + '.wav')
        with wave.open(str(target), 'wb') as audio:
            audio.setparams((1, 2, 44100, 0, 'NONE', 'not compressed'))
            audio.writeframes(b'\x00\x00' * 441)
        target.with_suffix('.json').write_text(json.dumps({
            'fingerprint': self.voice.fingerprint,
            'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}))
        return target

    def test_cancel_arriving_before_submission_stays_cancelled(self):
        self.voice.cancel(REQUEST_ID)
        result = self.voice.submit('Ready.', REQUEST_ID)
        self.assertEqual(result['state'], 'cancelled')
        self.assertFalse(self.voice.status()['active'])
        self.assertIsNone(self.voice.process)

    def test_same_request_is_idempotent_but_cannot_change_text(self):
        self.cached('Ready.')
        first = self.voice.submit('Ready.', REQUEST_ID)
        second = self.voice.submit('Ready.', REQUEST_ID)
        self.assertEqual(first, second)
        self.assertTrue(first['cached'])
        self.assertIsNotNone(self.voice.audio(first['id']))
        with self.assertRaises(ValueError):
            self.voice.submit('Different reply.', REQUEST_ID)
        self.voice.cancel(REQUEST_ID)
        self.assertIsNone(self.voice.audio(first['id']))
        self.assertIsNone(self.voice.part(first['id'], 0))

    def test_changed_or_wrong_format_audio_is_never_served(self):
        target = self.cached('Ready.')
        ready = self.voice.submit('Ready.', REQUEST_ID)
        target.write_bytes(target.read_bytes() + b'corrupt')
        self.assertIsNone(self.voice.audio(ready['id']))
        self.assertIsNone(self.voice.audio('../../config.json'))
        self.assertIsNone(self.voice.part(ready['id'], -1))

    def test_opaque_http_paths_and_head_body(self):
        self.cached('Ready.')
        ready = self.voice.submit('Ready.', REQUEST_ID)
        with patch.object(server, 'SPEECH', self.voice):
            path = ready['status_url']
            self.assertEqual(request(path)[0], 200)
            self.assertEqual(request(path, 'HEAD')[2], b'')
            self.assertEqual(request(ready['audio_url'], 'HEAD')[2], b'')
            for path in ('/speech/audio/../../config.json', '/speech/jobs/nope',
                         '/speech/audio/' + '0'*32 + '.wav'):
                self.assertEqual(request(path)[0], 404)
            status, _, _ = request('/speech', 'POST', {'text': 'Ready.', 'request_id': REQUEST_ID},
                                   headers={'Origin': 'https://unrelated.example'})
            self.assertEqual(status, 403)
        self.assertIsNone(self.voice.process)

    def test_diagnostic_instance_preserves_live_streamed_parts(self):
        folder = self.voice.cache / 'stream' / ('a' * 32)
        folder.mkdir(parents=True)
        audio = folder / '0.wav'
        audio.write_bytes(b'owned by the live server')
        diagnostic = LocalSpeech(ROOT, worker_command=['unused-test-worker'],
                                 cache_directory=self.voice.cache)
        try:
            self.assertTrue(audio.is_file())
        finally:
            diagnostic.close()
        self.assertTrue(audio.is_file())


if __name__ == '__main__':
    unittest.main()
