"""Pinned M1 preview. Qwen clones the approved briefing; it is not the Fish baseline.

Runs only inside the isolated offline worker. Sampling uses Qwen's own controls.
No system voice, remote host, or provider fallback is selected here.
"""
import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace

GIB = 1024**3
MIB = 1024**2
OUTPUT_RATE = 44100
NATIVE_RATE = 24000
RESAMPLE_HOLD_SAMPLES = 64
RMS_FRAME_S = .02
RMS_QUIET_FRACTION = .08
RMS_QUANTILE = .95
WARM_TEXT = 'Good evening, sir. Your local voice is ready.'
WARM_AUDIO_TOKENS = 36       # prime initial and continuation decoder batches; never played or saved
MODEL_REVISION = '50f45ef0047cde7e84c2ef04326acb8ada2436a7'
MODEL_DIRECTORY = 'model'
MODEL_ASSETS = {
    'model/config.json': 'b404fb6f99dac6f7a81f3a03af5675b131ecf9936ac75a7c9d75e54044600044',
    'model/model.safetensors': '9488e7005cc0cf44f8804eb543668d0763bb1c649ce6f1eddc663519524b3182',
    'model/speech_tokenizer/model.safetensors': '836b7b357f5ea43e889936a3709af68dfe3751881acefe4ecf0dbd30ba571258',
}


def speech_rms(audio, rate):
    import numpy as np
    frame = max(1, round(rate * RMS_FRAME_S))
    padded = np.pad(audio, (0, (-len(audio)) % frame))
    energy = np.sqrt(np.mean(padded.reshape(-1, frame).astype(np.float64)**2, axis=1))
    mask = np.repeat(energy > RMS_QUIET_FRACTION * np.quantile(energy, RMS_QUANTILE), frame)[:len(audio)]
    if not mask.any():
        raise ValueError('No speech energy')
    return float(np.sqrt(np.mean(audio[mask].astype(np.float64)**2)))


class ContinuousResampler:
    """Emit settled samples from one cumulative polyphase conversion.

    The tail is retained until future input settles the FIR boundary. Joining
    emitted pieces equals a single conversion of the whole native waveform.
    This avoids independent resampling filters at every streamed chunk join.
    """
    def __init__(self):
        self.parts = []
        self.emitted = 0

    def feed(self, audio=None, *, final=False):
        import numpy as np
        from scipy.signal import resample_poly
        if audio is not None and len(audio):
            self.parts.append(np.asarray(audio, dtype=np.float32))
        if not self.parts:
            return np.empty(0, dtype=np.float32)
        converted = resample_poly(np.concatenate(self.parts), OUTPUT_RATE // math.gcd(OUTPUT_RATE, NATIVE_RATE),
                                  NATIVE_RATE // math.gcd(OUTPUT_RATE, NATIVE_RATE))
        end = len(converted) if final else max(self.emitted, len(converted)-RESAMPLE_HOLD_SAMPLES)
        result = converted[self.emitted:end].copy()
        self.emitted = end
        return result


def verify_assets(pilot, profile, check):
    if profile['model_revision'] != MODEL_REVISION or profile['model_directory'] != MODEL_DIRECTORY:
        raise ValueError('Voice model revision changed')
    for relative, expected in {**MODEL_ASSETS, profile['reference']: profile['reference_sha256']}.items():
        check()
        with (pilot / relative).open('rb') as source:
            if hashlib.file_digest(source, 'sha256').hexdigest() != expected:
                raise ValueError('Voice asset changed')
    check()


class QwenVoice:
    sample_rate = OUTPUT_RATE

    def __init__(self, base, reference, profile, check):
        self.base, self.reference, self.profile, self.check = base, reference, profile, check

    def _split_generation_text(self, text, _budget):
        return [text]

    def warm(self):
        """Prime generation and the decoder before a real reply needs audio."""
        import mlx.core as mx
        p = self.profile
        for result in self.base.generate(text=WARM_TEXT, ref_audio=self.reference,
                ref_text=p['reference_text'], lang_code='english',
                temperature=p['temperature'], top_p=p['top_p'], top_k=p['top_k'],
                repetition_penalty=p['repetition_penalty'], max_tokens=WARM_AUDIO_TOKENS,
                stream=True, streaming_interval=p['stream_chunk_s'], verbose=False):
            self.check()
            mx.eval(result.audio)
        self.check()

    def generate(self, text, **_fish_arguments):
        import mlx.core as mx
        import numpy as np
        p = self.profile
        resampler = ContinuousResampler()
        generated_tokens = 0
        # These are explicitly the pinned Qwen generation options, not a mapping
        # of Fish semantic/acoustic temperatures to another engine.
        for result in self.base.generate(text=text, ref_audio=self.reference, ref_text=p['reference_text'],
                lang_code='english', temperature=p['temperature'], top_p=p['top_p'], top_k=p['top_k'],
                repetition_penalty=p['repetition_penalty'], max_tokens=p['max_tokens'],
                stream=True, streaming_interval=p['stream_chunk_s'], verbose=False):
            self.check()
            generated_tokens += int(result.token_count)
            mx.eval(result.audio)
            audio = np.array(result.audio, dtype=np.float32).reshape(-1)
            if not np.isfinite(audio).all():
                raise ValueError('Invalid voice waveform')
            settled = resampler.feed(audio)
            if len(settled):
                yield SimpleNamespace(audio=mx.array(settled), segment_idx=0)
        if generated_tokens >= p['max_tokens']:
            # Never cache a length-bound take as a complete answer. A streamed
            # prefix may already have played; the client reports the voice error.
            raise ValueError('Voice generation reached its length bound')
        tail = resampler.feed(final=True)
        if len(tail):
            yield SimpleNamespace(audio=mx.array(tail), segment_idx=0)


def cache_reference_speaker(base, reference):
    """Reuse the exact speaker vector for this immutable approved reference.

    MLX Audio caches reference codec tokens, but computes the speaker encoder
    again on every reply. Identity checking keeps other audio on its normal path.
    """
    extract = base.extract_speaker_embedding
    speaker = extract(reference)
    base.extract_speaker_embedding = lambda audio: speaker if audio is reference else extract(audio)


def load_voice(pilot, profile, check):
    import mlx.core as mx
    import numpy as np
    import soundfile as sf
    from scipy.signal import resample_poly
    from mlx_audio.tts.utils import load_model
    pilot = Path(pilot)
    verify_assets(pilot, profile, check)
    mx.set_memory_limit(int(profile['memory_limit_gib'] * GIB))
    mx.set_cache_limit(int(profile['cache_limit_mib'] * MIB))
    mx.set_wired_limit(min(int(profile['wired_limit_gib'] * GIB),
                          int(mx.device_info()['max_recommended_working_set_size'])))
    base = load_model(str(pilot / profile['model_directory']), strict=True)
    if base.sample_rate != NATIVE_RATE:
        raise ValueError('Unexpected native voice rate')
    wave, rate = sf.read(pilot / profile['reference'], dtype='float32', always_2d=True)
    if wave.shape[1] != 1 or rate != OUTPUT_RATE:
        raise ValueError('Unexpected approved reference format')
    wave = wave[:, 0]
    reference = mx.array(resample_poly(wave, NATIVE_RATE // math.gcd(rate, NATIVE_RATE),
                                      rate // math.gcd(rate, NATIVE_RATE)).astype(np.float32))
    original_sample = base._sample_token

    def cancellable_sample(*args, **kwargs):
        check()
        return original_sample(*args, **kwargs)

    base._sample_token = cancellable_sample
    cache_reference_speaker(base, reference)
    # Cache the reference codes before any reply. No acknowledgement or other
    # speech is generated by warming, and no microphone is opened.
    prepared = base._prepare_icl_generation_inputs(text=WARM_TEXT, ref_audio=reference,
            ref_text=profile['reference_text'], language='english')
    mx.eval(*prepared)
    check()
    return QwenVoice(base, reference, profile, check), wave
