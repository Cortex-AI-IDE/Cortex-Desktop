# Contributing to Cortex AI IDE

Thanks for taking the time to help. This document covers the practical
workflow: what to set up, how to run the code, what to include in a pull
request, and how to report a bug so it can actually be fixed.

By contributing you agree that your contribution is licensed under the
[Apache License 2.0](LICENSE), the same licence that covers this repository.

---

## Before you start

Read [README.md](README.md) and get the app running from source first. A change
you cannot run is a change you cannot verify.

You will need:

- Windows 10 or 11, 64-bit
- Python 3.11 or newer
- Node.js 18 or newer, plus `npm install` at the project root
- Git

Setting up:

```powershell
git clone https://github.com/Cortex-AI-IDE/Cortex-Desktop.git
cd Cortex-Desktop
npm install
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python src/main.py
```

There is no hot reload. Restart the app after a source change.

---

## Ways to contribute

| Kind | Start here |
|------|------------|
| Bug report | Open an issue using the details in [Reporting a bug](#reporting-a-bug) |
| Small fix | Open a pull request directly |
| Larger change | Open an issue first so the approach can be agreed before you write code |
| Documentation | Pull requests for `README.md` and this file are welcome |
| Skills | A new bundled skill belongs in `src/agent/src/skills/bundled/` |

For anything touching the permission model, credential handling or the tool
dispatch path, please open an issue first. Those areas are security-sensitive
and a change in them needs review before it is written, not after.

---

## Pull request workflow

1. **Fork** the repository and clone your fork.
2. **Branch** off `main` with a descriptive name:
   `fix/monaco-loader-path`, `feature/csv-preview`.
3. **Change one thing.** Keep the pull request focused. A refactor and a
   feature belong in two pull requests, not one.
4. **Format** with black before committing:
   ```powershell
   python -m black src
   ```
5. **Run the app** and confirm the behaviour you changed still works, plus
   anything adjacent to it.
6. **Commit** with a message that explains why the change is needed:
   ```
   fix: resolve npx.cmd shim when launching MCP servers on Windows

   CreateProcess does not consult PATHEXT, so a bare "npx" in an MCP
   server config failed even though npm installs npx.cmd. Resolve the
   shim before spawning.
   ```
7. **Push** your branch and open a pull request against `main`.

### What a reviewer will look for

- The change does what the description says, and nothing else.
- It handles the failure path, not just the happy path.
- Streamed and user-supplied content is size-bounded before any string work.
- No credentials, tokens or personal paths are committed.
- Line endings are preserved. Cortex works on files with mixed CRLF and LF,
  so check `git diff --stat` rather than `git diff` when a change looks huge.

---

## Reporting a bug

A report that can be reproduced is a report that can be fixed. Include:

1. **What you did**, step by step, with the prompt or the file that triggered it.
2. **What you expected** to happen.
3. **What actually happened.** Paste the exact text, not a paraphrase.
4. **Your version.** Run the app, or check `VERSION` in `src/version.py`.
5. **The log.** `~/.cortex/logs/cortex.log` is the first place to look. If the
   window froze, the log contains a `UI-STALL` entry with the duration and the
   Python stack that was blocking, which usually names the culprit directly.
6. **Your environment.** Windows build, Python version, `node --version`, and
   the model and provider you were using.

Redact API keys before posting a log.

If the problem only happens in the packaged `.exe` and not when running from
source, say so explicitly. That distinction has been the deciding clue more than
once.

---

## Code guidelines

### Python

- Follow the existing style. The project is formatted with black.
- Type hints on function signatures where the types are known.
- Prefer small, single-purpose functions over long ones.
- Do not add a dependency for something the standard library already does.

### GUI code

This is the area with the most traps.

- **Never do unbounded work on streamed content.** A single agent run can emit
  multi-megabyte payloads. A `re.findall`, `"".join()` or `.replace()` over one
  of them on the GUI thread freezes the window, sometimes for a minute. Cap the
  input first, then process.
- **Theme switching is expensive.** Reapplying the global stylesheet
  re-polishes every live widget, including four embedded Chromium panels. Do not
  call it while the app is running.
- **Keep the chat widget tree small.** Restore paths have caps for this reason.
  Do not bypass them.
- **Handler work belongs off the GUI thread** when it can block on I/O.

### Web assets

The panels are plain HTML, CSS and JavaScript. xterm.js and Mermaid are
vendored in the repository. Monaco is not: it is loaded from
`node_modules/monaco-editor` and pinned to the 0.52 line because Cortex needs
the AMD `min/vs` build. Do not bump that pin without migrating the loader.

---

## Licence and attribution

- Do not commit credentials, keys, tokens or personal machine paths.
- If you add a third-party dependency or vendored asset, check its licence is
  compatible with Apache-2.0 and note it in your pull request.
- Do not use the Cortex name or logo in a way that suggests an official
  release. The licence grants no trademark rights.
