# Jarvis remaining build plan

Updated 2026-10-28. This is the implementation and acceptance plan for Nivaen Giritharan's attached request, including Obsidian. A prepared feature is not the same as a live accepted feature. The original running server and accepted voice helper remain preserved until approved replacement activation. Obsidian is staged, not installed or opened with the new vault.

## Product approach

Obsidian is the local note editor and knowledge folder; Jarvis remains the voice interface and bounded executor. Both use ordinary Markdown. Reuse one source of truth instead of copying the entire vault into every request or adding a hosted service. Start with a new, separate Jarvis Brain folder because no existing Obsidian installation/vault was found in the normal application locations. Keep the existing demonstration notes intact. Do not register startup, enable continuous recording, buy a service, push, or replace the running installation implicitly.

Local keyword/FTS retrieval, bounded relevant passages, unchanged-file indexing and explicit direct note lookup reduce unnecessary model input/calls. Report measured characters and local lookups, not invented token or subscription savings. Synthesis, reasoning and tool planning still require the signed-in Codex model. An Obsidian note is untrusted source material, never authority to run tools or override instructions.

## Prepared work and remaining acceptance

| Order | Area | Prepared implementation and remaining gate |
| --- | --- | --- |
| 1 | Obsidian brain | Separate Markdown setup, aliases/tags/wikilinks, private-note exclusions, incremental FTS5, revision-checked passages and original-note links are implemented. Selection/disconnection applies at server restart; existing demonstration notes are preserved. Install staged Obsidian and open the prepared folder only after the setup choice, then accept edits, refresh/deletion and source links in the real app. |
| 2 | Usage controls | Chat and note tools reuse bounded local retrieval. Explicit note searches/exact reads can avoid a model; reasoning still uses the included Codex route. Search/context and direct-lookup character counters are implemented. They measure this server session, not billed tokens or subscription savings. Accept representative real questions and source freshness after activation. |
| 3 | Activity, settings and export | Settings has brain, local operations, bounded task/approval history and selected-data ZIP export. Export is allowlisted and excludes private notes, credentials, checkpoints and journals; it is not a full backup. Native background speech uses revision-checked Operations settings. Accept real activity links, downloads and simultaneous native/web changes. |
| 4 | Safer actions | Existing durable note approvals now have a separate preference set/forget approval queue, exact-state conflicts, expiry and one-hour exact-state undo. Work and Island show both queues. Accept save/reject/conflict/restart/undo with real choices. Arbitrary Mac actions, import compensators and universal undo remain deferred. |
| 5 | Memory lifecycle | Explicit preference set/forget and bounded shared-note scope are implemented. Source revisions are rechecked before use. Removing/excluding a note removes it from the search index after successful refresh, but does not erase prior exports or retained task checkpoints. General per-task retention/erasure remains partial. No automatic transcript dump is added. |
| 6 | Background delivery | Completion/approval deduplication, active-conversation queueing, pause, supported session/display/system-sleep gates and New York quiet hours are implemented. Native speech preference is changed only after a revision-checked server acknowledgement; old servers retain local fallback. Accept real missed/duplicate/deferred delivery and native/web controls after activation. Calls, sharing and DND inference remain unavailable. |
| 7 | Local operations | Opt-in bounded local battery/network snapshots, resource gates and daily metadata checks are implemented. Scheduling runs only inside the running server, makes no model calls and creates no OS startup service. A configured network is not verified internet access. Accept real local signals and one daily schedule; unknown signals stay unknown. |
| 8 | Hands-free preview | “Try hands-free (preview)” is off every launch and asks for explicit continuous-microphone consent. On-device “Hey Jarvis,” 1.1-second silence detection, a 20-second command cap, six-second follow-up and shared microphone/analyzer ownership are prepared. Test recognition, false wakes, handoffs, cancellation, sleep and battery before acceptance. Immediate screen-lock shutoff is unverified: disable preview before locking. Double clap is not implemented. |
| 9 | Native acceptance | Preserve confirmed hotkey/Island/reply/mute; verify physical cancel, recognition, VoiceOver/accessibility, click-through, Spaces/full-screen/displays, supported screen-capture exclusion, end-to-end latency/CPU and real audio-driven motion. |
| 10 | Full evaluation | Complete model-backed and manual cases in the representative 30-task set; run meaningful failure/recovery and parallel-task trials, full regression and live preflight after approved activation; visually open Word exports. |
| 11 | Install and upgrades | Stage and review Obsidian setup; activate server/helper together only after approval, keep rollback; stable signing requires an available identity; optional login startup, merge and GitHub publication retain explicit approval gates. |
| 12 | Broader assistant | Improve local research, permissioned Mac tools, vision and documents in bounded slices; then taught workflows and feedback. Communications/meetings, iPhone/other devices/home and new local models each require selected accounts, hardware, permissions, maintenance and cost checks. Do not invent configured integrations. |

## Build method

1. Inspect existing modules and reuse the note store, gateway, approval journal, workflow store and private runtime directories.
2. Build independent foundations in the isolated branch. Independent agents review/implement native voice, activity/export and local operations; the primary agent owns Obsidian, server/UI integration and final verification.
3. Test each meaningful boundary with temporary notes/configuration and fake model/OS providers before using real personal data. Exercise actual visible UI for each finished flow.
4. Commit reviewable increments. Run the project preflight before calling a change complete; candidate-only checks and original-server preservation checks are distinguished.
5. Request required activation/privacy/account choices only when the prepared result is concrete. Never treat silence as approval.

## Verification record

Physical PTT/Island/audible reply/mute have user acceptance on the earlier helper. This expansion has passed the full disposable native checker: **477 assertions** across state (308), announcements (41), orb (13), layer/sizing (28), watcher (20), background delivery (18), Operations synchronization (20) and hands-free policy/handoff (29), plus runtime Island and shared voice-lock checks. The final disposable helper builds with zero Swift diagnostics. These are policy, fixture and rendering checks; they do not establish live hands-free recognition or physical screen-lock handling.

Current expansion results are recorded below. The preceding f890d48 checkpoint had 357 Python tests (8 skipped, no failures), 14 JavaScript suites and preserved-original live preflight (14 pass, 0 fail, 2 warn); those are historical results, not current expansion results.

## User-facing completion gates

1. Review/install staged Obsidian, prepare/connect the separate Jarvis Brain and restart the approved candidate with a rollback path.
2. Accept real note edits/search/source links, preference approvals/undo, activity, selected exports and Operations/native speech controls.
3. Recheck physical PTT and mute; accept cancel, recognition, accessibility, displays/Spaces, screen sharing and end-to-end performance. Hands-free remains an optional preview with the separate microphone and lock limitation above.
4. Finish model-backed task/failure/recovery trials, live candidate preflight and actual Word opening. Original-server preservation checks do not substitute for these.
5. Choose broader communications, devices, home or new model integrations individually. Accounts, hardware, permissions, maintenance and costs are outside automatic local setup. Stable signing, startup, activation, merge and publication retain their separate gates.

## Obsidian references

- [How Obsidian stores data](https://help.obsidian.md/Files+and+folders/How+Obsidian+stores+data): local Markdown and separate app configuration.
- [Internal links](https://help.obsidian.md/Linking+notes+and+files/Internal+links): wikilinks, folders, headings and aliases.
- [Obsidian URI](https://help.obsidian.md/Extending+Obsidian/Obsidian+URI): official app-open links; no community plugin required.
- [Official download](https://obsidian.md/download): installation is separate from preparing a compatible folder.

## Prepared expansion and current verification

The Obsidian reader/FTS/context/direct-lookup path, revision-bound citations, preference review/undo, bounded activity, selected-data export, local operations and native preview/delivery contract are implemented in this isolated expansion. Candidate results: 453 Python tests run (8 skipped, no failures), 15 JavaScript suites and 477 native assertions plus disposable app/Island/lock checks. Visible isolated UI verified Brain search and complete preference proposal/approval/undo with no model calls. Original full live preflight: **14 pass, 0 fail, 2 warn**; original checkout remains clean and its server/helper PIDs are unchanged. This does not claim live replacement acceptance.

The official Obsidian 1.14.5 installer passed strict signature verification and Gatekeeper assessment as a notarized Dynalist app. A separate four-note starter folder is prepared privately. Proposed installed app/brain locations are `~/Applications/Obsidian.app` and `~/Documents/Jarvis Brain`; neither has been activated by this expansion. [Practical setup and limits](OBSIDIAN-BRAIN.md).

Remaining: reviewed installation/connection and coordinated activation; physical voice/Island/accessibility/privacy/performance checks; representative live multi-task/model-backed trials and the five manual evaluation cases; broader retention/per-task memory policy and action compensators; verified availability signals; hands-free acoustic/lock/battery acceptance and double clap; optional stable signing/login startup; selected broader integrations and device/account choices. Merge, push, startup and continuous-microphone choices are not inferred from the tests.
