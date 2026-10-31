"""Speech ownership, cache integrity and HTTP boundaries; no models or network."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

from local_speech import LocalSpeech
from local_speech_worker import Engine, playback_buffer_ready
from m1_voice_runtime import cache_reference_speaker
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


class SpeechStartupTests(unittest.TestCase):
    def test_warming_primes_audio_once_without_rendering_a_reply(self):
        engine = Engine({}, Path('.'), Path('.'), lambda _id: False)
        engine.ready = True
        from unittest.mock import Mock
        engine.model = Mock()
        engine.warm()
        engine.warm()
        engine.model.warm.assert_called_once_with()
        self.assertTrue(engine.primed)

    def test_reference_speaker_vector_is_identical_and_other_audio_is_not_reused(self):
        from unittest.mock import Mock
        reference, other, vector = object(), object(), object()
        base = Mock()
        extract = base.extract_speaker_embedding
        extract.return_value = vector
        cache_reference_speaker(base, reference)
        self.assertIs(base.extract_speaker_embedding(reference), vector)
        self.assertIs(base.extract_speaker_embedding(reference), vector)
        extract.assert_called_once_with(reference)
        base.extract_speaker_embedding(other)
        self.assertEqual(extract.call_count, 2)
        extract.assert_called_with(other)

    def test_slow_generation_keeps_a_buffer_and_fast_generation_can_start_early(self):
        self.assertFalse(playback_buffer_ready(.96, 0, 0, 8))
        self.assertFalse(playback_buffer_ready(1.92, .96, 1.44, 8))
        self.assertFalse(playback_buffer_ready(3.84, 2.88, 4.32, 8))
        self.assertTrue(playback_buffer_ready(4.8, 3.84, 5.76, 8))
        self.assertTrue(playback_buffer_ready(1.92, .96, .6, 8))
        # The opening of a real 6 s take ran at 0.94x, then slowed to 1.32x.
        # One second buffered was insufficient; retain another batch first.
        self.assertFalse(playback_buffer_ready(1, .5, .47, 6))
        self.assertTrue(playback_buffer_ready(2, 1.5, 1.48, 6))


if __name__ == '__main__':
    unittest.main()
