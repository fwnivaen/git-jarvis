# Ultra Jarvis — complete upgrade

For the basic demo and Obsidian connection, start with the [README](README.md) and [vault guide](docs/OBSIDIAN-BRAIN.md). This guide covers optional agent, memory, and workflow capabilities. The shipped usage policy allows signed-in Codex only; API keys do not enable OpenRouter or other paid backends. Focus timers and activity tracking are removed.

This extends the existing Python server, Markdown vault, galaxy, orb, vision and native voice. The lightweight runtime incorporates CrewAI-style manager delegation, named roles, shared reports, peer review, structured evidence, reusable workflows, checkpoints, recovery and measured evaluations. It does not install the CrewAI framework. Agent tools perform requested operations without an additional per-action approval drawer; existing editor confirmations and sensor permissions still apply.

## Start on macOS

The checked-in master flag is off so the existing route remains available until you enable Ultra. From a fresh clone, install the optional dependencies into the project-local environment, enable the feature and start the launcher:

```sh
git clone https://github.com/fwnivaen/git-jarvis.git
cd git-jarvis
python3 tools/setup_ultra.py
python3 tools/ultra_settings.py enable
./start.sh
```

Open `http://127.0.0.1:4700`. If that port is already occupied, stop the existing Jarvis process in its own Terminal before starting the upgraded server. Codex uses its existing ChatGPT sign-in; no API key is added or printed. If Codex is not signed in on another Mac, use its normal `codex login` flow.

Setup requires Python 3.11 or newer. It can also locate an available bundled Codex Python and never installs packages globally. To choose explicitly, pass `python3 tools/setup_ultra.py --python /absolute/path/to/python3.12`. The first setup downloads a public embedding model; search after setup uses the local cache. `--no-model` skips that download, retaining keyword fallback. Native speech and screen-watch helpers can be built separately with Apple's command-line tools. You can instead run `.local/ultra-full-venv/bin/python build.py` followed by `.local/ultra-full-venv/bin/python server.py` and build native helpers separately.

To return to the existing route:

```sh
python3 tools/ultra_settings.py disable
```

Flags are reread for each request. No edits to `config.json` are needed. Existing editor/save controls and browser camera, microphone and display permissions continue to apply.

## Crew and routing

| Role | Exact model ID | Effort |
| --- | --- | --- |
| Jarvis / boss | `gpt-6.1-sol` | max |
| Worker 01 | `gpt-6.1-sol` | medium |
| Worker 02 | `gpt-6-luna` | max |
| Worker 03 | `gpt-6-luna` | high |
| Quick chat, retrieval, summaries, vision | `gpt-6-luna` | high |
| Explicit manual override | `gpt-6-astra` | high |

Quick requests use one Luna call. Complex requests go to the boss, which assigns up to three complementary tasks. Workers exchange validated findings and review available peer reports as they arrive. The boss receives the reports and all retrieved sources, checks them and gives the user one answer. Final synthesis has no executable tools, preventing duplicate actions and repeated retrieval. Workers and direct-answer agents can use the shared tools.

The top-left glassy personnel display shows a crowned boss above three workers. The nearby collapsible activity drawer and terminal table show actual models, effort, phases, tool names, time, retries and tokens. They never show prompts, report text or chain-of-thought. Reasoning and plan notifications are discarded at the bridge.

`max_workers` defaults to 3 and can be raised to 16. Additional workers use Luna high. Each worker has one 45-second deadline spanning its draft, peer wait, review, two retries and any promotion. The request budget defaults to 240 seconds and can be configured up to 300. Boss calls share that request budget. Slow workers do not hold all peers behind a draft barrier. Slow local tools do not extend a model worker's wait; timed-out or uncertain writes are never automatically replayed.

One shared escalation per request can promote Luna to Sol after malformed structured output, missing/mismatched source quotes or failed arithmetic checks. Transport errors, timeouts and self-reported confidence do not cause promotion. Astra is never selected automatically. Code workflows review supplied code and suggest fixes; they cannot claim tests or code execution occurred because no shell/test execution tool is exposed.

Failures stay visible. Valid drafts survive a failed peer review. If boss review times out, the response clearly labels locally checked worker findings as awaiting boss review. No incomplete result is represented as fully reviewed.

In the final live trial, Luna max reached its 45-second limit while Sol medium and Luna high completed their drafts and peer reviews. The boss returned a clearly marked partial answer in 110.6 seconds. The requested models and limits are unchanged; see `docs/ultra-verification.md` for measured results.

## Memory, imports and preferences

Local Qdrant vectors and FastEmbed embeddings are merged with keyword rankings. Returned note passages remain verbatim for exact source checking. Missing dependencies, model files or index failures restore the existing keyword retrieval path. Unchanged index content is reused across sessions; note changes invalidate the index.

Use **Notes → Import a document**, or **Settings → Memory**, to turn PDF, DOCX and XLSX files into notes under `notes/imports/`. Limits are 16 MiB per input, 200 PDF pages, 40 sheets, 10,000 spreadsheet rows, 100 columns and 200,000 extracted characters. Imports preserve provenance and a source hash, reuse identical imports, and refuse to overwrite an edited imported note. DOCX paragraphs and tables retain their order. Spreadsheet formulas remain text and are never executed. Scanned PDFs require OCR first; charts, embedded images and scanned content are not reconstructed by these text converters.

Preferences explicitly supplied by you persist under `.local/ultra-memory/`. Add or forget them in Settings, or ask Jarvis to save a preference. They are included as subordinate data in subsequent sessions. Credential fields are rejected. There is no inferred personal profiling.

To import from a Terminal:

```sh
python3 tools/ultra_workflow.py import --file '/absolute/path/to/report.pdf'
```

For a model-triggered import, place the file in `.local/ultra-inbox/` yourself and provide its filename. Agents cannot import arbitrary filesystem paths.

## Standard tools

The same validated registry backs the crew, loopback HTTP and the standard MCP stdio adapter:

- `notes_search`, `note_read`, `notes_capture`, `note_write`
- `preferences_get`, `preferences_set`, `preferences_forget`
- `screen_watch_status`, `screen_watch_control`
- `document_import`

Note edits require the current file revision to prevent stale overwrites. A request-local durable journal deduplicates identical writes from peers and retries. An interrupted write with an uncertain outcome must be inspected before a fresh request; it is not replayed on recovery. There are no shell, arbitrary file, email or unrestricted browser tools.

Screen control requests are applied by the open Jarvis viewer. A start request can reuse an existing Entire Screen share; otherwise the viewer tells you to click its share control. Tool results distinguish a request from confirmed active watching. Frame comparisons stay local, unchanged automatic frames are skipped, and vision begins with Luna.

Launch the MCP adapter from a host that supports standard stdio servers:

```sh
.local/ultra-full-venv/bin/python tools/ultra_mcp.py
```

Use the absolute executable and script paths when configuring another host. The adapter connects only to local Jarvis and accepts `--url http://127.0.0.1:4701` for the review server. MCP client configuration formats and client-side permission policies belong to that host; Jarvis does not modify them.

## Workflows and recovery

Settings offers Automatic, General analysis, Evidence research, Code review and Document analysis. Automatic preserves cheap direct routing. A selected workflow applies to subsequent chat requests until changed back.

```sh
python3 tools/ultra_workflow.py templates
python3 tools/ultra_workflow.py run --workflow research --question 'Compare Menu Pricing and Launch Campaign.'
python3 tools/ultra_workflow.py runs
python3 tools/ultra_workflow.py resume --run-id 'THE_RUN_ID' --question 'THE_EXACT_ORIGINAL_QUESTION'
```

All commands accept `--url http://127.0.0.1:4701` for the review server. The response includes the real run ID, recovery status and observed evaluation counters.

Private checkpoints retain validated reports, excerpts, conversation history and counters. Recovery requires the original question and matching sources, preferences, instructions, settings and workflow. It reuses completed work and original deadlines, retry counts, escalation budget and tool journal. Waiting or restarting never replenishes a budget. Completed answers can be read back without a model call. Evaluations report observed source/math checks, retries, failures, tokens and elapsed time; resumed attempts retain earlier token usage. No LLM confidence score or fabricated dollar estimate is used.

## Flags and fallbacks

Every addition is controlled in the nonsecret `ultra.json`:

| Flags | Effect when off |
| --- | --- |
| `enabled`, `routing_enabled` | Existing chat route |
| `parallel_workers_enabled` | Sequential boss answer |
| `crew_profiles_enabled` | Original high-effort routing profile |
| `crew_visual_enabled`, `live_monitor_enabled`, `terminal_monitor_enabled` | Hide the corresponding visualization |
| `collaboration_enabled`, `structured_reports_enabled` | Skip peer review or additional report fields |
| `workflows_enabled`, `checkpoint_enabled`, `evaluation_enabled` | Disable the corresponding workflow capability |
| `memory_enabled`, `semantic_search_enabled` | Existing keyword retrieval; memory off also omits preferences |
| `document_import_enabled`, `preferences_enabled` | Disable imports or saved preferences |
| `tools_enabled`, `mcp_enabled` | Disable crew tools or the external MCP adapter |
| `cheap_vision_enabled`, `skip_unchanged_frames_enabled` | Existing vision path or frame behavior |

An invalid or missing master configuration restores the existing behavior. Explicit recovery/workflow requests are honestly refused when their feature is disabled, rather than silently starting a different run. Local private storage is excluded from Git and the browser's static-file allowlist. Model availability and effort support are checked against the signed-in account's catalog before inference.

## Run all checks

```sh
python3 tools/check_ultra.py --embeddings
python3 preflight.py
```

Run the second command with the upgraded server active. Capture creation/cleanup is skipped unless you explicitly pass `--allow-capture-cleanup`; without that flag, notes are preserved and the skipped chain is a warning. For a separate test server use `python3 preflight.py --url http://127.0.0.1:4701`. Inspect the original Jarvis dashboard visibly. Focus and its probe are removed. Current review results are in [Verification](docs/VERIFICATION.md); [earlier Ultra measurements](docs/ultra-verification.md) are historical.

## Verified dependencies and APIs — October 22, 2026

`requirements-ultra.txt` pins releases checked against official PyPI metadata before installation: qdrant-client 1.20.0, fastembed 0.9.0, pypdf 6.21.2, python-docx 1.2.0, openpyxl 3.1.5 and mcp 2.3.0. The server imports those packages only inside their enabled features. The bridge and orchestration remain standard-library Python.

- [OpenAI model catalog](https://developers.openai.com/api/docs/models) and [Codex app-server protocol](https://learn.chatgpt.com/docs/app-server). The installed CLI's generated stable and experimental JSON schemas were checked. `dynamicTools` and `item/tool/call` are documented **experimental** APIs, opted into explicitly. This installed build also requires `code_mode_host`; its stable host is enabled while code-mode, shell and unrelated model tools remain disabled. The tool boundary was exercised with real calls.
- [Qdrant Python client](https://github.com/qdrant/qdrant-client) and [FastEmbed supported models](https://qdrant.github.io/fastembed/examples/Supported_Models/). The local embedding is `BAAI/bge-small-en-v1.5`, 384 dimensions; its actual output and local persistent search were tested.
- [pypdf extraction](https://pypdf.readthedocs.io/en/stable/user/extract-text.html), [python-docx](https://python-docx.readthedocs.io/en/latest/user/quickstart.html), [openpyxl](https://openpyxl.readthedocs.io/en/stable/tutorial.html) and their installed public APIs. These converters provide the required bounded text imports without installing Docling's larger OCR stack.
- [Official MCP Python SDK v2](https://py.sdk.modelcontextprotocol.io/) and [client API](https://py.sdk.modelcontextprotocol.io/client/). A real stdio client negotiated the protocol, discovered all 12 tools and called the local registry.
- [CrewAI hierarchical process](https://docs.crewai.com/en/learn/hierarchical-process), [task outputs](https://docs.crewai.com/en/concepts/tasks) and [flows](https://docs.crewai.com/en/concepts/flows) inform the selected patterns. CrewAI, LangGraph, AG2, Mem0, Phoenix, Playwright MCP and RealtimeSTT are not installed. Explicit local preferences supply the requested persistent preference memory.

**UNVERIFIED:** the prompt's exact benchmark and price ratios, universal subscription entitlement, general latency/cost savings and future compatibility of experimental app-server APIs. Actual timings, timeout behavior and token use are reported as observations in the verification record.

## Complete files

The accompanying upgrade archive contains complete source files, tests, pinned requirements, tools, this guide and verification evidence. It is an overlay for the existing Jarvis installation. It excludes `config.json`, credentials, your notes, generated graph data, private journals, downloaded models, virtual environments and native binaries. Keep those existing local files in place.
