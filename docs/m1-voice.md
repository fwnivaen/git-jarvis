# Local voice on the 8 GB M1

JARVIS now uses a local **M1 voice preview**, conditioned on the user's approved
`briefing.wav`. It uses the existing Qwen3 TTS 0.6B Base 8-bit model through MLX Audio.
Its model weights, audio decoder, reference, sampling options and pitch are unchanged. This is an
alternative engine, and its timbre, accent and delivery require listening approval.
The conversation display calls it **Jarvis**; removing the former “M1 Preview”
label does not establish equivalence to the approved Fish recording. The profile's
approval field and speech status retain the alternative-engine disclosure.
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

One smaller local cloning engine was then tested. Qwen's measured MLX peak
is about 2.7 GB, and its stored model and decoder total about 2 GB. The approved
briefing itself is the conditioning audio. There is no paid synthesis, remote
voice service or automatic fallback to a different speaker. A 4-bit experiment
was faster but was rejected for the active setup because delivery can change.
The latency fix retains the existing 8-bit weights.

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

The M1 profile uses **adaptive buffering**: it measures generation after the first
chunk and starts once the buffered audio can cover the estimated remaining work,
with a playback margin and 20% headroom for slower later batches. This reduces the wait for complete replies while keeping
ordered playback. A sudden workload change can still underrun that estimate.
The native player counts buffer underruns for verification.

At desktop startup, and after browser activation or microphone activation, JARVIS
loads the reference and primes one complete short take, including the final
decoder batch, without playing or caching it. The previous truncated warm-up
left a first-reply cost: a fresh uncached short reply measured 4.61 seconds before
voice start, versus 1.50 seconds after complete priming in this follow-up test.
Reference codec tokens and the exact speaker embedding are reused. The model
stays resident for five minutes, or ten minutes during an active conversation;
new warm requests renew the idle period. The native player also prepares a silent
output graph before a reply, reuses it after normal playback, and releases it after
one minute without use. The model is still unloaded after inactivity on this
8 GB machine. Cold initialization therefore remains a real cost.

The settled galaxy now stops submitting graphics work. Orbit starts off, and
interaction or an explicitly enabled orbit wakes the renderer. The existing
activity poll pauses a requested orbit while the local speech service is busy,
including desktop speech. The orb renders at 10 frames per second while idle
or thinking and 30 during active audio, rather than continuously at 60. This
reduces competition between the dashboard and speech on the shared M1 GPU.

Spoken requests carry a voice flag to the existing chat service. Simple spoken
questions use one low-effort answer/classification call, with brief conversational
phrasing, aiming for one sentence of up to 18 words on simple questions. Requests
for detail remain full answers; text is never cut off by the
player. Self-contained questions omit the tool catalog; explicit action, personal
source and live-information requests retain tools in their first call. The
classifier can request a subsequent tool-enabled turn for an unrecognized tool
need or missing evidence. Complex requests retain the stronger reasoning route and all source,
math, tool and usage checks. Typed requests keep their existing routing.

Before this fix, the complete-buffer profile measured 10.28–12.08 seconds for a
warm uncached short reply and 15.63–85.48 seconds for a cold briefing. An initial
streaming-only attempt still needed 12.81 seconds from a general question to
player start. With the graphics fix and unchanged 8-bit weights, two subsequent
question-to-player starts were 5.79 and 7.46 seconds, including 3.83 and 4.22
seconds waiting for the existing brain. A warm short reply began in 1.47 seconds
and the briefing in 1.83 seconds. Startup priming took 9.97 seconds in the background. All measured replies completed with zero buffer underruns.
These are measured examples, not a guaranteed response time. The final comparison
and exact measurement limits are recorded in
the enclosing project's `artifacts/jarvis-voice-latency-2026-10-31/` directory.
Player starts mean rendered frames, not an acoustic loopback. Cloud response time,
answer length, memory pressure and other desktop work continue to affect latency.
The follow-up five-second target is a target, not a timeout that discards answers
or substitutes a different speaker. Tests and the remaining limits are recorded in
the enclosing project's `artifacts/jarvis-five-second-2026-10-31/` directory.
The final follow-up examples began at 2.03 seconds for the clock, 4.80 seconds for
a calculation, and 6.57–10.44 seconds for general questions, excluding microphone
finalization. The last batch completed without buffer underruns. The five-second
target remains unmet for general conversation; the cloud brain alone took 7.87
seconds on one question. The short sample and briefing are PCM-identical to the
previous 8-bit voice renders.

Generation is bounded at 1,024 Qwen audio tokens and a 180 second job deadline.
A take that reaches the token bound fails and is never cached as complete.
A streamed prefix may already have played; the client reports the voice failure
and keeps the complete text visible.

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

The running worker checks the active configuration, both weight files and the
reference again. It uses an
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

To restore the voice settings from before the latency fix, revert the latency
fix commit. Reverting `e604fcf` as well restores the earlier system voice. Restart
the existing server with its existing launch flags, and rebuild
and relaunch the desktop app. This keeps private application configuration and
user notes in place. A local copy of the previous desktop bundle is also retained
at `.local/voice-rollback-20261031/Jarvis Desktop.app`.

The original package, Fish reconstruction metadata, benchmark comparisons and
measured speech-start timings are retained in the enclosing project's
`artifacts/jarvis-voice-options-2026-10-31/` directory. Historical feasibility
findings precede this implementation; they describe the unchanged original Fish
setup. Perceptual voice approval and physical acoustic-loopback timing remain
user listening checks.
