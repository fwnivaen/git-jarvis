"""Audio-anchored word progress: pauses map to punctuation and text never leads the voice."""
import unittest

import speech_timing as st
from local_speech import valid_timeline

LOUD, QUIET = -20.0, -70.0


def speech(*runs):
    """runs: (seconds, loud?) -> 10 ms frame levels."""
    return [LOUD if loud else QUIET for seconds, loud in runs for _ in range(round(seconds / st.FRAME_S))]


class SpeechTimingTests(unittest.TestCase):
    TEXT = 'Good evening, sir. All systems are running normally.'

    def test_sentence_pause_anchors_the_matching_punctuation(self):
        levels = speech((0.1, False), (1.6, True), (0.4, False), (2.4, True), (0.2, False))
        anchors = st.timeline(self.TEXT, levels, 4.5, True)
        sentence_end = self.TEXT.index('sir.') + 4
        self.assertIn((1.7, round(sentence_end / len(self.TEXT), 4)), anchors)
        self.assertEqual(anchors[-1][1], 1.0)
        self.assertEqual(anchors, sorted(anchors))

    def test_progress_holds_during_a_pause_and_resumes_after(self):
        levels = speech((1.6, True), (0.5, False), (2.4, True))
        anchors = st.timeline(self.TEXT, levels, 4.5, True)
        during = [st.fraction_at(anchors, t, True) for t in (1.7, 1.9, 2.05)]
        self.assertEqual(len(set(during)), 1)
        self.assertGreater(st.fraction_at(anchors, 3.0, True), during[0])

    def test_unfinished_audio_never_claims_the_whole_reply(self):
        levels = speech((1.6, True), (0.4, False), (1.0, True))
        anchors = st.timeline(self.TEXT, levels, 4.5, False)
        self.assertLess(st.fraction_at(anchors, 99, False), 1.0)
        self.assertEqual(st.fraction_at(anchors, 0, False), 0.0)

    def test_silence_or_empty_audio_has_no_anchors(self):
        self.assertEqual(st.timeline(self.TEXT, [], 3, True), [])
        self.assertEqual(st.fraction_at([], 2.0, True), 0.0)

    def test_forced_alignment_maps_words_to_text_positions(self):
        items = [('Good', 0.1, 0.3), ('evening', 0.3, 0.7), ('sir', 0.75, 1.0)]
        anchors = st.forced_anchors(self.TEXT, items, self.TEXT.index('All'))
        self.assertEqual(anchors[0], (0.1, 0.0))
        self.assertEqual(anchors[-1][0], 1.0)
        self.assertAlmostEqual(anchors[-1][1], (self.TEXT.index('sir.') + 4) / len(self.TEXT), places=3)

    def test_mismatched_alignment_is_discarded(self):
        items = [('completely', 0.1, 0.3), ('different', 0.3, 0.7), ('words', 0.75, 1.0)]
        self.assertEqual(st.forced_anchors(self.TEXT, items), [])

    def test_merge_keeps_exact_words_and_only_later_pause_anchors(self):
        forced = [(0.1, 0.0), (1.0, 0.35)]
        pauses = [(0.0, 0.0), (1.2, 0.35), (1.5, 0.35), (3.0, 0.8)]
        merged = st.merge(forced, pauses, self.TEXT, heard_s=3.0)
        self.assertEqual(merged[:2], forced)
        expected = 0.35 + (3.0 - 1.0) / ((1.0 - 0.1) / 0.35 * st.TRAIL_FACTOR)
        final_word = (self.TEXT.rindex(' ') + 1) / len(self.TEXT)
        self.assertAlmostEqual(merged[-1][1], round(min(expected, final_word), 4))
        self.assertEqual(st.merge(forced, pauses, self.TEXT, complete=True), forced)
        self.assertEqual(st.settled_phrase(pauses), (1.2, 0.35))

    def test_progress_beyond_aligned_words_waits_for_the_pause_to_end(self):
        forced = [(0.1, 0.0), (1.0, 0.35)]
        pauses = [(0.0, 0.0), (1.1, 0.35), (1.6, 0.35), (3.0, 0.5)]
        merged = st.merge(forced, pauses, self.TEXT, heard_s=3.0)
        self.assertEqual(st.fraction_at(merged, 1.5, False), 0.35)
        self.assertGreater(st.fraction_at(merged, 2.5, False), 0.35)

    def test_server_rejects_malformed_or_non_monotonic_timelines(self):
        self.assertEqual(valid_timeline([[0, 0], [1.5, 0.4]]), [[0.0, 0.0], [1.5, 0.4]])
        for bad in ([[1, 0.5], [0.5, 0.6]], [[0, 0.5], [1, 0.4]], [[0, 2]], [['a', 0]], [[True, 0]], 'x', []):
            self.assertIsNone(valid_timeline(bad))


if __name__ == '__main__': unittest.main()
