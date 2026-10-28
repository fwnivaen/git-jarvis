# Jarvis remaining build plan

Updated 2026-10-28. This is the implementation and acceptance plan for Nivaen Giritharan's attached request, including Obsidian. A prepared feature is not the same as a live accepted feature. The user approved local-main merge and coordinated activation on 2026-10-28. The new server/helper are running; Obsidian is installed and opened the connected Brain. The original revision, notes/configuration and earlier helper remain retained for rollback.

## Product approach

Obsidian is the local note editor and knowledge folder; Jarvis remains the voice interface and bounded executor. Both use ordinary Markdown. Reuse one source of truth instead of copying the entire vault into every request or adding a hosted service. Start with a new, separate Jarvis Brain folder because no existing Obsidian installation/vault was found in the normal application locations. Keep the existing demonstration notes intact. Do not register startup, enable continuous recording, buy a service, push, or replace the running installation implicitly.

Local keyword/FTS retrieval, bounded relevant passages, unchanged-file indexing and explicit direct note lookup reduce unnecessary model input/calls. Report measured characters and local lookups, not invented token or subscription savings. Synthesis, reasoning and tool planning still require the signed-in Codex model. An Obsidian note is untrusted source material, never authority to run tools or override instructions.

## Prepared work and remaining acceptance

| Order | Area | Prepared implementation and remaining gate |
| --- | --- | --- |
| 1 | Obsidian brain | Separate Markdown setup, aliases/tags/wikilinks, private-note exclusions, incremental FTS5, revision-checked passages and original-note links are implemented. Selection/disconnection applies at server restart; existing demonstration notes are preserved. Approved installation, opening, connection and restart are complete. Local search is visibly verified; accept real edits, refresh/deletion and native app links. |
| 2 | Usage controls | Chat and note tools reuse bounded local retrieval. The activated server read About me with zero model calls, and a real Work answer cited the unchanged source. Reasoning still uses the included Codex route. Session character/lookup counters do not measure billed tokens or subscription savings. Accept broader questions and source freshness during real edits. |
| 3 | Activity, settings and export | Settings has brain, local operations, bounded task/approval history and selected-data ZIP export. The activated settings-only download passed the UI flow and archive inspection; the real task appears in activity metadata. Other selections, activity navigation and simultaneous native/web changes still need acceptance. Export excludes private notes, credentials, checkpoints and journals; it is not a full backup. Native background speech uses revision-checked Operations settings. |
| 4 | Safer actions | Existing durable note approvals now have a separate preference set/forget approval queue, exact-state conflicts, expiry and one-hour exact-state undo. Work and Island show both queues. Accept save/reject/conflict/restart/undo with real choices. Arbitrary Mac actions, import compensators and universal undo remain deferred. |
| 5 | Memory lifecycle | Explicit preference set/forget, bounded shared-note scope and a separate exact-state removal control for settled Work tasks are prepared. Work erasure covers catalog contents, graph source checkpoints and per-task tool journals, with pending cleanup and duplicate-prevention markers. Source-note exclusion does not erase old exports/receipts; older Ultra runs and general cross-store retention remain separate. No automatic transcript dump or age-based deletion is added. [Scope](TASK-PRIVACY.md). |
| 6 | Background delivery | Completion/approval deduplication, active-conversation queueing, pause, supported session/display/system-sleep gates and New York quiet hours are implemented. Native speech preference is changed only after a revision-checked server acknowledgement; old servers retain local fallback. Accept real missed/duplicate/deferred delivery and native/web controls after activation. Calls, sharing and DND inference remain unavailable. |
| 7 | Local operations | Opt-in bounded local battery/network snapshots, resource gates and daily metadata checks are implemented. The manual local health-check UI passed after activation. Automatic daily checks and local signal collection remain off. Scheduling runs only inside the running server, makes no model calls and creates no OS startup service. Accept opt-in local signals and a daily schedule; a configured network is not verified internet access. |
| 8 | Hands-free preview | “Try hands-free (preview)” is off every launch and asks for explicit continuous-microphone consent. On-device “Hey Jarvis,” 1.1-second silence detection, a 20-second command cap, six-second follow-up and shared microphone/analyzer ownership are prepared. Test recognition, false wakes, handoffs, cancellation, sleep and battery before acceptance. Immediate screen-lock shutoff is unverified: disable preview before locking. Double clap is not implemented. |
| 9 | Native acceptance | Preserve confirmed hotkey/Island/reply/mute; verify physical cancel, recognition, VoiceOver/accessibility, click-through, Spaces/full-screen/displays, supported screen-capture exclusion, end-to-end latency/CPU and real audio-driven motion. |
| 10 | Full evaluation | Full regression, activated-server preflight, fresh parallel tasks, third-task refusal, task removal/replay blocking and a real sourced Work/Word export passed. Complete the manual cases in the representative 30-task set, broader model/failure trials and visual Word formatting acceptance. |
| 11 | Install and upgrades | Approved Obsidian setup, local-main merge and coordinated server/helper activation are complete. Rollback is retained. Stable signing needs an available identity; login startup and publication remain separate choices. |
| 12 | Broader assistant | Improve local research, permissioned Mac tools, vision and documents in bounded slices; then taught workflows and feedback. Communications/meetings, iPhone/other devices/home and new local models each require selected accounts, hardware, permissions, maintenance and cost checks. Do not invent configured integrations. |

## Build method

1. Inspect existing modules and reuse the note store, gateway, approval journal, workflow store and private runtime directories.
2. Build independent foundations in the isolated branch. Independent agents review/implement native voice, activity/export and local operations; the primary agent owns Obsidian, server/UI integration and final verification.
3. Test each meaningful boundary with temporary notes/configuration and fake model/OS providers before using real personal data. Exercise actual visible UI for each finished flow.
4. Commit reviewable increments. Run the project preflight before calling a change complete; candidate-only checks and original-server preservation checks are distinguished.
5. Request required activation/privacy/account choices only when the prepared result is concrete. Never treat silence as approval.

## Verification record

Physical PTT/Island/audible reply/mute have user acceptance on the earlier helper. This expansion has passed the full disposable native checker: **494 assertions** across state (308), announcements (41), orb (13), layer/sizing (28), watcher (20), background delivery (23), Operations synchronization (26) and hands-free policy/handoff (35), plus runtime Island and shared voice-lock checks. The final disposable helper builds with zero Swift diagnostics. These are policy, fixture and rendering checks; they do not establish live hands-free recognition or physical screen-lock handling.

Current expansion results are recorded below. The preceding f890d48 checkpoint had 357 Python tests (8 skipped, no failures), 14 JavaScript suites and preserved-original live preflight (14 pass, 0 fail, 2 warn); those are historical results, not current expansion results.

## User-facing completion gates

1. Completed: install verified Obsidian, create/connect Jarvis Brain, merge local main and restart both components with a rollback path.
2. Accept real note edits/source links, preference approvals/undo, activity navigation, other export selections and Operations/native speech changes. Local search, a settings-only ZIP download/inspection and a manual health check have passed.
3. Recheck physical PTT and mute; accept cancel, recognition, accessibility, displays/Spaces, screen sharing and end-to-end performance. Hands-free remains an optional preview with the separate microphone and lock limitation above.
4. Extend the passed real task/parallel/replay trials to broader failures and the five manual evaluation cases; visually open Word exports. Full activated-server preflight has passed.
5. Choose broader communications, devices, home or new model integrations individually. Accounts, hardware, permissions, maintenance and costs are outside automatic local setup. Stable signing, startup and publication retain their separate gates; local activation/merge were explicitly approved.

## Obsidian references

- [How Obsidian stores data](https://help.obsidian.md/Files+and+folders/How+Obsidian+stores+data): local Markdown and separate app configuration.
- [Internal links](https://help.obsidian.md/Linking+notes+and+files/Internal+links): wikilinks, folders, headings and aliases.
- [Obsidian URI](https://help.obsidian.md/Extending+Obsidian/Obsidian+URI): official app-open links; no community plugin required.
- [Official download](https://obsidian.md/download): installation is separate from preparing a compatible folder.

## Prepared expansion and current verification

The Obsidian reader/FTS/context/direct-lookup path, revision-bound citations, preference review/undo, bounded activity, selected-data export, local operations and native preview/delivery contract are implemented and merged into local main. Current results: 490 Python tests (8 skipped, no failures), 16 JavaScript suites and 494 native assertions plus disposable app/Island/lock checks. Visible isolated UI verified Brain search and complete preference proposal/approval/undo with no model calls. Full preflight on the activated server: **14 pass, 0 fail, 2 warn**. The new server/helper are running; physical acceptance remains separate.

The official Obsidian 1.14.5 installer passed strict signature verification and Gatekeeper assessment as a notarized Dynalist app. The app is installed at `~/Applications/Obsidian.app`, visibly opened `~/Documents/Jarvis Brain`, and the new server is connected to its four starter notes. [Practical setup and limits](OBSIDIAN-BRAIN.md).

Remaining: real Obsidian edit/source-link acceptance; physical voice/Island/accessibility/privacy/performance checks; broader live model/failure trials and the five manual evaluation cases; broader retention/per-task memory policy and action compensators; verified availability signals; hands-free acoustic/lock/battery acceptance and optional double clap; stable signing/login startup; selected broader integrations and device/account choices. Local merge was approved and completed. Push, startup and continuous-microphone choices are not inferred from the tests.

## Approved installation and activation progress

On 2026-10-28 the user explicitly approved the official Obsidian installation, new Brain folder, local-main merge and coordinated restart. Obsidian is installed and visibly opened `~/Documents/Jarvis Brain`; four starter notes were created without overwriting existing notes. The folder is connected to the running server. Full activated-server preflight passed **14 pass, 0 fail, 2 warn**, including real capture/recall/cleanup and JPEG vision. A fresh candidate two-task trial, third-task refusal, reviewed test-task removal and old-request blocking also passed. After activation, a new sourced task completed in 7.968 seconds and produced valid editable Word content. [Activation evidence and rollback](ACTIVATION-2026-10-28.md). Startup, push and hands-free enabling remain separate choices.
