# Saved task privacy

This feature is prepared in the isolated build. Replacing the running assistant still requires approval.

## What Jarvis saves

Work retains questions, agent findings, final answers, source passages, recovery checkpoints and tool journals locally. Saving this state permits recovery within the task's original budgets. It does not automatically turn task output into an Obsidian note or a preference.

Settings → **Saved task privacy** lists retained Work tasks. Opening the page makes no model call and removes nothing. There is no automatic age-based deletion. Active tasks and tasks whose callbacks or process cleanup have not settled cannot be removed.

## Remove one finished task

1. Choose **Review removal** beside the task. Inspect the effects and limits.
2. Choose **Erase saved task** only if you want to remove its saved contents. This is a separate confirmation and cannot be undone.
3. Check the verified result. If cleanup is pending, some contents may remain. Refresh, review the pending cleanup and explicitly retry it.

The confirmation binds one exact task and its reviewed state. A changed task or changed artifacts require another review. Jarvis first records the authorized removal and blocks recovery, then removes only that task's known checkpoint and journal artifacts. An interrupted cleanup retains a minimal pending record and never reports that removal finished.

A minimal record of the removed task ID and original admission fingerprints remains. Retrying the original launch request cannot recreate the task. Starting a new task requires a fresh request.

## Limits

- The control covers the selected Work engine task, its catalog contents, graph checkpoints and per-task tool journals.
- Source Markdown/Obsidian notes, saved preferences, note/preference approval queues and ordinary chat state are outside this operation.
- Previously downloaded files, exports, open browser contents, backups and any remote model-provider history are outside this operation.
- Optional learning records contain minimized hashes/metrics and are retained separately; this is not an erase-everywhere control.
- File removal and SQLite content cleanup are application-level operations. They do not establish forensic erasure from an SSD, snapshots or backups.
- No model tool can approve or invoke the removal. Requests require the local viewer origin and an explicit, state-bound confirmation.

The older Ultra checkpoint store is separate from the Work engine. This control does not imply that every historical store or action journal has one retention policy. Keep uncertain action receipts until their actual outcomes are reconciled.
