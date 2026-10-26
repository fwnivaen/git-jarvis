# Jarvis voice and Island implementation record — 2026-10-26

This is the current record for the isolated `codex/jarvis-voice-island` branch. Older handoffs describe earlier machines and revisions; their measurements are historical. The running checkout was left on `main` and its server was not replaced.

## Reconciliation

The actual repository is `fwnivaen/Jarvis`, with `server.py` listening on loopback port 4700. The original checkout was clean before and after verification. It already had a standard-library HTTP server, Codex CLI model route, Markdown vault, SQLite FTS5 and optional local semantic search, 3D galaxy, browser voice helper, Work workflows, checkpoints, bounded tool journal, native screen/notes controls, and an explicit permission model for its existing sensors. Focus is deliberately removed: `/focus` returned 404. It did not have a native menu-bar conversation app or Island, an `ultra_tasks.py`, or `/stage2/tasks`; the latter returned 404. Existing workflow status is available from `/workflows`.

The discovered Mac is a MacBookPro17,1 with an Apple M1 chip, 8 GB memory and a 1280×800 display. Its screen reports a zero-point top safe-area inset, so the live Island uses the flat pill layout. No hardware detail from an imported handoff was assumed.

Private `config.json` remains ignored and stayed in the original checkout. The new worktree has no copied config or Codex credentials. No API key is needed for the normal signed-in CLI path. The local server source now identifies Nivaen Giritharan, New York and `America/New_York`, with a daylight-saving-aware capture timestamp. Its speech still addresses the user as Sir.

## Isolated implementation

- `native/build_desktop.sh` builds an ad hoc signed menu-bar app with the installed Command Line Tools. The bundle belongs to `local.fwnivaen.jarvis.desktop` and stays under this worktree's ignored `.local/` directory. No login item or LaunchAgent was registered.
- The menu has a read-only voice-permission check. `--check-permissions` reports the same statuses without prompting or opening the microphone. `--measure-mic` takes two short in-memory audio samples and reports first-buffer latency and meter activity. `--check-hotkeys` reports registration and any delivered key events without opening the microphone. Demo mode accepts `JARVIS_ISLAND_DEMO_STATE` to hold one named state and `JARVIS_ISLAND_DEMO_HOLD` to exit after a chosen number of seconds when the executable is launched directly.
- Hold Control–Shift–Space for speech, use Option–Escape to cancel, and Option–Command–M to mute. Plain Escape is registered only during an active voice operation. `--ask` and `say` are available. Normal mode was temporarily launched and closed for visual checks.
- One microphone tap drives on-device transcription and the Island meter. The new app uses installed `SpeechTranscriber` assets when present and falls back to `SFSpeechRecognizer` with on-device recognition required. The enhanced English model is supported on this Mac but was not installed; the menu offers an explicit download action. Spoken replies use `AVSpeechSynthesizer` with a British voice preference. Muted replies advance text at 180 words per minute.
- The SwiftUI Island implements the active, approval, transient, notification, ready and hidden priority model; answer/failure/status classification; UTF-16 word synchronization; notch and flat pill layouts; a runtime Metal orb; a Canvas fallback; reduced-motion response; and a non-activating panel across Spaces. It can be disabled in the menu. Snapshot mode does not register hotkeys, open the microphone or call the server.
- The desktop watcher polls the **existing** `/workflows` endpoint at three seconds while busy and thirty seconds while idle. It shows a background “Working” state from verified workflow activity, ignores history on its first poll and deduplicates terminal transitions. Visual completion notices are on by default. “Speak background updates” is opt-in in the menu; when enabled, summaries wait behind an active conversation and respect mute. The running server has no general approval queue, so the Island's approval state is implemented in the view model but has no live task approval source.
- `voice_bridge.py` and the new Swift helper share a per-user advisory microphone lock. This prevents two *updated* helpers from recording together after deployment. The currently running server has not loaded this change.

The implementation is a useful local voice/Island slice of the attached vision. It does not establish that all of the requested assistant capabilities are complete. The [feature catalog and roadmap](voice-island-catalog.md) records every main capability class and its next gate.

## Verification and limits

| Check | Result | Limit |
| --- | --- | --- |
| Baseline original repository hygiene | 239 files, 187 links, 0 failures | Source hygiene only. |
| Baseline Python regression | 268 run, 32 skipped, 0 failures | Before isolated changes. |
| Approved full preflight against original running server | 14 pass, 0 fail, 2 warn | The warnings are expected API-provider skips for the active Codex route. It made model calls and created/recalled/cleaned one temporary note. Original Git status remained clean. |
| New native `swiftc` build and ad hoc signature | Passed with no compiler warnings | Not activated as the normal voice helper. |
| Island state checks | 303 passed | Pairwise active/error transitions, passive work priority, queue expiry and UTF-16 mappings; no live window timing proof. |
| App `--check-island` | Passed | Compiles the Metal shader at runtime; does not prove every live transition. |
| Static snapshots | 30 images, notch and flat pill | [Notch speaking](screenshots/island/notch-speaking.png) and [flat pill listening](screenshots/island/pill-listening.png); static captures omit live Metal and any Core Animation effects. |
| Cross-language microphone lock | Python-held lock blocked the Swift app; release restored access | Tested in the isolated build. Running server still has its original module loaded. |
| Targeted Python checks | 3 passed | Lock, symlink refusal, and New York winter/summer offsets. |
| Changed-branch regression suite | 271 Python tests run, 39 skipped, 0 failures; all 12 JavaScript test files passed | Skips reflect private config and optional packages deliberately absent from the isolated worktree. |
| Changed-branch repository check | 263 tracked files, 206 local links, 0 failures | Run after staging the new source files. |
| Temporary normal app launch | Started and stopped; original server remained on port 4700 | No microphone, hotkey or spoken request was triggered during an ongoing call. |
| Voice permissions, read-only | Microphone allowed; speech recognition not requested | No permission prompt or recording. |
| Physical microphone input | First buffer: 183 ms on the first start and 178 ms on the subsequent start; 20 buffers in each two-second capture | `--measure-mic` held the cross-process lock; normalized peak meter was 0.10 and 0.01. No audio or transcript was saved. This measures input buffers, not end-to-end recognition. |
| Global shortcut registration | Control–Shift–Space registered successfully | A synthetic key sent by the desktop automation did not produce a hotkey event; physical keyboard acceptance remains open. |
| Spoken request path | `--ask` exited normally after 15.1 seconds | It exercised the local request and speech-completion path, but audible output was not independently confirmed. |
| Live flat-pill window | Onscreen at 466×76, top center; level 27, sharing state 0 | `CGWindowListCopyWindowInfo` confirms the configured window property, not actual screen-share exclusion. |
| Foreground focus | Foreground app unchanged during the speaking-state visual demo | Other Spaces/full-screen behavior remains untested. |
| Visual-demo CPU, ten-second cumulative time | Ready: 0.34 CPU seconds / 10.02 seconds = 3.39%; speaking layout: 0.50 / 10.02 = 4.99% | M1 Mac, native display, no microphone or speech synthesis. This is visual-render cost, not end-to-end speaking cost. |

End-to-end recognition, physical hotkey delivery, audible speaker output, external display, full-screen/Spaces, Stage Manager, VoiceOver, screen-share exclusion, and click-through acceptance remain open. The first call prevented audio checks; after it ended, microphone buffers and a `--ask` request were measured. Speech permission remains unrequested. The CPU values above cover visual states only. The bundle uses ad hoc signing; `security find-identity` found zero valid code-signing identities on this Mac. Stable signing would need an identity supplied later, and login startup needs a separate approved activation step. No wake word or continuous microphone listener is enabled.

## Identity and privacy audit

The owner remote and active README links point to `fwnivaen/Jarvis`. The application identifier uses `fwnivaen`; the server persona and timezone match the supplied details. The verified authenticated GitHub profile supplies the exact commit email, used only for this branch's commit commands. Config, vault captures, model assets and `.local` runtime artifacts remain ignored. The user later supplied the friend's name; known name, account, bundle and home-path identifiers were searched in the source mirror, both checkout trees, ignored config, app bundles, Git author metadata and startup-registration names with zero matches. The friend’s identifiers are intentionally absent from this document. Active source/docs/test scans showed third-party GitHub links and a synthetic test credential; these are attribution/fixtures, not copied owner information. Archived documents remain historical and were not rewritten. This is a zero-match result for the known identifiers in the audited scope, not proof about unknown aliases or data outside it.

## Review, activation and rollback

Review this branch and its two static previews before activation. To roll back the proposed desktop slice, discard this isolated worktree/branch; the original checkout and running process are untouched. If the branch is later merged, `Show Jarvis Island` can immediately hide the panel while leaving voice controls available. The new desktop app and updated server must be activated together for the microphone lock to protect both voice paths. Persistent activation, startup registration, merge and push require separate approval under the attached prompt.
