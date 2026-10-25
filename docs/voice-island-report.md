# Jarvis voice and Island implementation record — 2026-10-25

This is the current record for the isolated `codex/jarvis-voice-island` branch. Older handoffs describe earlier machines and revisions; their measurements are historical. The running checkout was left on `main` and its server was not replaced.

## Reconciliation

The actual repository is `fwnivaen/Jarvis`, with `server.py` listening on loopback port 4700. The original checkout was clean before and after verification. It already had a standard-library HTTP server, Codex CLI model route, Markdown vault, SQLite FTS5 and optional local semantic search, 3D galaxy, browser voice helper, Work workflows, checkpoints, bounded tool journal, native screen/notes controls, and an explicit permission model for its existing sensors. Focus is deliberately removed: `/focus` returned 404. It did not have a native menu-bar conversation app or Island, an `ultra_tasks.py`, or `/stage2/tasks`; the latter returned 404. Existing workflow status is available from `/workflows`.

Private `config.json` remains ignored and stayed in the original checkout. The new worktree has no copied config or Codex credentials. No API key is needed for the normal signed-in CLI path. The local server source now identifies Nivaen Giritharan, New York and `America/New_York`, with a daylight-saving-aware capture timestamp. Its speech still addresses the user as Sir.

## Isolated implementation

- `native/build_desktop.sh` builds an ad hoc signed menu-bar app with the installed Command Line Tools. The bundle belongs to `local.fwnivaen.jarvis.desktop` and stays under this worktree's ignored `.local/` directory. No login item or LaunchAgent was registered.
- Hold Control–Shift–Space for speech, use Option–Escape to cancel, and Option–Command–M to mute. Plain Escape is registered only during an active voice operation. `--ask` and `say` are available; normal mode was not launched against the running assistant.
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
| Island state checks | 299 passed | Pairwise active/error transitions, work priority, queue expiry and UTF-16 mappings; no live window timing proof. |
| App `--check-island` | Passed | Compiles the Metal shader at runtime; does not prove every live transition. |
| Static snapshots | 30 images, notch and flat pill | [Notch speaking](screenshots/island/notch-speaking.png) and [flat pill listening](screenshots/island/pill-listening.png); static captures omit live Metal and any Core Animation effects. |
| Cross-language microphone lock | Python-held lock blocked the Swift app; release restored access | Tested in the isolated build. Running server still has its original module loaded. |
| Targeted Python checks | 3 passed | Lock, symlink refusal, and New York winter/summer offsets. |
| Changed-branch regression suite | 271 Python tests run, 39 skipped, 0 failures; all 12 JavaScript test files passed | Skips reflect private config and optional packages deliberately absent from the isolated worktree. |
| Changed-branch repository check | 262 tracked files, 206 local links, 0 failures | Run after staging the new source files. |

The full changed-branch Python and source hygiene checks are recorded in the final change review. No physical microphone, speaker, speech recognition, external display, full-screen/Spaces, Stage Manager, VoiceOver, screen-share exclusion, click-through, or cold-start latency acceptance was performed. CPU targets of 3% compact and 5% speaking remain unmeasured because the replacement app was not activated. The bundle uses ad hoc signing; a stable signing identity and login startup need a later approved activation step. No wake word or continuous microphone listener is enabled.

## Identity and privacy audit

The owner remote and active README links point to `fwnivaen/Jarvis`. The application identifier uses `fwnivaen`; the server persona and timezone match the supplied details. The verified authenticated GitHub profile supplies the exact commit email, used only for this branch's commit command. Config, vault captures, model assets and `.local` runtime artifacts remain ignored. Active source/docs/test scans showed third-party GitHub links and a synthetic test credential; these are attribution/fixtures, not copied owner information. Archived documents remain historical and were not rewritten. A friend's name or account identifier was not supplied, so an absolute friend-specific zero-trace claim cannot be established from a string scan alone.

## Review, activation and rollback

Review this branch and its two static previews before activation. To roll back the proposed desktop slice, discard this isolated worktree/branch; the original checkout and running process are untouched. If the branch is later merged, `Show Jarvis Island` can immediately hide the panel while leaving voice controls available. The new desktop app and updated server must be activated together for the microphone lock to protect both voice paths. Normal app launch, startup registration, merge and push require the user's separate approval under the attached prompt.
