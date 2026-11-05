# Contributing to Jarvis

Start with the [README](README.md), [Obsidian guide](docs/OBSIDIAN-BRAIN.md), and [architecture](docs/ARCHITECTURE.md). The core server uses Python's standard library; the viewer is plain JavaScript with no npm build. Keep changes compatible with that lightweight path.

## Development setup

Use Python 3.11+ and a current Node.js release for the checks. To include optional integration tests, install the pinned dependencies locally:

```sh
python3 tools/setup_ultra.py --no-model
python3 tools/check_repo.py
python3 tools/check_ultra.py
```

The test runner uses `.local/ultra-full-venv/` when present. Without optional packages, integration-dependent tests report skips; a skipped test is not a verified integration. Native builds and physical sensor acceptance are separate from Python/JavaScript regressions.

## Verify a change

- Add targeted regression coverage for behavior changes; keep fixtures fictional and avoid network/account dependencies in unit tests.
- Run `python3 tools/check_repo.py`, `python3 tools/check_ultra.py`, and `git diff --check`.
- With a local server running, run `python3 preflight.py`. It exercises real model calls and model switching, so it needs a working ChatGPT sign-in and allowance. It skips capture creation/cleanup by default; `--allow-capture-cleanup` explicitly permits creating and deleting one disposable vault note. Use a separate demo vault for that check.
- For a UI-only change, `python3 preflight.py --quick` skips model calls. Inspect the original dashboard visibly; fixtures do not establish actual layout, microphone, sound, or macOS permission behavior.
- Focus is retired. Keep its routes, tools, timer, and activity tracking absent. The removal regressions and live `/focus` 404 check guard this boundary.

GitHub Actions runs repository checks and Python/viewer regressions on Linux and builds the native voice/screen-watch helpers on macOS. It has no account credentials or physical microphone/camera access.

## Keep local data private

Never include `config.json`, authentication caches, API keys, `.local/` runtime state, generated `viewer/graph-data.js`, private exports, or personal notes. Leave the ignore rules intact. Use fictional notes in tests and screenshots; keep your own vault outside this repository. Do not paste private settings or message contents into issues.

## Issues and pull requests

Report the platform, Python version, relevant command, expected behavior, actual result, and safe reproduction steps. Include a redacted error rather than a private configuration file. For a pull request, explain the user-visible behavior and list checks actually run, skips, and anything still unverified.

Keep documentation aligned with shipped code. Separate live observations from mock fixtures and historical evidence. Changes to model backends, network exposure, sensor activation, or persisted user data should explain their permissions and data flow.
