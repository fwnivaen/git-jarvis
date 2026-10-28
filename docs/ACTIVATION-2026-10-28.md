# Jarvis activation — 2026-10-28

## Running setup

The user approved installing Obsidian, creating a separate Brain, merging local main and restarting Jarvis together. These actions are complete:

- Official, notarized Obsidian 1.14.5 is installed at `~/Applications/Obsidian.app`. Strict signature verification and Gatekeeper assessment passed.
- Obsidian visibly opened `~/Documents/Jarvis Brain`. Four starter notes cover setup, Nivaen's supplied details, the Jarvis project and memory rules. No account, community plugin or sync subscription was added.
- The local main checkout runs the updated server on loopback port 4700, connected to this Brain. Both native helper bundles were updated; one normal desktop helper is running.
- The original 30 notes and private configuration were verified unchanged during activation. The earlier revision and native bundles remain available for rollback.
- Hands-free is off. No login startup registration or GitHub push was performed.

## Verified behavior

| Check | Actual result | Limit |
| --- | --- | --- |
| Python suite in the approved runtime | 490 tests, 8 skipped, no failures | Skips are not passes. Full discovery was executed by activated-server preflight. |
| Browser/controller suites | 16 suites passed | Automated fixtures do not prove every physical interaction. |
| Native checks | 494 assertions passed; both helpers built; Island and microphone ownership checks passed | Physical acceptance remains separate. |
| Full activated-server preflight | **14 pass, 0 fail, 2 warn** | Two optional API-provider checks were skipped because the included Codex route is active. |
| Real Brain capture | Saved, recalled and cleaned a temporary note in the selected Brain | The checker verifies the selected folder before writing. |
| Real screen vision | Supplied JPEG answered successfully | Does not prove permission for capturing every Mac app. |
| Visible Brain search | New York found About me; the UI reported no model call | External app links need a human check. |
| Exact note read | About me returned owner details and a source with zero model calls | General synthesis still uses the model. |
| Plan preview | Dry run, no admission, concurrency limit two | Covers the existing read-only Work recipes. |
| Candidate parallel trial | Two fresh real tasks completed in 9.229 and 9.963 seconds; third launch returned 429 | An earlier reviewer failed with `inference_failed`; its exact upstream cause remains unknown. Its expired budget was not extended. |
| Candidate task privacy | Reviewed synthetic task removed; replay returned 410 | Notes, older Ultra runs, exports and approval queues are separate stores. |
| Activated Work task | A sourced answer completed and settled in 7.968 seconds; sources unchanged | One fresh post-activation task, not an overall model success rate. |
| Activated Word export | Valid editable OOXML; the answer and New York were preserved | Word is not installed in the checked locations; application formatting acceptance is pending. |
| Visible Work/Word flow | Completed sourced answer visible; UI reported “Word download started” | Download initiation and API content verified, not Word application rendering. |
| Task privacy review | Effects/limits shown; Keep closed the review; task remained saved | No actual task was erased through the active UI. |
| Selected-data export | Settings-only UI download and valid ZIP inspection passed; four allowlisted settings/manifest files, no recognized secrets or home paths | Other export selections retain fixture coverage and need user acceptance. |
| Local operations | Manual health-check UI completed; real task appeared in activity metadata | Daily scheduling, local battery/network collection and background speech remain off. |
| Updated desktop helper | Microphone allowed, on-device speech model ready; all three global shortcut registrations returned status 0 | Registration does not prove a physical press. Earlier PTT/Island/reply/mute were user-confirmed on the prior helper. |

Sanitized runtime evidence and the generated Word document are in the active checkout's ignored `.local/brain-evidence-20261028/` directory. The visible connected-Brain screenshot is `brain-connected.png`. Evidence contains no copied private configuration or login credentials.

## Remaining human checks

1. Unlock the Mac and finish the reversible edit-in-Obsidian → Jarvis refresh → undo check. Native editor input was unavailable after the lock; no temporary marker persisted and the starter guide remains unchanged.
2. Hold Control–Shift–Space, say “Read note About me,” then release. Check the Island and answer. Test Option–Command–M for silent text and Option–Escape to cancel.
3. In Jarvis → Brain, search New York and click Open in Obsidian. Browser automation was blocked by its external-app security policy; no alternate route was used to claim this passed.
4. Accept accessibility, click-through, Spaces/full-screen/displays, actual screen-share exclusion and end-to-end audio/performance behavior. Hands-free needs its own explicit microphone consent and acoustic/privacy/battery trial; keep it off before locking.

These are open acceptance items. The broader assistant roadmap also retains optional double-clap, additional action compensators, verified call/sharing/DND signals and individually selected communications/device/home/model integrations. See the [remaining plan](BUILD-PLAN.md).

## Identity audit

Final redacted checks found zero known-friend identifier matches in both active checkout source trees, relevant generated evidence/native bundles, private configuration identity checks, Git author metadata and Jarvis startup metadata. No unexpected literal personal home paths were found in active source outside deliberately synthetic privacy-test fixtures. No Jarvis startup registration was found. Unknown aliases, unrelated applications and rewriting Git history are outside the audit. Private configuration and credentials were not copied into evidence.

## Rollback

Stop only the new Jarvis desktop helper and server. Select retained revision `1d1027c` without discarding committed or unrelated work. Preserve the Brain, original notes, private configuration and journals. The previous browser helper is retained under the active checkout's `.local/rollback-20261028/`; the earlier accepted desktop bundle remains under the review checkout's `.local/Jarvis Desktop.app`. Restore the previous browser helper and launch the earlier desktop bundle with the earlier server, never two normal desktop helpers. Rollback is a replacement activation and should be performed deliberately.
