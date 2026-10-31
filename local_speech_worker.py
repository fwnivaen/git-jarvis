"""Resident offline M1 voice preview; the original Fish profile stays archived.

JSON lines are the only stdout protocol. A reader thread handles targeted
cancellation while MLX generates; cancellation is checked at acoustic frames.
Dependencies live in the existing voice pilot, never in the stdlib HTTP server.
"""
import argparse
import contextlib
import gc
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import re
import sys
import tempfile
import threading
import time

from speech_plan import delivery_for, spoken_text, synthesis_text
import speech_timing

MAX_PACKET_BYTES = 65536
MAX_CANCELLED_IDS = 128
FRAME_PROGRESS_INTERVAL = 32
MAX_AUDIO_SECONDS = 120
LONG_TEXT_BYTES = 400
SHORT_TEXT_BYTES = 1000
SAFE_EDGE_FADE_S = .003
DEFAULT_SAMPLE_RATE = 44100
# Qwen generates in native chunks with continuous resampling. The M1 profile
# buffers a complete reply: cold decoder compilation can otherwise underrun.
STREAM_CHUNK_S = 0.5
SPOKEN_CHARS_PER_S = 16.0     # conservative initial estimate; generation rate is measured per reply
DIGIT_EXTRA_CHARS = 4         # digits are read out as words
PLAYBACK_MARGIN_S = 0.8
GAIN_WINDOW_S = 0.9           # the uniform level is set from this fixed opening, so a take always gets the same gain
ALIGNER_DIRECTORY = 'models/qwen3-forced-aligner-0.6b-8bit'   # mlx-community/Qwen3-ForcedAligner-0.6B-8bit @ 0e1a68e9
ALIGNER_SHA256 = 'be19ef8ac4326d032e7673342930b14c2df30bd68c1632493b0f563e30829f91'
ALIGNER_RATE = 16000
ALIGNED_END_TOLERANCE_S = 0.6   # an aligned prefix must end at its settled pause, or it is discarded
MAX_ALIGNED_CHARS_PER_S = 25.0  # about 1.4x this voice's measured speaking rate
ALIGN_CHARS_PER_S = 17.5      # measured speaking rate, for word-progress alignment only
OPENING_BIAS_DB = 0.0         # no opening-level correction for the M1 preview
PROTOCOL_STDOUT = sys.stdout


class Cancelled(Exception):
    pass


def emit(job_id, state, **values):
    print(json.dumps({'id': job_id, 'state': state, **values}), file=PROTOCOL_STDOUT, flush=True)


class Engine:
    def __init__(self, profile, pilot, cache, cancelled):
        self.profile, self.pilot, self.cache = profile, pilot, cache
        self.cancelled = cancelled
        self.model = None
        self.ready = False
        self.current_id = None
        self.frames = 0

    def check(self):
        if self.cancelled(self.current_id):
            raise Cancelled()

    def load(self):
        if self.ready:
            return
        self.check()
        import mlx.core as mx
        import numpy as np
        import soundfile as sf
        from m1_voice_runtime import load_voice, speech_rms
        self.mx, self.np, self.sf = mx, np, sf
        self.speech_rms = speech_rms
        self.model, wave = load_voice(self.pilot, self.profile, self.check)
        self.reference = None
        self.rate = DEFAULT_SAMPLE_RATE
        self.target_level = speech_rms(wave, self.rate)
        self.aligner = None
        self.ready = True
        self.check()

    def load_aligner(self):
        """Optional word-level aligner. Missing or changed weights fall back to pause timing."""
        folder = self.pilot / ALIGNER_DIRECTORY
        try:
            with (folder / 'model.safetensors').open('rb') as handle:
                if hashlib.file_digest(handle, 'sha256').hexdigest() != ALIGNER_SHA256:
                    return None
            from mlx_audio.stt import load as load_stt
            return load_stt(str(folder))
        except Exception:
            return None

    def align(self, audio, text):
        """[(word, start, end)] for 44.1 kHz audio, or None."""
        from scipy.signal import resample_poly
        try:
            result = self.aligner.generate(audio=resample_poly(audio, 160, 441).astype('float32'), text=text, language='English')
            return [(item.text, float(item.start_time), float(item.end_time)) for item in result]
        except Exception:
            return None

    def render(self, packet):
        started = time.monotonic()
        self.current_id = packet['id']
        self.frames = 0
        emit(self.current_id, 'loading')
        with contextlib.redirect_stdout(sys.stderr):
            self.load()
        self.check()
        loaded = time.monotonic()
        p, np, sf, rate = self.profile, self.np, self.sf, self.rate
        complete_buffer = p.get('playback_mode', 'complete') == 'complete'
        from scipy.signal import resample_poly
        text = spoken_text(packet['text'])
        plan = delivery_for(text, packet.get('delivery'))
        prepared = text  # Qwen Base has no Fish style tags or acoustic-temperature control.
        self.mx.random.seed(p['seed'])
        emit(self.current_id, 'generating')
        align_estimate = (len(text) + DIGIT_EXTRA_CHARS * sum(ch.isdigit() for ch in text)) / ALIGN_CHARS_PER_S
        estimate = (len(text) + DIGIT_EXTRA_CHARS * sum(ch.isdigit() for ch in text)) / SPOKEN_CHARS_PER_S
        parts_dir = self.cache / 'stream' / packet['id']
        parts_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        fade = round(SAFE_EDGE_FADE_S * rate)
        pending, published, held = [], [], None
        state = {'gain': None, 'first_audio': None, 'gain_reductions': 0, 'generated': 0, 'first_chunk': None,
                 'aligned_fraction': 0.0, 'forced': [], 'aligned_words': 0}

        def publish(piece):
            # One uniform gain from the buffered prefix; the peak guard can
            # only lower it for the remainder, never raise it mid-reply.
            if not complete_buffer:
                peak = float(np.max(np.abs(resample_poly(piece * state['gain'], p['peak_oversampling'], 1))))
                if peak > p['true_peak_limit']:
                    state['gain'] *= p['true_peak_limit'] / peak
                    state['gain_reductions'] += 1
            finished = (piece * state['gain']).astype('float64')
            if not np.isfinite(finished).all():
                raise ValueError('Invalid finished audio.')
            index = len(published)
            temporary = parts_dir / ('.%d.wav' % index)
            sf.write(temporary, finished, rate, subtype='PCM_16')
            temporary.chmod(0o600)
            os.replace(temporary, parts_dir / ('%d.wav' % index))
            published.append(finished)
            if state['first_audio'] is None:
                state['first_audio'] = time.monotonic() - started
            emit(self.current_id, 'streaming', parts=len(published),
                 audio_seconds=sum(len(part) for part in published) / rate, estimated_seconds=estimate,
                 timeline=progress(False))

        def progress(complete, align=False):
            # Word progress anchored to the real pauses in what has been published.
            audio = np.concatenate(published)
            hop = round(speech_timing.FRAME_S * rate)
            frames = len(audio) // hop
            if not frames:
                return []
            power = np.mean(audio[:frames * hop].reshape(frames, hop) ** 2, axis=1)
            levels = (10 * np.log10(power + 1e-12)).tolist()
            pauses = speech_timing.timeline(text, levels, align_estimate, complete)
            if self.aligner is None or not align:
                return speech_timing.merge(state['forced'], pauses, text, len(audio) / rate,
                                           complete and state['aligned_fraction'] >= 1.0)
            # Exact word times for the settled part: align the audio up to the last
            # confirmed phrase end (or all of it once complete) against that text.
            if complete:
                target = (len(audio) / rate, 1.0)
            else:
                target = speech_timing.settled_phrase(pauses)
            if target and target[1] > state['aligned_fraction']:
                seconds, fraction = target
                cut = min(len(text), round(fraction * len(text)))
                # Only the new stretch since the last aligned word: constant cost per phrase.
                previous = state['forced'][-1] if state['forced'] else (0.0, 0.0)
                begin_s, begin_c = previous[0], round(previous[1] * len(text))
                items = self.align(audio[round(begin_s * rate):round(min(len(audio) / rate, seconds + 0.1) * rate)], text[begin_c:cut])
                items = [(word, start + begin_s, end + begin_s) for word, start, end in items or []]
                forced = speech_timing.forced_anchors(text, items, cut, begin_c)
                # A forced aligner always fits the text it is given; reject a result that
                # claims more words than could physically be spoken since the last aligned point.
                plausible = forced and (forced[-1][1] - previous[1]) * len(text) <= MAX_ALIGNED_CHARS_PER_S * max(0.0, forced[-1][0] - previous[0])
                if plausible and (complete or abs(forced[-1][0] - seconds) <= ALIGNED_END_TOLERANCE_S):
                    state['forced'] = state['forced'] + [anchor for anchor in forced if anchor[0] >= previous[0] and anchor[1] >= previous[1]]
                    state.update(aligned_fraction=fraction, aligned_words=len(state['forced']) // 2)
            return speech_timing.merge(state['forced'], pauses, text, len(audio) / rate, complete and bool(state['forced']) and state['aligned_fraction'] >= 1.0)

        def begin(prefix):
            audio = np.concatenate(prefix).astype('float64')
            if not complete_buffer:
                audio = audio[:round(GAIN_WINDOW_S * rate)]
            gain = self.target_level / self.speech_rms(audio, rate)
            if not complete_buffer and len(audio) >= round(GAIN_WINDOW_S * rate):
                gain *= 10 ** (OPENING_BIAS_DB / 20)
            peak = float(np.max(np.abs(resample_poly(audio * gain, p['peak_oversampling'], 1))))
            state['gain'] = gain * min(1.0, p['true_peak_limit'] / peak)

        def playable():
            # The steady rate is measured after the first chunk, excluding the
            # one-off prompt prefill. Remaining generation time must fit inside
            # the buffered plus still-to-come playback, with a margin.
            at, first = state['first_chunk']
            produced = (state['generated'] - first) / rate
            if produced <= 0:
                return False
            generated = state['generated'] / rate
            if generated < GAIN_WINDOW_S:
                return False
            remaining = max(0.0, estimate - generated)
            seconds_per_audio = (time.monotonic() - at) / produced
            return remaining * seconds_per_audio <= generated + remaining - PLAYBACK_MARGIN_S

        # Short approved replies stay one continuous render. Native long-form
        # batching retains the model's conversation and speaker context.
        budget = LONG_TEXT_BYTES if len(prepared.encode()) > SHORT_TEXT_BYTES else p['chunk_length']
        segment = 0
        # A chunk is held back only when a later segment join may need its tail faded.
        joins = len(self.model._split_generation_text(prepared, budget)) > 1
        with contextlib.redirect_stdout(sys.stderr):
            for result in self.model.generate(text=prepared, ref_audio=self.reference,
                    ref_text=p['reference_text'], instruct=p['instruct'],
                    temperature=p['temperature'], top_p=p['top_p'], top_k=p['top_k'],
                    max_tokens=p['max_tokens'], chunk_length=budget, stream=True,
                    streaming_interval=STREAM_CHUNK_S):
                self.check()
                self.mx.eval(result.audio)
                piece = np.array(result.audio, dtype=np.float32).reshape(-1)
                if not len(piece):
                    continue
                if not np.isfinite(piece).all():
                    raise ValueError('Invalid speech.')
                state['generated'] += len(piece)
                if state['first_chunk'] is None:
                    state['first_chunk'] = (time.monotonic(), state['generated'])
                if state['generated'] / rate > MAX_AUDIO_SECONDS:
                    raise ValueError('Reply exceeds voice duration limit.')
                if held is not None and result.segment_idx != segment and len(held) >= fade and len(piece) >= fade:
                    # The same 3 ms edge fade as before, only at native segment joins.
                    held[-fade:] *= np.linspace(1, 0, fade)
                    piece[:fade] *= np.linspace(0, 1, fade)
                segment = result.segment_idx
                if held is not None:
                    pending.append(held)
                held = piece
                if not joins:
                    pending.append(held)
                    held = None
                if not complete_buffer and state['gain'] is None and pending and playable():
                    begin(pending + ([held] if held is not None else []))
                if state['gain'] is not None:
                    for part in pending:
                        publish(part)
                    if pending:
                        # Parts are announced first; word timing is refined afterwards.
                        emit(self.current_id, 'streaming', parts=len(published),
                             audio_seconds=sum(len(part) for part in published) / rate,
                             estimated_seconds=estimate, timeline=progress(False, align=True))
                    pending = []
        self.check()
        generated = time.monotonic()
        if held is not None:
            pending.append(held)
        if not pending and not published:
            raise ValueError('No speech generated.')
        if not any(np.any(part) for part in pending + published):
            raise ValueError('No speech generated.')
        emit(self.current_id, 'finishing')
        if state['gain'] is None:
            # Finished before playback was due: identical to the whole-file recipe.
            begin(pending)
        for part in pending:
            publish(part)
        audio = np.concatenate(published)
        self.check()
        with tempfile.TemporaryDirectory(prefix='render-', dir=self.cache) as scratch:
            scratch = Path(scratch)
            target = self.cache / (packet['key'] + '.wav')
            temporary = scratch / 'final.wav'
            sf.write(temporary, audio, rate, subtype='PCM_16')
            metrics = {'audio_seconds': len(audio) / rate, 'model_load_seconds': loaded - started,
                       'generation_seconds': generated - loaded,
                       'finishing_seconds': time.monotonic() - generated,
                       'total_seconds': time.monotonic() - started,
                       'first_audio_seconds': state['first_audio'], 'stream_parts': len(published),
                       'first_model_chunk_seconds': state['first_chunk'][0] - started,
                       'playback_mode': p.get('playback_mode', 'complete'),
                       'timeline': progress(True, align=True), 'aligned_words': state['aligned_words'],
                       'word_alignment': self.aligner is not None,
                       'estimated_seconds': estimate, 'gain_reductions': state['gain_reductions'],
                       'sample_rate': rate, 'uniform_gain': float(state['gain']),
                       'pitch_shift_semitones': 0, 'effects_applied': [],
                       'peak_memory_gb': self.mx.get_peak_memory() / 1e9,
                       'voice_status': 'M1 reference-conditioned preview', 'engine': p['engine']}
            metadata = {'fingerprint': packet['fingerprint'], 'sha256': hashlib.sha256(temporary.read_bytes()).hexdigest(),
                        'metrics': metrics, 'delivery': plan}
            temporary.chmod(0o600)
            os.replace(temporary, target)
            meta = scratch / 'metadata.json'
            meta.write_text(json.dumps(metadata))
            meta.chmod(0o600)
            os.replace(meta, target.with_suffix('.json'))
        self.mx.clear_cache()
        return metrics


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', required=True)
    parser.add_argument('--pilot', required=True)
    parser.add_argument('--cache', required=True)
    args = parser.parse_args()
    profile = json.loads(Path(args.profile).read_text())
    cache = Path(args.cache)
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    packets = queue.Queue()
    cancelled = set()
    lock = threading.Lock()
    closed = threading.Event()

    def is_cancelled(job_id):
        with lock:
            return closed.is_set() or job_id in cancelled

    def receive():
        try:
            while True:
                line = sys.stdin.readline(MAX_PACKET_BYTES + 1)
                if not line or len(line) > MAX_PACKET_BYTES:
                    return
                try:
                    packet = json.loads(line)
                    if not isinstance(packet, dict) or not re.fullmatch(r'[a-f0-9]{32}', packet.get('id', '')):
                        continue
                    if packet.get('op') == 'cancel':
                        with lock:
                            if len(cancelled) >= MAX_CANCELLED_IDS:
                                cancelled.pop()
                            cancelled.add(packet['id'])
                    elif packet.get('op') == 'warm':
                        packets.put(packet)
                    elif packet.get('op') == 'render' and re.fullmatch(r'[a-f0-9]{64}', packet.get('key', '')):
                        packets.put(packet)
                except (ValueError, TypeError):
                    continue
        finally:
            closed.set()
            packets.put(None)

    threading.Thread(target=receive, daemon=True).start()
    engine = Engine(profile, Path(args.pilot), cache, is_cancelled)
    while True:
        packet = packets.get()
        if packet is None:
            break
        if packet.get('op') == 'warm':
            # Load ahead of the first reply (hotkey press / submit). No output audio.
            try:
                engine.current_id = packet['id']
                with contextlib.redirect_stdout(sys.stderr):
                    engine.load()
            except Exception:
                if not engine.ready:
                    engine.model = None
                    gc.collect()
            continue
        try:
            if is_cancelled(packet['id']):
                raise Cancelled()
            metrics = engine.render(packet)
            if is_cancelled(packet['id']):
                raise Cancelled()
            emit(packet['id'], 'ready', metrics=metrics)
        except Cancelled:
            emit(packet['id'], 'cancelled')
        except Exception:
            # Never return arbitrary model exceptions, paths, prompts or bodies.
            if not engine.ready:
                engine.model = None
                gc.collect()
            emit(packet['id'], 'failed')
        finally:
            with lock:
                cancelled.discard(packet['id'])
            if engine.model is not None:
                engine.mx.clear_cache()


if __name__ == '__main__':
    main()
