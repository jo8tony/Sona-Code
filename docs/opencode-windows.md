# Windows OpenCode language-resource compatibility build

Sona Code bundles OpenCode 1.18.32 from upstream commit
`545f51d26cc39a907d2867492d498d9607ea5fa4`, compiled with Bun 1.3.14 for the
Windows x64 baseline target. The source archive and compiler archive are
SHA-256 checked. Installation uses the upstream frozen lockfile, a dedicated
Bun package cache, and the public npm registry.
The preparer retries once only for Bun's known `ENOTEMPTY` patched-cache
rename conflict; both attempts use the same frozen lockfile. Other failures,
or a second conflict, stop preparation.
Dependency lifecycle scripts are skipped: this Bun target uses shipped WASM
grammars and prebuilt assets rather than the optional Node.js tree-sitter
C++ bindings. The preparer checks that both required grammar files exist,
then builds the upstream embedded web UI and CLI. No system C++ toolchain or
global npm hooks are installed by this preparation step.

The Windows build plugin replaces the exact y18n 5.0.8 locale reader with
in-memory dictionaries from yargs 18.0.0. Exact locale selection, language
fallback, plural formatting, and unknown-language behavior remain supported.
The plugin refuses changed source and the build requires exactly one patched
module. It does not intercept general filesystem errors.

## Build and check

Run `python scripts/prepare-opencode.py` on Windows x64. The PowerShell helper
used by both desktop packages delegates to this script. The Windows workflow
uses Node.js 24 for the desktop tooling and patch regression. No global OpenCode
or Bun installation is changed.

Build inputs, the first models.dev snapshot for a recipe, and output live in
the ignored `build/opencode/` directory. The models snapshot follows the
upstream build process and is recorded by SHA-256 in the receipt. Cached
binaries are reused only when their receipt matches the source/compiler/
patch recipe and the executable checksum; `--version` and `--help` are tested
on every preparation. This is a source compatibility build, not a claim of
bit-for-bit equivalence to the upstream signed executable.

Run the native tests against the prepared executable:

```powershell
$env:OPENCODE_TEST_BINARIES = (Resolve-Path src-tauri/binaries/opencode-x86_64-pc-windows-msvc.exe).Path
python -m pytest tests/test_models_native.py tests/test_workspace_queue_native.py -q
python -m pytest tests/ -q
```

The Windows workflow also checks that both installed packages contain this
same verified executable and exercises their workspace and terminal startup.
For local payload checks while a desktop instance is in use, pass
`--sidecars-only` to `scripts/smoke-windows-package.py`. This checks CLI help,
workspace startup, history, and ConPTY without launching the desktop. It is
not a substitute for the full installed-package checks required by CI.

## Local validation on 2026-10-08

- The patched Windows x64 baseline executable built successfully with the
  pinned compiler and passed `--version` and `--help`.
- Full pytest: 327 passed, 9 opt-in/environment checks skipped. Native V1
  tests were run separately against the patched executable: 5 passed,
  including `/doc`, model requests, SSE, queue behavior, and native history.
- The Electron NSIS artifact built successfully. Its extracted OpenCode
  payload matched the prepared executable's SHA-256 and passed the payload
  checks above. A live desktop instance prevented safely exercising its
  installer hooks and desktop lifecycle in this session.
- This machine lacks the Rust/MSVC toolchain needed to build the Tauri
  installer. Both complete installed-package checks remain CI requirements.
- The original executable already starts on this machine. Verification on
  a previously affected computer is still outstanding; no release evidence
  has been recorded and the release gate remains closed.

## Release gate

Building a test artifact does not authorize release. Before publishing, test
the installers on at least one previously affected Windows computer as a
normal user, and confirm both startup and a completed message. Record the
original locale error and Windows version, without personal credentials.

Get the current recipe with `python scripts/prepare-opencode.py --print-recipe`.
Then fill in `release_validation` in `packaging/opencode.json`:

```json
{
  "recipe_sha256": "the recipe printed by the script",
  "affected_windows": {
    "normal_user": true,
    "startup_and_message": true,
    "tested_on": "YYYY-MM-DD",
    "windows_version": "the tested Windows version",
    "original_error": "the original EPERM/EUNKNOWN locale error"
  }
}
```

`python scripts/prepare-opencode.py --release-check` fails until that evidence
exists for the current recipe. Source, compiler, plugin, or preparer changes
invalidate the evidence. The CI publish step runs this gate after the native
compatibility tests and both installed-package smoke tests. The initial
validation fields intentionally remain empty.

## Startup diagnostics

Workspace servers continuously drain merged stdout/stderr into a 64 KiB tail.
An incomplete first line cut by truncation is omitted so a partial credential
cannot leak. Diagnostics strip ANSI/control escapes and mask known provider,
model, inline-config, environment, and Basic-auth credentials, plus credential
headers. The output reader closes without waiting for inherited child pipes.

Only `EPERM` or `EUNKNOWN` opening `B:\~BUN\locales\*.json` is classified as
this known error. It stops immediate repeated launches and suggests upgrading
Sona Code, or selecting the bundled build when using a custom/PATH executable.
Other failures retain three attempts and report the source, exit/timeout,
and safe error summary through the existing HTTP 503 `detail` string. Logs
also contain the executable path, version, and bounded diagnostic output.
