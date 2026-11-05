# Verification

Jarvis separates offline regression coverage, running-server checks, and physical macOS acceptance. A passing fixture or build does not establish account access, microphone behavior, or a successful real model answer.

## Public onboarding review — November 5, 2026

Tested with Python 3.12 in an isolated demo checkout, without copying private configuration or runtime state. The owner's running server and vault were not changed.

| Check | Observed result |
| --- | --- |
| Repository hygiene and local documentation links | 360 files, 248 local links, **0 failures** |
| Python regressions | **469 passed, 45 skipped** (514 discovered); optional integration dependencies were not installed locally |
| Viewer regressions | **17 suites passed**, including the local audio suite's 13 tests |
| Full live preflight | **13 pass, 0 fail, 3 warn** |
| Live model operations | Signed-in included usage, configured-model answer, exact model switching/reset, cited note answer, and synthetic JPEG vision check passed |
| Shared Markdown vault | Connected an external copy of the 30 fictional notes; Brain local search returned sources and Read in Jarvis opened a source in Notes. Adding a fictional note and refreshing increased the indexed count to 31; a `Private/` fixture stayed excluded |
| Focus removal | No dashboard control; live removed routes returned 404 and capability/tool removal regressions passed |
| Static files and private configuration | 42 served routes matched disk; configuration paths/traversal variants were blocked; local configuration and graph data remained ignored/untracked |

The three live warnings are the two inapplicable API-backend probes and the deliberately skipped capture creation/cleanup chain. That chain was covered by isolated fixtures, not performed against the demo or owner's real vault in this review. Screenshots use fictional demonstration notes. No physical microphone, Accessibility, audible speech, or native Obsidian-app opening was accepted in this review.

## Reproduce the checks

```sh
python3 tools/check_repo.py
python3 tools/check_ultra.py
git diff --check
```

Install optional dependencies with `python3 tools/setup_ultra.py --no-model` to include their integration tests. Missing dependencies and embedding models are reported as skips. `--embeddings` exercises an installed local embedding model.

With Jarvis running, `python3 preflight.py` checks the viewer, graph, removed routes, configured model, model switching, real chat/image responses, served-file integrity, and private configuration boundaries. It makes model requests and uses your signed-in allowance. Capture creation/cleanup is skipped with a warning unless `--allow-capture-cleanup` is explicitly supplied. `--quick` skips inference and reports those chains as warnings.

## Scope

- Focus timers, activity tracking, and related tools are removed. Regression checks assert their absence; live preflight checks that `/focus` returns 404.
- The core Linux regression job and macOS native build job run in GitHub Actions. CI does not sign in to Codex or exercise physical sensors.
- Native microphone, audible output, Accessibility, global shortcuts, and external-app opening need acceptance on the user's Mac. Building a helper is not a physical acceptance result.
- Included usage and model access depend on the installed Codex runtime and signed-in account. No paid API fallback is enabled.

Earlier dated measurements remain in the [Ultra record](ultra-verification.md) and [engineering records](README.md). Historical Focus probes are not current features or current acceptance results.
