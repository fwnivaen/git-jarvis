# Local voice on the 8 GB M1

JARVIS now uses a local **M1 voice preview**, conditioned on the user's approved
`briefing.wav`. It uses Qwen3 TTS 0.6B Base, 8 bit, through MLX Audio. This is an
alternative engine, and its timbre, accent and delivery require listening approval.
The original Fish profile is preserved verbatim in
`voice-profiles/reference-native-v2.json`. The approved briefing is unchanged.

## Why the engine changed

The supplied Fish S2 Pro profile needs about 7.59 GB of stored tensor data before
generation overhead. Its original runtime recorded more than 13 GB peak model
memory. This Mac has 8 GiB total RAM. The original 15 GiB memory setting was not
used. A bounded test of a compressed version of the same Fish family also proved
impractical: about 121 seconds to produce a 6.5 second briefing. Variants retaining
the original BF16 audio layers stalled. These tests did not establish perceptual
equivalence to the approved recording.

One smaller local cloning engine was then tested. Qwen's measured MLX peak was
about 2.7 GB. Its two weight files total about 1.99 GB. The approved briefing itself
is the conditioning audio. There is no paid synthesis, remote voice service or
automatic fallback to a different speaker.

Primary model and runtime sources:
[Qwen model](https://huggingface.co/mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit),
[Qwen implementation](https://github.com/QwenLM/Qwen3-TTS),
[compressed Fish model](https://huggingface.co/majentik/fishaudio-s2-pro-MLX-4bit),
[MLX voice cloning](https://blaizzy.github.io/mlx-audio/guides/voice-cloning/).

## Playback and text

The response appears as text as soon as the existing brain returns it. Voice
preparation runs separately. Both the browser orb and native desktop app use the
same local speech service. Request ownership, deduplication and targeted Stop
prevent an old reply from playing over a new one. Muting or a voice error leaves
the full text available.

The M1 profile buffers **the complete generated reply** before playback. Live
tests exposed decoder stalls and audible gaps with predictive streaming. Complete
buffering trades a longer speech-start delay for continuous playback. Repeated
identical replies use a checked WAV cache. Warming on submit or push-to-talk can
hide model loading behind the existing brain response. The worker releases the
model after 2–3 minutes without a speech request.

Measured native player starts were 15.63 seconds for a cold briefing, 10.28
seconds for the next uncached short reply, and 0.56 seconds for a cached briefing.
These measure player-reported rendered frames, not acoustic loopback. Other
uncached short examples took 2.23–20.89 seconds to become complete audio under
the observed desktop load. A 36.4 second long reply needed 85.16 seconds to
prepare. This is a memory-compatible preview with noticeable waiting, especially
for long answers; it does not provide real-time conversational speech on this
busy M1. Timing varies with workload and decoder compilation.

A later check run alongside the full regression suite measured 85.48 seconds
for the cold briefing, 12.08 seconds for the uncached short reply and 0.27 seconds
for the cached briefing. Playback still completed continuously. Running other
heavy work on this 8 GB machine can therefore make even a short new reply wait
well over a minute. The earlier, faster results must not be treated as a latency
guarantee.

Generation is bounded at 1,024 Qwen audio tokens and a 180 second job deadline.
A take that reaches the token bound is rejected instead of presenting a cut-off
recording as a complete answer. Text stays visible when those limits are reached.

Output is mono PCM16, 44.1 kHz. Qwen produces 24 kHz audio; one continuous rational
polyphase conversion preserves continuity across generated chunks. The output
rate does not imply 44.1 kHz native bandwidth. A single uniform gain matches the
approved target's speech level, bounded by a true-peak ceiling. There is no pitch
shift, EQ, denoising or voice effect. Highlighting follows estimated pause timing;
word alignment is approximate. No forced-aligner model is loaded.

## Reproduction

Use Apple silicon macOS and Python 3.12. The HTTP server stays standard-library
Python; voice packages live only in an isolated environment.

```sh
python3.12 setup_m1_voice.py --package '/path/to/JARVIS-voice-transfer.zip'
python3.12 setup_m1_voice.py --verify-only
```

The installer reads the package README and checks all package-manifest hashes
before copying the approved briefing. It downloads only pinned voice assets and
checks each file's size and SHA-256 against `voice-profiles/m1-assets.json`.
`requirements-m1-voice.lock` pins the isolated dependencies. Model revision:
`50f45ef0047cde7e84c2ef04326acb8ada2436a7`. The active profile is `voice-profile.json`.
The reference SHA-256 is
`390eba436df7ab3bbc58fc5f60294934cdf2aae36dc028cb0d73301507b59d2c`.

The running worker checks both weight files and the reference again. It uses an
environment containing no application API credentials, offline model loading,
and a macOS sandbox that denies networking. Its memory guideline is 3.5 GiB,
wired limit 3 GiB, and cache limit 128 MiB. System-wide memory settings are
unchanged. These limits are guidelines, not guarantees about total system memory.

## Verification and rollback

Run `python3 preflight.py` against the live server, plus
`node --test tests/local-speech.test.cjs` and
`.local/qwen-voice-candidate/.venv/bin/python tests/check_m1_runtime.py`.
The test suite covers cancellation
before submission, duplicate requests, changed cached audio, opaque audio URLs,
cross-origin rejection, playback ordering and speech failure. The native app is
built using `native/build_desktop.sh`.

The voice replacement is one Git commit. To restore the prior voice, revert that
commit, restart the existing server with its existing launch flags, and rebuild
and relaunch the desktop app. This keeps private application configuration and
user notes in place. A local copy of the previous desktop bundle is also retained
at `.local/voice-rollback-20261031/Jarvis Desktop.app`.

The original package, Fish reconstruction metadata, benchmark comparisons and
measured speech-start timings are retained in the enclosing project's
`artifacts/jarvis-voice-options-2026-10-31/` directory. Historical feasibility
findings precede this implementation; they describe the unchanged original Fish
setup. Perceptual voice approval and physical acoustic-loopback timing remain
user listening checks.
