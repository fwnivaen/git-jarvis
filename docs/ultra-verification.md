# Ultra Jarvis verification

> Historical checkpoints. For the current review, see [Verification](VERIFICATION.md). Focus timers and activity tracking are removed; old Focus probes below are retained as historical evidence only.

## Compact agent visuals — October 23, 2026

The Galaxy's legacy hierarchy has been replaced by three equal agent signals, without changes to agent execution. One-, two- and three-agent states, shared-work connections, review, completion, offline/recovery, mobile, light mode, reduced motion, collapse and full view were checked. A browser-launched three-agent workflow completed in 24.8 seconds. See the [visual design, real screenshot, labelled fixture screenshots and reproducible checks](agent-visuals.md).

The current regression result is **184 Python tests passed** and **14 JavaScript suites passed**. Final full preflight: **14 pass, 0 fail, 2 warn**. Visible probe: **FOCUS PROBE: 16 pass, 0 fail | 1440x900**. An initial transient foreground-read failure and initial wrong-size probe are documented in the visual verification record; neither is counted as a pass. No Focus behavior or permissions changed.

## Earlier runtime verification

Both stages extend the existing server and viewer. The checked-in master flag remains off; these observations used an independently enabled review server on port 4701. Enable and run commands, feature flags and complete dependency details are in [ULTRA-JARVIS.md](../ULTRA-JARVIS.md). The earlier Stage 1 record is retained in [the historical archive](archive/ultra-stage-1-verification.md).

## LangGraph workflow integration — October 23, 2026

The current workflow launcher runs on **http://127.0.0.1:4700**, with private local flags enabling Ultra, tools and workflows. The committed master flag remains off. See [setup and controls](WORKFLOWS.md), [framework evaluation](framework-evaluation.md) and [measurements](framework-results.json). The earlier port-4701 observations below are retained as history.

| Check | Observed result |
| --- | --- |
| Full Python regression suite with real local embeddings | **184 tests passed**, including 21 new workflow/HTTP/transport tests |
| Existing JavaScript regression suites | **14 suites passed**; new workflow JavaScript also passed syntax validation |
| Full live preflight after final runtime changes | **14 pass, 0 fail, 2 warn** |
| Visible Focus probe | **FOCUS PROBE: 16 pass, 0 fail \| 1440x900** |
| Bounded synthetic stress | 20 completed research workflows, peak three workers, 20 excess launches rejected, cancellation and SQLite persistence verified |
| Real model workflows | Focused answer and three-agent research completed with checked citations; research took 16.722 seconds in one trial |
| Real model cancellation and recovery | Cancelled then resumed within the original budget; complete with three citations and thirteen tool calls |
| Corrected tool-execution handoff | Browser-launched task completed with two real tool calls; final answer accurately reported successful note reading |
| Visible UI | Launch, running agents, completion, cancel/retry, saved-run selection and source links into native Notes verified |

The final UI-only refinement retained selected history controls and expanded findings across polls; its quick preflight recorded **9 pass, 0 fail, 7 warn** (model checks intentionally skipped). The complete runtime validation above used the full preflight.

[Verified workflow screenshot](screenshots/workflows-complete.jpg)

Full preflight summary:

```text
14 pass, 0 fail, 2 warn
```

Both warnings are API-key/model checks skipped because this installation uses Codex sign-in. An earlier validation had two failures: a missing scratch-directory fixture in a new transport test, and a transient live foreground-read failure. The fixture was corrected and the final full preflight passed. The workflow boundary was also corrected after a real answer incorrectly denied a successful tool call; the follow-up live answer was inspected against the opened note.

The final visible 1440×900 probe title was read in full:

```text
FOCUS PROBE: 16 pass, 0 fail | 1440x900 | PASS viewport | PASS visible | PASS reload-read-only | PASS focused | PASS deferred | PASS unavailable | PASS paused | PASS drift | PASS excused | PASS home | PASS intent-window | PASS commands | PASS pixel-identical | PASS pixel-change | PASS disconnect | PASS visible-at-finish
```

The temporary viewport override was reset. Initial wrong-size probe runs failed and are not counted as passes. Diagnostics came from the running port-4700 service; the latest check reported `front_readable: true`. The session ledger had five entries and the legacy aggregate six keys. The current browser inventory did not expose a window count; that instrument is recorded as unavailable. The controlled browser host was `127.0.0.1:4700`. No Focus behavior or operating-system permissions were changed.

Automated failure tests use isolated temporary data and a deterministic model boundary, while live workflow trials use the existing signed-in model transport. No claims of broad accuracy, dollar-cost savings or exactly-once remote inference are made. Workflow recovery is read-only, bounded by the original deadline and available attempts. Raw journals, sources and credentials remain untracked.

## Repository release check — October 23, 2026

The repository now has a concise project overview, architecture and usage guides, current screenshots, an indexed historical archive and automated GitHub checks. Private settings, personal captures/imports, environments, downloaded models and runtime state remain ignored. A credential-pattern scan found no matches in 381 historical blobs; the tracked-file and documentation check reports **164 files, 100 local links, 0 failures**.

| Check | Observed result |
| --- | --- |
| Local Python suite with real embeddings | **163 tests passed** |
| JavaScript regressions | **14 suites passed** |
| Clean clone without private configuration or credentials | **163 tests run, 162 passed, 1 optional embedding test skipped**; all **14 JavaScript suites passed** |
| GitHub CI | [Python/viewer regressions and native macOS helper builds passed](https://github.com/fwnivaen/git-jarvis/actions/runs/39911589688) |
| Latest full live preflight | **14 pass, 0 fail, 2 warn** |
| Fresh visible Focus probe | **FOCUS PROBE: 16 pass, 0 fail \| 1440x900**; the full title was read and the temporary viewport override was reset |

The clean-clone check exposed three HTTP tests that relied on the developer's private settings. Their fixtures now supply in-memory test settings; the application behavior is unchanged. The optional embedding test is skipped in a fresh clone without the downloaded model and passes separately in the local environment. Both preflight warnings are API-key/model checks skipped because this installation uses Codex sign-in.

The latest preflight passed the running server's Focus instruments as well as real model switching, chat, image recognition, capture/recall/cleanup, served-file integrity and browser restrictions. The earlier foreground-tab failures below remain relevant: a successful run does not establish reliable physical foreground tracking across every window or permission state. No Focus implementation or macOS permission changes were made during repository cleanup.

## Upgrade validation — October 22, 2026

The following measurements retain the original upgrade review and its limitations.

| Check | Observed result |
| --- | --- |
| Python suite in the local virtual environment | **163 tests passed**, including the real local embedding, persistent vector search, PDF/DOCX/XLSX imports and MCP stdio checks |
| JavaScript regressions | **14 suites passed**, covering the existing galaxy, orb, voice, Focus, camera, screen watch, capture/source behavior and both crew suites |
| October 22 final full live preflight | **13 pass, 1 fail, 2 warn**; the failure is the running server's foreground-tab read |
| Visible Focus probe | **FOCUS PROBE: 16 pass, 0 fail \| 1440x900** |
| Real dynamic tool call | `preferences_get` completed through the warm Codex connection in **8.7 seconds** end to end; one registry call, zero workers and zero escalations |
| Real image request | Full preflight correctly identified the red JPEG through the cheap Luna vision route |
| Real three-worker collaboration | Boss planning and final review completed; Sol medium and Luna high each drafted and reviewed available peer findings; Luna max reached its **45-second** draft limit; a boss-reviewed **partial** answer was returned |
| Read back that saved partial answer | **2.08 seconds**, no model call, the same observed evaluation totals and `resumable: false` |
| MCP protocol | A real stdio client negotiated **2026-07-28**, discovered all **12 tools** and called the local registry |
| Source hygiene | No secrets, notes, generated graph data, models, virtual environments or private runtime journals are included in the upgrade archive |

The two preflight warnings are the existing API-key/model checks skipped because this installation uses Codex sign-in. That live preflight also passed note retrieval, capture followed by immediate recall and cleanup, model switching/reset, served-file integrity and browser access restrictions. An earlier full run passed **14 pass, 0 fail, 2 warn**, but subsequent October 22 runs failed the foreground check, first on activity and then on the front tab. The October 22 final result is retained here; the latest release check appears above.

Field diagnostics were fetched from the actual running review server. Its Focus tick was alive and fresh, with no active session or armed target. A subsequent diagnostic could read the foreground application but reported browser-tab access unavailable. The session ledger had five entries and the legacy ledger had six counter keys. Chrome's Window menu listed **five windows**. The controlled tab host was `127.0.0.1:4701`, while the physical foreground window differed and changed during inspection. UI control was then left alone. No identities were added to diagnostics, and no Focus implementation or permission changes were made. The simulated probe below does not establish reliable physical foreground tracking.

The final visible probe title was read in full at an asserted actual page viewport of 1440 × 900:

```text
FOCUS PROBE: 16 pass, 0 fail | 1440x900 | PASS viewport | PASS visible | PASS reload-read-only | PASS focused | PASS deferred | PASS unavailable | PASS paused | PASS drift | PASS excused | PASS home | PASS intent-window | PASS commands | PASS pixel-identical | PASS pixel-change | PASS disconnect | PASS visible-at-finish
```

## Collaboration and limits

The final live collaboration took **110.6 seconds** end to end. Its monitor measured **109.6 seconds**, five model jobs, four completed jobs, one failed worker, zero retries, zero escalations and **191,955 reported tokens**. Those are observations for this run, not a general speed or cost claim. The failed worker was Luna max. Its timeout did not hold the other workers behind a shared draft barrier: they exchanged and reviewed available findings before the boss synthesized the partial answer.

Earlier live trials exposed two issues that were corrected: final synthesis repeated retrieval instead of using all supplied tool sources, and a global draft barrier consumed the faster workers' review budget while one slow worker drafted. Final synthesis now receives all validated reports and retrieved passages with executable tools disabled. Drafts and reviews share one pool of at most three workers, and reviews start when peer findings become available. Regression tests cover the source handoff, shared worker deadlines, one peer-review round and parallelism bound.

The requested max-effort settings can still exceed the fixed 45-second worker budget. They were preserved. A worker timeout remains visible and the answer is marked partial. A boss timeout produces an explicit “not received a final boss review” notice with only locally checked findings. Neither timeout nor self-reported confidence triggers escalation.

Checkpoint tests cover context mismatches, original absolute deadlines, shared retry/escalation counters, source revisions, all observed token usage, completed and partial answer readback, and retained worker statuses in the monitor. New checkpoints preserve the full metadata roster so reopening an answer cannot mislabel previously used workers as “Not needed.” Tool journals deduplicate identical writes from peers and retries; interrupted uncertain writes are not automatically repeated. A deliberately slow local callback also confirmed that a worker's model wait remains bounded by its deadline.

## Memory, documents and tools

The isolated Python 3.12 environment contains the pinned official releases listed in `requirements-ultra.txt`. A real `BAAI/bge-small-en-v1.5` embedding produced **384 dimensions**. A synonym query found the matching note, and the embedded Qdrant index reopened successfully from local disk. Missing optional dependencies and embedding/index failures retain the original keyword retrieval behavior.

Real PDF, DOCX and XLSX fixtures were converted to Markdown. Tests cover paragraph/table order, formulas retained as text, provenance hashes, repeat-import reuse, refusal to overwrite edited imports, bounded input sizes, path/symlink restrictions and imported titles surviving server rebuilds. Text extraction does not perform OCR or reconstruct chart images.

The same typed tool registry serves crew calls, local HTTP and MCP. Tests cover all feature flags, explicit persistent preferences, local origin boundaries, current note revisions, screen request/confirmation distinctions and refusal of arbitrary built-in shell/file/browser tools. The installed Codex build requires the documented experimental dynamic-tool protocol and its `code_mode_host`; code-mode execution and unrelated tools remain disabled. Real tool execution was verified after applying that compatibility setting.

## Visual verification

The top-left glassy hierarchy retains the requested Sol max boss, Sol medium worker, Luna max worker and Luna high worker. Avatars are stable while their status changes. The nearby drawer shows only safe activity metadata; model output, prompts and private reasoning do not enter its table or terminal counterpart.

Desktop, compact mobile, collapse/reopen and Settings memory/import controls were inspected in a visible browser. At an actual **390 × 844** viewport, all four figures fit without horizontal overflow. Full view and other workspaces hide the crew, and reduced-motion preferences stop the decorative animation. Temporary viewport overrides were reset after checking.

- [Finished desktop](screenshots/ultra-complete-desktop.jpg)
- [Actual live collaboration](screenshots/ultra-collaboration-live.jpg)
- [Memory and import controls](screenshots/ultra-memory-settings.jpg)
- [Compact mobile crew](screenshots/ultra-complete-mobile.jpg)

**UNVERIFIED:** physical webcam imagery and audible native speech in this session, OCR/chart reconstruction, the prompt's benchmark and price ratios, general latency/cost savings, universal subscription model entitlement and future compatibility of experimental app-server APIs. Existing voice/camera controller regressions pass; those do not establish physical audio or camera results.
