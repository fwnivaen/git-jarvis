# Jarvis feature catalog and roadmap — 2026-10-25

Statuses describe this worktree and observed running system. **Verified working** means exercised locally; **partial** means a real implementation exists with an open acceptance or integration gate; **missing** means no implementation was found; **blocked** means external access, a supplied detail or explicit activation is required. Effort is a rough order-of-magnitude estimate for the next meaningful slice, not a commitment.

| Capability | Status | Next effort / dependency | Evidence or reference |
| --- | --- | --- | --- |
| Local HTTP server and 4700 | Verified working | Maintenance | `server.py`, live loopback check and preflight. |
| Included Codex CLI model route | Verified working | Maintenance | Approved preflight; [usage policy](usage-policy.md). No paid key required. |
| Optional backend selection with paid route refused | Verified working | Maintenance | Existing route/usage tests; [usage policy](usage-policy.md). |
| Markdown vault and FTS5 | Verified working | Maintenance | Existing note tests and preflight. |
| Local semantic retrieval | Partial | Small; local model availability | Existing optional embedding module and tests; not re-downloaded here. |
| 3D galaxy | Verified working | Maintenance | Existing viewer and baseline browser suites. |
| Memory scope and retention | Partial | Medium; policy and UI review | Existing preferences/retention controls; [architecture](ARCHITECTURE.md). |
| Work workflow execution, checkpoints and replay | Verified working | Medium; broader live task trials | `/workflows`, [workflow guide](WORKFLOWS.md), existing regression suite. |
| Default two concurrent **tasks** | Missing | Medium; admission design and compatibility | Current Work admission is one run with internal workers; `TASK_MAX_CONCURRENT` absent. |
| General approval cards and queued approvals | Missing | Large; durable task state and trusted action boundary | Existing note-save confirmation is narrower; no `/stage2/tasks`. |
| Screen snapshots and explicit vision | Partial | Medium; physical permission acceptance | Existing Eyes/Watch routes and tests; [architecture](ARCHITECTURE.md). |
| Word document export | Partial | Small; live import/export acceptance | Existing document tools; [Ultra guide](../ULTRA-JARVIS.md). |
| Focus retirement | Verified working | None | `/focus` 404 and existing removal tests. |
| Native menu-bar push-to-talk | Partial | Small; permission, speech and latency checks on Mac | New desktop app builds; no normal launch yet. |
| On-device `SpeechTranscriber` | Partial | Small; model install and live audio acceptance | Installed OS supports it; model not installed. [Apple Speech session](https://developer.apple.com/videos/play/wwdc2025/277/). |
| Legacy on-device speech fallback | Partial | Small; physical mic acceptance | New app requires `supportsOnDeviceRecognition`; not physically exercised. |
| British spoken replies and muted text | Partial | Small; audible/mute acceptance | New synthesis and paced-text implementation. |
| Island state model and static layouts | Partial | Medium; live timing and accessibility acceptance | 303 state checks, 30 static snapshots, runtime Metal check; [report](voice-island-report.md). |
| Live approval Island source | Missing | Medium; general approval engine | View state exists, live approval source does not. |
| Workflow background completion announcements | Partial | Small; live transition and presence testing | Existing `/workflows` polling, three/thirty-second intervals; spoken summaries are opt-in. |
| Microphone ownership across updated helpers | Partial | Small; integrated activation | Cross-language exclusion test passed; running server not updated. |
| Native wake-word and double-clap | Partial | Large; privacy, battery and false-activation study | Existing browser voice recognizes a Jarvis phrase after explicit mic activation; the new native app has no continuous listener. |
| Presence, calls, sharing and quiet-hours delivery | Missing | Large; permissioned Mac signals and delivery policy | Do not infer availability from silence. |
| Plan preview and dry run | Partial | Medium; per-tool preview contracts | Existing bounded workflow templates; no universal preview. |
| Idempotency, durable write journal, resource locks | Partial | Medium; extend across all actions | Existing `execution_control.py`/gateway; new voice lock. |
| Reversible operations and undo | Partial | Large; per-action compensators | Existing note revision checks; no universal undo. |
| Tool registration and validation | Partial | Medium; live capability acceptance | Existing scoped gateway; [upgrade contracts](upgrade-contracts.md). |
| Unified activity timeline and replay | Partial | Medium; merge workflow and native events | Existing workflow events/journal, no unified voice timeline. |
| Immediate progress acknowledgment | Partial | Small; latency sampling | Work view has real progress; [upgrade evidence](upgrade-evidence.md). |
| Resource budgets and model output validation | Partial | Medium; cross-route coverage | Existing Ultra budgets and typed contracts; [architecture](ARCHITECTURE.md). |
| Representative 30-task evaluation | Missing | Medium; private case design and real runs | Existing 24 synthetic upgrade cases are a different, fixture-only evaluation. |
| Risk approvals and kill switches | Partial | Large; action taxonomy and durable approvals | Existing optional feature gates and note approval, no general policy engine. |
| Secret handling and prompt-injection defenses | Partial | Medium; audit every tool boundary | Ignored config and scoped tools; no universal defense claim. |
| Safe upgrades, health checks, battery/network awareness | Partial | Medium; verified signals and scheduling | Existing upgrade gates; no unified daily health agent. |
| Stable signing and optional login startup | Blocked | Small after approval and signing identity | Ad hoc test build only; no LaunchAgent changed. |
| Controlled workers and failure drills | Partial | Medium; broader live acceptance | Existing bounded workers and fixture drills; [upgrade checklist](upgrade-checklist.md). |
| Per-task memory and long-task continuity | Partial | Medium; privacy and recovery policy | Existing checkpoints/preferences; not joined into one task model. |
| New York timezone | Verified working | Maintenance | `ZoneInfo` winter/summer test and source audit. |
| First-run permissions and export | Partial | Medium; live acceptance | Native usage descriptions present; guided first-run and complete export absent. |
| Local research, Mac tools and documents | Partial | Large; one permissioned tool at a time | Existing notes/tools/vision/doc support; other actions require scope review. |
| Communications, meetings, iPhone, other devices, home | Missing | Large; accounts, permissions and user choices | No new integrations authorized or installed. |

## Order of work

1. **Activate and accept the local voice slice:** review the isolated app, choose a stable signing identity if desired, then test microphone permission, transcription, speech, mute, screen privacy, focus, click-through and CPU/latency on this Mac. Merge/restart/startup changes require explicit approval.
2. **Unify task status and approvals:** design the default two-task admission and a durable task/approval endpoint using the existing gateway and journal. Keep the Work route intact during migration. Add plan preview and practical undo per action.
3. **Make background delivery trustworthy:** use verified completion/approval events, queue around active speech, and add permissioned presence/quiet-hours signals. Measure missed and duplicate updates.
4. **Offer hands-free listening only after a privacy review:** start with an optional local wake word; measure false activations and battery use. Double-clap and follow-up listening follow the same gate.
5. **Grow tools and devices by permissioned slices:** evaluate each integration for cost, maintenance, authentication and reversibility. Retain the existing rejection of paid fallback and the retired Focus feature.

The existing [execution upgrade checklist](upgrade-checklist.md) still governs Deep Agents, remote workspaces and evaluated learning. Its open gates are not passed by this voice branch.
