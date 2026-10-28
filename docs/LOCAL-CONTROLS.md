# Local voice, task and note controls

This describes the approved build running from local main after the coordinated restart on 2026-10-28. See the [implementation record](voice-island-report.md) for checks and remaining acceptance.

## Desktop voice

- Hold **Control–Shift–Space** to speak from any app; release to submit.
- **Option–Escape** cancels. Plain Escape is borrowed only during applicable active states.
- **Option–Command–M** mutes speech; answers remain visible.
- The **✦** menu controls the Island, local speech model, read-only permissions, optional background speech and paused background updates.

The helper uses one microphone tap while explicitly listening. Hands-free is off each launch. The explicit “Try hands-free (preview)” menu choice asks before continuous on-device transcription, exact “Hey Jarvis” activation and a bounded follow-up. Turn the preview off before locking; immediate lock detection has not been verified. Background notices stay in memory, expire after thirty seconds and wait behind active speech. New York quiet hours plus session-inactive, display-sleep and system-sleep notifications defer delivery; manual pause covers other unwanted updates. Calls, Do Not Disturb and screen sharing are not detected. Foreground tracking and Focus remain removed.

## Work and previews

**Preview plan** reports the actual recipe, roles, tools, budgets and private checkpoint effects. It makes no model/tool call, reads no note contents and creates no task database. **Launch workflow** is separate.

`TASK_MAX_CONCURRENT` defaults to two owners, accepts integers 1–8 and falls back to one with a notice when invalid. Shared model/tool limits remain three; chat keeps one owner. Cancelled tasks retain capacity until callbacks/process cleanup settle. Failed process cleanup retries with bounded backoff while status stays responsive.

The canonical `/stage2/tasks` view projects existing Work records. Work tools remain read-only. Completed answers offer **Download Word document**, creating an editable DOCX in memory only after a click.

## Queued note decisions and undo

1. Edit an existing note and choose **Review changes**. The original identity/revision and proposed contents enter a private queue; the note has not changed.
2. Inspect the before/after contents and choose **Save changes**. **Not now** leaves the proposal in **Work → Note decisions**.
3. Mission Control offers **Approve and save** or **Reject edit**. Approval expires after five minutes and binds one exact note identity, revision and proposed text.
4. Verified saves offer **Undo this edit** for one hour. Undo refuses later independent edits and requires the exact saved revision and text.

The Island shows a generic notice for actual pending proposals. **Review ↗** opens `/#approvals`, selecting Mission Control. Resolved/expired proposals clear on the next successful poll. Failed polls do not invent a resolution.

Interrupted/unverified effects become uncertain and are never replayed automatically. Inspect the note before a fresh edit. These note controls cover existing note edits. A separate preference queue now supports explicit set/forget proposals and exact-state undo; imports and general external actions still have no compensation/approval engine. Model tools can propose a preference change but cannot approve it.

### Storage and privacy

`.local/note-actions/control.sqlite3` uses the existing private `ControlStore` and native tool journal. Its directory/database use modes 0700/0600. It contains original/proposed note text: treat it as private note data. Git and HTTP static serving exclude it. Catalog/polling return metadata; explicit local review fetches contents.

Limits are eight pending proposals, sixty-four total records and a 64 MiB threshold. At capacity, new proposals are refused. There is no automatic deletion or archive UI. During approved stopped-service maintenance, preserve the whole queue directory in a private backup before starting a new one. Never remove uncertain receipts to replay actions.

Decision POSTs require the local viewer origin, strict fields and an exact fingerprint. No email, publishing, purchases, deletion or other external action is exposed. The legacy approved-note route remains for compatibility.

## Readiness and setup

Settings/Voice offer **Refresh checks** and **View setup checks**; `/health` returns the same on-demand overview. It inspects artifacts, private file modes, supplied feature gates and tool metadata. The HTTP wrapper reads normal backend/settings locally and returns no credentials or private paths.

It does not open the microphone, prompt, download models, read note contents, open databases, make model/network calls or schedule work. A bundle is not proof of a running helper, and a database header is not an integrity check. Separate Settings sections now provide opt-in daily checks/resource policy and selected-data export; they are not effects of opening readiness. Native background speech uses a revision-checked shared setting, while other native/web settings remain separate. Complete guided acceptance and full backup/restore are not claimed.

## Preferences, Brain and local operations

Settings → Saved preferences queues exact set/forget choices. Work → Preference decisions displays before/after maps and requires exact approval; one-hour undo refuses any intervening map change. Retries reuse the same proposal identity until an explicit new review, preventing duplicate pending changes. Queues and tool/workflow state are namespaced with the selected vault at startup. GET status does not create a new queue.

The [Obsidian Brain guide](OBSIDIAN-BRAIN.md) explains the separate shared Markdown folder, local search, bounded source/history context, private-note exclusions and future-only connection changes. Existing notes and private configuration are preserved.

Settings → Background and daily checks is opt-in. It uses New York time, one completed check per local day, bounded retries, resource gates and a cross-process lock. It calls no model and runs only while this server runs. Battery/network probes require a separate explicit setting; a configured network is not verified internet. Quiet hours defer background notices. Native background speech patches only that setting using its exact current revision; stale updates are refused. Unknown call/DND/sharing state is not inferred.

History projects bounded task/approval metadata, not transcripts or note bodies. Settings → Export your data downloads only checked sections as an in-memory ZIP. Private notes/configuration/credentials/checkpoints/journals are excluded; this is not a complete backup or automatic erasure mechanism.

Settings → **Saved task privacy** adds explicit review and removal of one terminal, cleanup-complete, unowned Work task. Its contents and known checkpoint/journal files are removed only after a separate exact-state confirmation. A pending receipt means cleanup is unfinished and requires an explicit retry. Minimal admission/removal markers prevent an old launch request from recreating the task. Source notes, preferences, approval queues, older Ultra runs, previous exports and optional minimized learning hashes are excluded. There is no automatic expiry or secure-wipe claim. [Full scope](TASK-PRIVACY.md).

## Verification and rollback

`tools/check_native.sh` builds both helpers in a disposable directory. `JARVIS_DESKTOP_APP_PATH`, `JARVIS_VOICE_APP_PATH` and `JARVIS_SWIFT_MODULE_CACHE` select explicit output/cache paths. Demo, snapshot and text/layer/transition evidence modes register no hotkeys, open no microphone and call no server.

The recorded previous revision `1d1027c`, original 30 notes, unchanged private configuration, earlier desktop bundle and saved browser helper remain the rollback baseline. The user approved local merge and coordinated activation on 2026-10-28. Both updated native helpers were installed, and only one normal desktop helper runs. Pushing and login startup still require a separate choice. Never run two normal helpers.

After approved activation, rollback stops the new helper/server, returns to the recorded previous revision without discarding work and starts the previous build. Preserve configuration, vault, checkpoints and approval journals. **Show Jarvis Island** can immediately hide the panel while preserving voice.
