"""Run with the isolated voice Python; synthetic arrays, no model weights."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from types import SimpleNamespace
import unittest

import mlx.core as mx
import numpy as np
from scipy.signal import resample_poly

from m1_voice_runtime import ContinuousResampler, QwenVoice


class RuntimeChecks(unittest.TestCase):
    def test_uneven_streamed_joins_equal_one_continuous_conversion(self):
        audio = np.random.default_rng(42).uniform(-.8, .8, 24001).astype(np.float32)
        resampler = ContinuousResampler()
        pieces, start = [], 0
        for stop in (1, 14, 50, 899, 7600, 7601, 12000, 22001, 24001):
            pieces.append(resampler.feed(audio[start:stop]))
            start = stop
        pieces.append(resampler.feed(final=True))
        np.testing.assert_array_equal(np.concatenate(pieces), resample_poly(audio, 147, 80))

    def test_token_bound_cannot_finish_as_a_complete_reply(self):
        class FakeVoice:
            def generate(self, **_options):
                for _ in range(2):
                    yield SimpleNamespace(audio=mx.zeros(240), token_count=2)
        profile = dict(reference_text='Reference.', temperature=.9, top_p=1., top_k=50,
                       repetition_penalty=1.05, max_tokens=4, stream_chunk_s=.5)
        voice = QwenVoice(FakeVoice(), None, profile, lambda: None)
        with self.assertRaisesRegex(ValueError, 'length bound'):
            list(voice.generate('A reply that must not silently end early.'))


if __name__ == '__main__':
    unittest.main()
