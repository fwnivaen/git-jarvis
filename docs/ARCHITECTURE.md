# Architecture

Jarvis extends one local application rather than running separate assistants behind unrelated interfaces. The viewer sends requests to the loopback server; existing note, voice and vision paths remain available when Ultra is disabled.

```mermaid
flowchart TD
    UI[Galaxy, orb and voice] --> HTTP[Local Python server]
    HTTP --> Memory[Hybrid note retrieval and preferences]
    Memory --> Route[Luna routing / direct answer]
    Route -->|simple| Answer[Answer with source IDs]
    Route -->|complex| Boss[Sol boss plans work]
    Boss --> Workers[Up to 3 concurrent workers]
    Workers --> Shared[Validated reports and peer review]
    Shared --> Final[Sol boss checks and synthesizes]
    Final --> Answer
    Answer --> UI
    Workers <--> Tools[Typed Jarvis tool registry]
    Tools <--> MCP[MCP stdio adapter]
```

## Request lifecycle

1. The HTTP boundary validates the local host/origin, input shape and size. Retrieval supplies actual note excerpts and explicit saved preferences.
2. Simple questions are classified and answered in one Luna call. Explicit worker requests skip cheap classification and go directly to the boss. Manual model choices preserve the original override behavior.
3. The boss creates complementary assignments. Stable worker slots use the configured model/effort profiles and one bounded pool. Workers exchange validated reports and review available peers without waiting for the slowest draft.
4. Source IDs, exact quotations, output types and restricted arithmetic are checked locally. Only objective failures can consume the shared Luna-to-Sol promotion budget. Confidence and timeouts cannot trigger promotion.
5. The final boss receives the collected reports and retrieved passages. Executable tools are disabled during synthesis to prevent repeated operations. Failed workers and unfinished boss review are disclosed.
6. The response retains the existing answer/source contract, so citations, galaxy highlighting, camera motion and speech continue through the existing controllers.

## Boundaries and implementation choices

| Component | Responsibility |
| --- | --- |
| [server.py](../server.py) | Loopback HTTP, existing backends, bounded requests, local tool adapters and graph refresh |
| [build.py](../build.py), [note_store.py](../note_store.py) | Markdown graph creation, revision-checked reads and durable edits |
| [ultra.py](../ultra.py) | Feature flags, routing, worker profiles, objective validators and request/worker budgets |
| [codex_bridge.py](../codex_bridge.py) | One warm app-server connection, ephemeral threads, catalog checks and fixed dynamic-tool dispatch |
| [ultra_monitor.py](../ultra_monitor.py) | Allowlisted activity metadata shared by the viewer and terminal |
| [ultra_memory.py](../ultra_memory.py) | Lazy local embeddings, embedded Qdrant, keyword fusion and explicit preferences |
| [ultra_documents.py](../ultra_documents.py) | Bounded PDF/DOCX/XLSX extraction with provenance and repeat-import protection |
| [ultra_tools.py](../ultra_tools.py) | Typed tool validation and durable write deduplication |
| [ultra_workflows.py](../ultra_workflows.py) | Workflow templates, private checkpoints and observed evaluation summaries |
| [tools/ultra_mcp.py](../tools/ultra_mcp.py) | Standard stdio MCP adapter over the same tool registry |
| [viewer/galaxy.js](../viewer/galaxy.js), [viewer/layout-worker.js](../viewer/layout-worker.js) | Batched rendering, stable cached positions, picking and lifecycle control |
| [sensor_state.py](../sensor_state.py) | Expiring opt-in camera booleans and temporary reminder relief, independent of the removed Focus system |
| [native/](../native/) | Swift speech and screen-watch indicator |

There is no frontend dependency installation or build pipeline. Fonts, rendering libraries and posture assets are bundled with their notices. Optional Python integrations load only when their feature is enabled; failures retain the original keyword or chat behavior where applicable.

## Failure and recovery semantics

Each worker has a single deadline covering draft, peer wait, review, retries and promotion. A separate request budget covers the whole pipeline, including both boss calls. Recovery never replenishes those limits. Cached completed or partial answers need no model call and preserve the observed worker states and token totals.

Checkpoints store validated work products, context fingerprints, source revisions and counters. A changed question, source, preference, instruction or configuration cannot silently reuse a different run. Tool writes have pending/completed journal entries. Identical peer/retry writes are deduplicated; an uncertain interrupted write is not replayed automatically.

Monitor fields are fixed metadata such as model, phase, tool name and counters. Prompts, raw arguments, worker reports and private reasoning never enter the activity table. Token use is observed when the backend reports it; subscription token counts are not presented as a dollar bill.

## Data and permissions

The project configuration, private memory, journals, generated graph and personal captures/imports are ignored by Git. The HTTP static allowlist excludes private files. The checked-in vault contains fictional café notes for reproducible exploration.

Local posture inference sends boolean status to the server. Explicit vision requests can send one image to the configured model. Model requests can include selected note passages and preferences. Camera, microphone, display sharing and macOS automation retain their existing permission boundaries. Agent tools execute requested operations without an additional per-action approval drawer; the note editor additionally uses a durable exact-operation approval queue and exact-revision undo. That queue is not exposed to model tools. See [local controls](LOCAL-CONTROLS.md).

Camera state expires independently; there is no foreground activity polling or Focus timer. Notes, voice, models, vision and agent workflows retain their original routes.

## Scope

This is a personal local application, not a multi-tenant service. The candidate admits two task owners by default, with one ordinary chat owner and bounded internal parallelism. `TASK_MAX_CONCURRENT` controls task admission; shared model/tool capacity stays bounded at three. Screen observation is implemented; unrestricted computer control, email sending, trading and unrelated business-service adapters are outside the connected tool set. Historical visual briefs and earlier measurements are retained in [archive/](archive/), separate from [current verification](ultra-verification.md).

## Durable Work-tab workflows

The Work workspace now has an explicit LangGraph route alongside ordinary chat. `workflow_graph.py` owns graph structure; `workflow_runtime.py` adapts the existing model connection, validators and read-only tool subset. The existing HTTP server exposes start/cancel/retry actions and bounded reconnectable event streams. SQLite saves graph state and per-agent budgets. Both routes share bounded `BRAIN_SLOTS`; per-task bridge ownership and a shared three-call model semaphore prevent independent tasks from bypassing global capacity. Failed cleanup retains its slots until confirmed settlement. The original chat crew and quick route remain available. See [workflow setup and boundaries](WORKFLOWS.md) and [measured selection](framework-evaluation.md).

The native watcher polls canonical `/stage2/tasks` with a legacy fallback. Pending note-action metadata drives an actual Island review link. `/health` supplies metadata-only readiness; the HTTP wrapper declares private config/settings reads without returning their contents. Neither view creates a general external-action engine or a presence monitor.
