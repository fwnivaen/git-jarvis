# Jarvis Brain with Obsidian

A **vault** is simply a folder of notes. Obsidian edits those notes; Jarvis searches the same files and can use relevant passages in answers. The connection uses ordinary Markdown, without a community plugin, Obsidian account or paid API key.

## Prepared setup

The isolated candidate includes a new **Brain** tab and a separate four-note starter folder for Nivaen Giritharan. It includes a start guide, About me, Jarvis project and memory rules, plus Inbox, Knowledge, Projects, Decisions, Daily and Private folders. Existing demonstration notes are preserved.

The proposed installed locations are `~/Applications/Obsidian.app` and `~/Documents/Jarvis Brain`. Installation, selecting that folder for the running assistant, and restarting Jarvis require the explicit replacement approval from the attached prompt. The verified official Obsidian 1.14.5 installer is staged privately; the app has not been installed or opened.

Once connected, edit notes in Obsidian or Jarvis. Choose **Refresh notes** in Brain to update the galaxy, or ask a question; source use refreshes the shared index first. **Open in Obsidian** opens the original file. **Read in Jarvis** resolves its stable identity after refreshing the graph. Connection/disconnection takes effect at server startup so an active task never silently changes vaults.

## Saving unnecessary model work

- The Brain tab searches locally with SQLite FTS5 and makes no model call.
- “Search my notes for supplier review” returns matching excerpts locally.
- “Read note About me” reads one exact title, folder path or alias locally.
- A reasoning question still uses the signed-in Codex model. Relevant passages share a default 6,000-character budget; recent conversation context is bounded to 3,000 characters.
- Unchanged files avoid content reads during indexing. New, edited, renamed and deleted notes are reconciled on refresh.
- Counters measure local lookups and source characters in this server session. They do not measure billed tokens, account allowance or a promised percentage of savings.

## What is shared

The supported collection is at most 1,000 Markdown files, 800,000 bytes per note and 64 MiB of text. Alias and tag properties, wikilinks and local Markdown note links help search and the galaxy. This is a bounded Markdown/property reader, not a complete YAML or Obsidian plugin engine.

Jarvis excludes hidden paths, `Private`, `Secrets`, symlinks and notes with a private/exclude property. For example:

```yaml
---
jarvis_private: true
---
```

Privacy properties accept common quoted booleans and comments. Unknown or incomplete privacy metadata is excluded. The Private folder is an exclusion from Jarvis; its ordinary Markdown files are not encrypted by this feature.

Selected passages can be sent through the configured signed-in model route when answering. Retrieved notes remain untrusted source material and cannot grant tool authority. A changed/deleted/private source invalidates an in-flight source receipt; Jarvis asks for a fresh question instead of returning the stale answer. Numeric graph IDs may change, so citations and editors also use stable identity and exact revisions.

## Memory, review and export

Settings lets you request saving or forgetting an explicit preference. The model tools also queue preference proposals. **Work → Preference decisions** shows complete before/after maps; only an exact local approval applies the change. Decisions expire after five minutes. One-hour undo is offered while the complete saved state still matches. Unknown outcomes are never automatically repeated. Preferences are not a password store.

History displays bounded task and decision metadata. Settings can download only selected data as a ZIP; private notes, configuration, credentials, checkpoints and journals are excluded. Recognized credential forms are redacted, but selected user text should still be reviewed before sharing. This is not a complete backup/restore system.

Removing or excluding a note removes it from subsequent successful search refreshes. It does not erase earlier exports, retained task checkpoints or approval receipts. Broader retention and deletion controls remain on the [build plan](BUILD-PLAN.md).

## Maintenance helper

`tools/setup_brain.py` can prepare a new absolute folder or stage an existing folder selection and context budget. It never overwrites an existing vault or starts/restarts Jarvis. Use its `--help` for the explicit preparation/connect/disconnect options. Do not point a running task at a replacement scope.

## References

[Obsidian local files](https://help.obsidian.md/Files+and+folders/How+Obsidian+stores+data) · [Internal links](https://help.obsidian.md/Linking+notes+and+files/Internal+links) · [Obsidian URI](https://help.obsidian.md/Extending+Obsidian/Obsidian+URI) · [Official download](https://obsidian.md/download)
