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
- Windows asset: `opencode-windows-x64-baseline.zip`
- License: MIT

The Windows installer redistributes the official, unmodified OpenCode CLI
binary published by the OpenCode project. Its license is included at
`licenses/OpenCode-LICENSE.txt` in the installed application resources.
