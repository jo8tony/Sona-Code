# Repository Guidelines

## Project Structure & Module Organization

`sona_code/` contains the Python application. Keep HTTP forwarding in `proxy/`, persistence and parsing in `recording/`, management endpoints in `admin/`, terminal support in `terminal/`, and browser assets in `web/static/`. Tests live in `tests/` and mirror these responsibilities through files such as `test_router.py` and `test_store.py`.

The desktop shell is split across `src-tauri/` (Rust/Tauri), `desktop/` (startup page), `packaging/` (PyInstaller spec), and `scripts/` (platform build helpers). Generated data belongs in `.runtime/`; build outputs under `build/`, `dist/`, and `src-tauri/target/` must remain untracked.

## OpenCode Integration & API Compatibility

The workspace uses the OpenCode V1 `opencode serve` HTTP API, not the V2 API documented at https://opencode.ai/v2/docs/api. `sona_code/workspace/manager.py` starts one local subprocess per project on demand with `opencode serve --hostname 127.0.0.1 --port <random-port>`. Each server uses a randomly generated HTTP Basic password. The browser calls this application's FastAPI workspace routes; the backend forwards requests with `httpx` and relays SSE from `/event`. OpenCode owns conversation persistence.

Current API paths include `/global/health`, `/session`, `/agent`, `/config/providers`, and `/session/{id}/prompt_async`. V2 uses paths such as `/api/info`, `/api/session`, and `/api/agent`, and is not a drop-in replacement. A V2 migration must adapt endpoints, request/response structures, and event formats, with compatibility coverage; do not assume replacing the executable or adding an `/api` prefix is sufficient. Treat the running version's `/doc` OpenAPI description as the API contract.

The Windows installer bundles a pinned OpenCode CLI version from `packaging/opencode.json` (currently `1.18.32`). macOS normally resolves OpenCode from PATH; settings also support a custom command. The workspace still expects V1 API behavior regardless of executable source. Keep version-specific assumptions aligned with the packaging manifest and verify compatibility when upgrading.

Persisted history browsing uses `workspace/history.py` read-only V1 SQLite snapshots to avoid starting project servers. Match the child process's `XDG_DATA_HOME` and `OPENCODE_DB` overrides, open with `mode=ro` and live WAL support, and never write or migrate native storage. Unsupported/legacy schemas and ambiguous channel databases fall back to the V1 HTTP API. `/workspace/projects` includes non-archived session summaries and counts before projects are expanded. Keep the snapshot projection aligned with the pinned version and run the opt-in `tests/test_models_native.py` compatibility checks after native upgrades.

The separate OpenCode terminal runs the native TUI through Windows ConPTY or macOS system PTY, with xterm.js and WebSocket transporting terminal input/output. Both workspace and terminal processes share environment setup in `terminal/manager.py`. Application providers are compiled by `admin/models.py` into namespaced native V1 providers through `OPENCODE_CONFIG_CONTENT`, without editing project or user files. Each model uses native `provider.api` and `provider.npm`; provider-level proxy switches choose dedicated `/managed/<provider-token>/<model-token>/…` recording routes or direct endpoints. Model-specific credentials override provider credentials, and empty credentials explicitly prevent native auth/environment fallback. Keep real proxy credentials out of native model metadata. Legacy terminal routing fields are read only for migration; model management is the authoritative configuration. Responses parsing and usage normalization run in the recording background, never in the forwarding loop.

Application skills are managed in `admin/skills.py` and `admin/skill_routes.py`. Import a single directory containing YAML-frontmatter `SKILL.md` and copy all resources into the application's isolated OpenCode config directory under `skills/<name>`. Enable/disable writes the official `permission.skill.<name>` allow/deny setting in the isolated `opencode.json[c]`, preserving other JSONC content; never move files to disable them. Startup migrates legacy `skills-disabled` copies into `skills` with deny permissions. Delete only application copies. Read the original OpenCode XDG config directory's `skills` and `skill` trees in place; inject non-conflicting external skill paths through native `skills.paths`. Never change original external files/config. Prefer application copies for duplicate names and mark external conflicts.

The skill page directly lists all application and external skills, with name/description search. Desktop imports use the Tauri dialog plugin's native directory picker (`core.invoke("plugin:dialog|open", {options: {directory: true}})`); plain browsers accept absolute paths. `/skills` lists enabled skills actually loaded at the expected native location and permitted for the selected agent. Selection/valid typed commands show a blue skill mention inline in the message composer; unknown commands show validation errors. Invoke through V1 `/session/{id}/command`, preserving native resource-base behavior. A generated native message ID links persisted manual invocation metadata in `.skill-uses` to the user message; history shows the selected skill inline with the task. Automatic `skill` tool calls and skill/resource reads show distinct visible cards with real pending/running/completed/error status. Never infer successful loading merely from composer selection.

Project file references also appear as blue inline mentions in the composer and user-message history, with full relative paths retained for serialization and tooltips. Derive outgoing references from the remaining composer mentions so deleting and undoing a mention update the payload; image previews remain above the input. One unmodified Backspace immediately after a skill/file mention (or its single inserted separator) deletes the whole mention through a native undoable edit, preserving surrounding text and IME composition.

Native deny permissions hide skills from the AI skill tool, but native slash commands may still exist: filter the picker/command menu and enforce disablement and agent permissions at the backend. Skill mutations reject active workspace tasks and restart idle servers to refresh native caches and process-scoped external paths; OpenCode owns conversation persistence. Existing native terminals require a restart. Cover copying, native permission persistence, legacy migration, external-source priority, traversal/symlink rejection, active-task protection, history metadata, and native command delegation in `tests/test_skills.py`.

## Build, Test, and Development Commands

- `python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"` installs the Python app and test dependencies.
- `.venv/bin/python -m sona_code --config .runtime/config.json` starts the local proxy and admin UI.
- `.venv/bin/python -m pytest tests/ -q` runs the full test suite.
- `npm ci` installs the pinned Tauri CLI dependencies.
- `npm run desktop:build` builds the macOS sidecar, `.app`, and `.dmg`.
- `npm run desktop:build:windows` builds the Windows sidecar and NSIS installer on Windows.
- `cargo fmt --manifest-path src-tauri/Cargo.toml -- --check` verifies Rust formatting.

## Coding Style & Naming Conventions

Use four spaces in Python and follow PEP 8: `snake_case` functions/modules, `PascalCase` classes, and explicit type hints for public APIs. Keep JavaScript dependency-free and consistent with existing two-space indentation and `camelCase` names. Format Rust with `cargo fmt`; prefer small functions with explicit error propagation. Preserve the proxy's byte-transparent forwarding behavior and keep parsing or disk writes off the response hot path.

## Testing Guidelines

Pytest and `pytest-asyncio` are configured in `pyproject.toml`. Name files `test_*.py`, classes `Test...`, and functions `test_*`. Add regression coverage for routing, SSE streaming, redaction, persistence, and platform-specific terminal behavior. Run all tests before pushing; `main` also builds the Windows installer in GitHub Actions.

## Commit & Pull Request Guidelines

History uses short imperative summaries in Chinese or English, sometimes Conventional Commit style (`feat(terminal): ...`). Prefer `type(scope): summary` when practical and keep each commit focused. Pull requests should explain behavior changes, list verification commands, link relevant issues, and include screenshots for Web UI changes. Call out platform-specific packaging effects.

## Security & Configuration Tips

Never commit `.runtime/`, API keys, call recordings, generated sidecars, or local demo captures. Use masked example credentials and verify header-redaction tests when touching proxy or recording code.
