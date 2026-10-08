# Third-Party Notices

## OpenSpec runtime

- OpenSpec: https://github.com/Fission-AI/OpenSpec, version 1.14.1, MIT.
- Node.js: https://nodejs.org/, version 22.23.3, license and third-party notices
  included in `tools/openspec/licenses/Node-LICENSE.txt`.
- Go runtime used by the native launcher: version 1.27.1, BSD-style license
  included in `tools/openspec/licenses/Go-LICENSE.txt`.

The complete, locked OpenSpec production dependency tree is distributed in
`tools/openspec/package/node_modules`, retaining package licenses. OpenSpec's
license and the dependency version/license inventory are under
`tools/openspec/licenses/`. Official command and skill templates are generated
from the same pinned OpenSpec package at build time.

## OpenCode

- Project: https://github.com/anomalyco/opencode
- Bundled version: 1.18.32
- Upstream source commit: `545f51d26cc39a907d2867492d498d9607ea5fa4`
- Windows target: `opencode-windows-x64-baseline`
- Compiler: Bun 1.3.14
- Sona Code patch: `embedded-yargs-locales-v1`
- License: MIT

The Windows installer includes a modified build of OpenCode, compiled from
the pinned upstream source and dependency lockfile. Sona Code embeds yargs
language dictionaries so y18n does not try to read CLI locale resources from
a physical `B:\~BUN\locales` path. The V1 HTTP API and native storage are
unchanged. Its license is included at `licenses/OpenCode-LICENSE.txt` in the
installed application resources. The patch and build recipe are maintained
in this repository; release requires ordinary-user verification on an
affected Windows computer.
