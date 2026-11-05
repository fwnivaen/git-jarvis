# Using Jarvis

Start from the repository with `./start.sh`, then open [localhost:4700](http://localhost:4700). Keep that terminal open; Control+C stops the server. The launcher rebuilds the note graph and selects the project-local Ultra environment when enabled. For installation and the feature flags, use the [Ultra guide](../ULTRA-JARVIS.md).

## Notes and the galaxy

The included café notes are fictional examples. For your own notes, [connect an external Obsidian vault](OBSIDIAN-BRAIN.md); the `notes/` demo is tracked by Git. `[[Note Title]]` links form the graph. Search with Command-K, select a star to inspect its note, or use the Notes workspace for reading and editing. Editing requires confirmation and the current file revision. For a connected vault, use **Brain → Refresh notes** after edits; source retrieval also refreshes the index. The unconnected demo path needs a rebuild/restart and browser reload after external edits.

Ask a question about a note to receive source chips. Selecting a source reveals the supporting note in the galaxy. Unsupported answers should say when the vault lacks the information. Greetings and general chat do not move the camera to unrelated notes.

Use “remember that…”, “note to self…” or “log this…” to create a capture. Captures become searchable immediately. Notes → Import a document converts bounded PDF/DOCX/XLSX text into Markdown with provenance. Scanned PDFs need OCR first. Personal captures and imports are ignored by Git; keep your own local backup if needed.

Settings → Memory lets you explicitly save or forget preferences. Hybrid search and preferences require their corresponding Ultra flags; the existing keyword retriever remains the fallback.

## Crew and workflows

The top-left glassy crew shows the boss above three workers. Its heading collapses the figures. The activity drawer shows actual models, phases, tools, elapsed time, retries and reported token use. Simple questions leave worker slots unused.

Settings offers automatic routing and reusable analysis, research, code-review and document workflows. Ask for three workers explicitly when you want parallel collaboration. Workers share reports and review available findings; the boss returns one answer. A timed-out worker remains visible and a partial result is labelled. Code review can suggest a patch, but no shell/test execution tool is exposed.

The model chooser under the orb applies an explicit runtime override. “Go back to your normal brain” restores configured routing. Exact unavailable model names are refused rather than substituted. Restarting clears the runtime override. Model IDs, effort profiles, limits and recovery commands are in the [Ultra guide](../ULTRA-JARVIS.md).

## Voice and orb

Click Mic, click the orb or hold Space outside an editable field. Only explicit microphone activation enables listening. The optional “Jarvis” phrase setting gates recognized transcripts after activation; it is not an always-on hotword detector. Stop or interrupt speech through the existing controls.

The orb reflects listening, model activity, actual speech, confirmations and connection status. Appearance, reduced motion and quiet interface sounds are saved on the device. Preview animations in orb settings do not open the microphone or change real controller state.

For native speech recognition, build `sh native/build_voice.sh`. The helper requires an available on-device English recognizer and macOS microphone/speech permissions. A Dictation-disabled error requires enabling Dictation in System Settings. Permission grants belong to the launcher or helper named by macOS. Browser recognition may use browser speech services. Audible output must be checked on the Mac itself.

Focus and its activity tracking are removed. Old numeric ledgers are not loaded or updated.

## Eyes and screen watch

Eyes requests the camera, calibrates upright posture and runs bundled face/pose landmarks locally. It reports presence, head-down posture and slouch flags. Lowering the head is a posture heuristic, not recognition of a phone. Lighting and camera position affect it. Reset upright posture after moving the camera or chair. Stopping Eyes releases the camera and models; it never turns on the microphone.

An explicit “look at me” sends one current webcam image to the configured vision route. Ordinary chat has no webcam image. With Ultra cheap vision enabled, image checks start with Luna; disabling that flag preserves the existing route.

Screen watch uses one Entire Screen share selected through the browser. Local thumbnail comparisons gate cloud checks; unchanged automatic frames are skipped when that feature is enabled. Screen questions reuse the same share. “Stop watching” ends the watch and share. Build `sh native/build_watch.sh` for the native watch indicator.

“Give me three minutes” silences nudges across Eyes and screen watch. A model-issued watch start is a request, not proof of an active share: the open viewer must confirm the browser share. No screen pixels are retained in the heartbeat or activity monitor.

## Checks and troubleshooting

Run `python3 tools/check_repo.py` and `python3 tools/check_ultra.py` for repository and regression checks. Add `--embeddings` after installing the optional local model. With the server active, run full `python3 preflight.py`; it makes real model calls and tests model switching. Capture creation/recall/cleanup is skipped by default and reported as a warning. `--allow-capture-cleanup` explicitly permits creating and deleting one disposable verification note; use a separate demo vault for that check. Model/API skips are warnings, not passes.

Verify the original Jarvis dashboard visibly in Chrome. On-device microphone recognition and audible output need physical acceptance checks; automated fixtures do not establish those results.

If port 4700 is occupied, stop the existing Jarvis process in its own terminal. If an optional search dependency/model fails, inspect the fixed status notice and keyword fallback. Never paste credentials into an issue or commit local configuration, runtime journals or personal notes.
