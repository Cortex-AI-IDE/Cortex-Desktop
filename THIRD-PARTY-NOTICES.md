# Third-Party Notices

Cortex Desktop is built on open-source software. This file lists the components
that are compiled into, or bundled with, a Cortex release, with the licence each
one is offered under. It is generated from `requirements.txt` and the vendored
assets in `src/`, and is updated when a dependency is added or removed.

Cortex's own source is licensed under Apache-2.0 (see `LICENSE`). Because the
application links PyQt6, the compiled application as a whole is distributed
under GPL v3. See "Binary distribution" in `README.md`.

---

## 1. Graphical shell

| Component | Licence | Notes |
|---|---|---|
| PyQt6 | GPL v3 | Riverbank's Python bindings for Qt 6. A commercial licence is required for a closed-source distribution. |
| PyQt6-Qt6 | GPL v3 / LGPL v3 | The Qt 6 binaries the bindings load. |
| PyQt6-WebEngine | GPL v3 | Python bindings for Qt WebEngine. |
| PyQt6-WebEngine-Qt6, Qt6 WebEngine | LGPL v3 | Chromium-based web engine behind the editor, chat, terminal and preview panels. |
| Qt6 | LGPL v3 | Provided as separate shared libraries under LGPL v3. |

**Practical consequence:** the combined Cortex binary is a GPL v3 work. The
complete corresponding source for every release is this repository.

## 2. Python packages

Direct dependencies from `requirements.txt`. Licences are those declared by each
upstream project.

| Package | Licence |
|---|---|
| requests, aiohttp | Apache-2.0 |
| openai | Apache-2.0 |
| orjson | Apache-2.0 / MIT (dual) |
| cryptography | Apache-2.0 OR BSD-3-Clause |
| anthropic | MIT |
| mcp | MIT |
| python-docx | MIT |
| openpyxl | MIT |
| beautifulsoup4 | MIT |
| PyYAML, urllib3, flatlatex, pywinpty | MIT |
| httpx, numpy, psutil, send2trash, pypdf, lxml, mistune, xlrd | BSD-3-Clause |
| Pygments | BSD-2-Clause |
| python-dotenv | BSD-3-Clause |
| certifi | MPL-2.0 |
| typing_extensions, defusedxml | PSF-2.0 |
| Pillow | MIT-CMU (HPND) |
| audioop-lts | MIT |

### PDF libraries (permissive)

| Library | Licence | Role |
|---|---|---|
| pypdf | BSD-3-Clause | Page count and PDF text extraction |
| pypdfium2 | Apache-2.0 OR BSD-3-Clause | Page rasterisation for the in-app PDF viewer (Chrome's PDFium renderer) |

Both are permissive, so the only copyleft obligation in a shipped binary comes
from PyQt6 (GPL v3).

Earlier builds used PyMuPDF, which is AGPL-3.0 and would have forced the whole
compiled application to AGPL-3.0 rather than GPL v3. It was replaced on
2026-10-21 for exactly that reason, and is no longer a dependency.

## 3. Vendored front-end libraries

Bundled as files under `src/ui/`, served locally with no CDN dependency.

| Library | File | Licence |
|---|---|---|
| xterm.js | `src/ui/components/assets/xterm/xterm.js` | MIT |
| xterm-addon-fit | `src/ui/components/assets/xterm/xterm-addon-fit.js` | MIT |
| marked v4.3.0 | `src/ui/components/assets/marked.min.js` | MIT |
| mermaid | `src/ui/html/ai_chat/vendor/mermaid.min.js` | MIT |

`src/ui/components/assets/highlight.min.js` is a placeholder stub, not a copy of
highlight.js, and carries no third-party code.

Syntax highlighting inside the chat is done by Pygments (BSD-2-Clause, listed
above), not by a JavaScript library.

## 4. Fonts

| Font | File | Licence |
|---|---|---|
| Inter | `InterVariable.ttf` | SIL Open Font License 1.1 |
| JetBrains Mono | `JetBrainsMono-Regular.ttf`, `JetBrainsMono-Bold.ttf` | SIL Open Font License 1.1 |

The SIL OFL permits bundling and redistribution inside an application, including
a commercial one, provided the fonts are not sold on their own and the licence
notice is retained.

## 5. Bundled agent skills

Cortex ships 248 agent skills under `src/agent/src/skills/bundled/`, collected
from several upstream projects under MIT and Apache-2.0 terms, plus original
Cortex work.

## 6. Provider APIs

Cortex connects to third-party model providers using the user's own credentials.
No provider library or model weights are redistributed here. Each provider's own
terms govern that use.

---

## Reporting an omission

If a component is missing from this file, or a licence is stated incorrectly,
open an issue at
[github.com/Cortex-AI-IDE/Cortex-Desktop/issues](https://github.com/Cortex-AI-IDE/Cortex-Desktop/issues).
