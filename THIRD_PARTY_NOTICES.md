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

The Windows Python sidecar also includes the pinned `@opencode-ai/plugin`
1.18.32 SDK (MIT) and its locked production dependencies to prepare a fresh
isolated native config without registry access. The SDK archive retains the
packages' original license files; dependencies use MIT, ISC, Apache-2.0, or
BSD license terms (the dual AFL/BSD package is used under BSD-3-Clause).
The complete package/version/license inventory is recorded in
`packaging/opencode-sdk/package-lock.json`. Installed files are placed only
in a fresh application OpenCode config's `node_modules` directory.
# Monaco Editor

Monaco Editor 0.57.0 (Microsoft Corporation) is distributed under the MIT license.
Local language modules, workers, CSS and fonts are bundled in `sona_code/web/static/vendor/monaco/`.
The original license and upstream third-party notices are included there as `LICENSE.txt` and `ThirdPartyNotices.txt`, including the notices for TypeScript and DOMPurify. No CDN is used.

## Local language services

The full Windows package distributes fixed, unmodified upstream components in
`tools/language-services`. No component is fetched when opening an editor.

| Component | Version | License |
| --- | --- | --- |
| Pyright | 1.1.414 | MIT |
| TypeScript Language Server | 6.0.2 | Apache-2.0 |
| TypeScript | 6.0.3 | Apache-2.0 |
| Eclipse JDT Language Server | 1.61.0 (202609031315) | EPL-2.0 |
| Eclipse/Equinox/m2e dependencies | Exact bundle versions in the manifest | Original EPL-1.0/EPL-2.0 and third-party terms |
| Temurin OpenJDK | 21.0.12.1+1 | GPL-2.0 with Classpath Exception and bundled third-party notices |
| Node.js | 22.23.3 | Node.js license and bundled third-party notices |

Original LICENSE, NOTICE, Eclipse `about.html` and `about_files` documents are
retained in the component directories and collected under `licenses/`. Logback
1.5.32 is offered under EPL-1.0 or LGPL-2.1; its matching source JAR is included.
Permissive libraries retain their individual copyright and license notices.

`sources/manifest.json` maps each distributed Eclipse bundle to its exact p2
source JAR or upstream source archive. Native Equinox launchers use the source
commit recorded in their `Eclipse-SourceReferences` headers. The older m2e
workspace CLI includes its matching 0.4.0 source JAR. The Temurin source archive,
the exact `temurin-build` commit and the official Windows build metadata are
included, alongside the Node.js, JDT LS, Pyright and TypeScript source archives.
These files are shipped with the same installer and offline rebuild kit; an
external source URL alone is not the source delivery. All archive URLs and
SHA-256 values are pinned in `packaging/language-services.json`.

Commercial use remains subject to each component's license, including notice
retention and applicable source redistribution obligations.
