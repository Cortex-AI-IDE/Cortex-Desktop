# Third-Party Notices

Cortex Desktop is built on open-source software. This file lists the components
compiled into, or bundled with, a Cortex release, and the licence each one is
offered under. It is maintained by hand and updated whenever a dependency is
added, removed, or replaced.

Cortex's own source is licensed under Apache-2.0. See `LICENSE`.

Because Cortex links PyQt6, a compiled Cortex binary is distributed as a GPL v3
work. The complete corresponding source for every release is this repository.
See "Binary distribution" in `README.md`.

---

## 1. Graphical shell

| Component | Licence | Notes |
|---|---|---|
| PyQt6 | GPL v3 | Riverbank's Python bindings for Qt 6 |
| PyQt6-Qt6 | GPL v3 / LGPL v3 | The Qt 6 binaries the bindings load |
| PyQt6-WebEngine | GPL v3 | Python bindings for Qt WebEngine |
| PyQt6-WebEngine-Qt6, Qt6 WebEngine | LGPL v3 | Chromium-based engine behind the editor, chat, terminal and preview panels |
| Qt6 | LGPL v3 | Provided as separate shared libraries under LGPL v3 |

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
| audioop-lts | MIT |
| httpx, numpy, psutil, send2trash, pypdf, lxml, mistune, xlrd | BSD-3-Clause |
| python-dotenv | BSD-3-Clause |
| Pygments | BSD-2-Clause |
| certifi | MPL-2.0 |
| typing_extensions, defusedxml | PSF-2.0 |
| Pillow | MIT-CMU (HPND) |

### 2.1 PDF rendering

| Library | Licence | Role |
|---|---|---|
| pypdf | BSD-3-Clause | Page count and text extraction |
| pypdfium2 | Apache-2.0 OR BSD-3-Clause | Page rasterisation for the in-app PDF viewer |

Both are permissive. The only copyleft obligation in a shipped binary comes from
PyQt6 and Qt 6.

## 3. Front-end libraries

Bundled as files under `src/ui/` and served locally, with no CDN dependency.

| Library | File | Licence |
|---|---|---|
| xterm.js | `src/ui/components/assets/xterm/xterm.js` | MIT |
| xterm-addon-fit | `src/ui/components/assets/xterm/xterm-addon-fit.js` | MIT |
| marked v4.3.0 | `src/ui/components/assets/marked.min.js` | MIT |
| mermaid | `src/ui/html/ai_chat/vendor/mermaid.min.js` | MIT |

`src/ui/components/assets/highlight.min.js` is a placeholder stub and carries no
third-party code. Syntax highlighting in the chat is performed by Pygments
(BSD-2-Clause), not by a JavaScript library.

## 4. Fonts

| Font | File | Licence |
|---|---|---|
| Inter | `InterVariable.ttf` | SIL Open Font License 1.1 |
| JetBrains Mono | `JetBrainsMono-Regular.ttf`, `JetBrainsMono-Bold.ttf` | SIL Open Font License 1.1 |

The SIL OFL permits bundling and redistribution inside an application, including
a commercial one, provided the fonts are not sold on their own and the licence
notice is retained.

## 5. Bundled skills

Cortex ships 249 agent skills under `src/agent/src/skills/bundled/`. They come
from several upstream projects under different terms, so the licence is recorded
per skill rather than assumed for the folder as a whole.

35 of the 249 carry their own `LICENSE.txt`:

| Upstream | Licence | Skills |
|---|---|---|
| Anthropic | Apache-2.0 | algorithmic-art, brand-guidelines, canvas-design, claude-api, doc-coauthoring, frontend-design, internal-comms, mcp-builder, skill-creator, slack-gif-creator, theme-factory, web-artifacts-builder, webapp-testing (13) |
| AgriciDaniel | MIT | seo, seo-audit, seo-backlinks, seo-competitor-pages, seo-content, seo-dataforseo, seo-firecrawl, seo-geo, seo-google, seo-hreflang, seo-image-gen, seo-images, seo-local, seo-maps, seo-page, seo-plan, seo-programmatic, seo-schema, seo-sitemap, seo-technical (20) |
| Cloudflare | MIT | security-audit (1) |
| puneetindersingh | MIT | seo-content-brief (1) |

The remaining skills are Cortex's own work and are covered by the root
Apache-2.0 licence.

Apache-2.0 requires a copy of the licence, and any upstream `NOTICE`, to travel
with redistributed copies (Apache-2.0 §4(a) and §4(d)). MIT requires the
copyright and permission notice to be retained (MIT §1). The per-skill
`LICENSE.txt` files satisfy those obligations for the 35 skills listed above.

## 6. Provider APIs

Cortex connects to third-party model providers using the user's own credentials.
No provider library or model weights are redistributed here. Each provider's own
terms govern that use.

---

## Corrections

If a component is missing from this file, or a licence is stated incorrectly,
open an issue at
[github.com/Cortex-AI-IDE/Cortex-Desktop/issues](https://github.com/Cortex-AI-IDE/Cortex-Desktop/issues).
