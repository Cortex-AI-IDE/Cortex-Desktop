<div align="center">

# Running Cortex AI IDE on macOS

### An unofficial port guide

**Cortex 3.0.47** &nbsp;·&nbsp; Apache-2.0 &nbsp;·&nbsp; Windows 10/11 and Linux are the official platforms

</div>

---

## Dear Mac user

Cortex does not ship a macOS build, and there is no macOS release on the
roadmap. That is the honest position, stated first so nothing below reads as a
promise.

Here is what that means for you:

- The **official** platforms are **Windows** (including the Microsoft Store
  build) and **Linux**. Linux users get `.deb` and `.rpm` packages from
  [cortex-ide.app/download](https://cortex-ide.app/download/).
- **The build pipeline is not published.** The repository you are reading is
  the application source. The PyInstaller spec, the Windows installer script,
  the MSIX manifest and the code signing configuration for the official builds
  are in a separate, private folder. Nothing in this repository builds an
  installer out of the box, on any platform.
- **You can fix both of those yourself.** The code is Apache-2.0. You can port
  it, build it, ship it, sell it, and modify it however you like. The one thing
  the licence does not give you is the Cortex name and logo, so do not present
  your build as the official product.
- **The port is small.** This is not a rewrite. The codebase already branches on
  `sys.platform` in the places that matter, and there is already a macOS code
  path in the key store. What is missing is a POSIX terminal backend and a set
  of shell names. Budget **30 to 45 minutes** for a working development build,
  and plan a second session if you want a signed `.dmg`.

Use whatever agent you already work with (Cursor, Claude Code, Codex, Cortex
itself on a Windows or Linux machine pointed at this folder). This document is
written so an agent can execute it directly: every claim names a file, a line
and the exact change.

**The target:** `python src/main.py` opens a working Cortex window on your Mac,
with the agent, the editor, chat, the explorer and a terminal. Then, if you want
it, a double-clickable `.app` inside a `.dmg`.

---

## Contents

- [What already works on macOS](#what-already-works-on-macos)
- [What needs changing](#what-needs-changing)
- [The 30 to 45 minute build](#the-30-to-45-minute-build)
- [Terminal: replacing PowerShell with zsh or bash](#terminal-replacing-powershell-with-zsh-or-bash)
- [Monaco editor and Node.js](#monaco-editor-and-nodejs)
- [Grep and ripgrep](#grep-and-ripgrep)
- [API keys and the macOS Keychain](#api-keys-and-the-macos-keychain)
- [The agent's shell tools on macOS](#the-agents-shell-tools-on-macos)
- [Packaging a .dmg](#packaging-a-dmg)
- [Verification checklist](#verification-checklist)
- [Troubleshooting](#troubleshooting)
- [What to do with your build](#what-to-do-with-your-build)

---

## What already works on macOS

Before anything is changed, a surprising amount is already portable. Each row
below was read directly from this source tree. This is the good news, and it is
why the job is short.

| Area | Evidence | Status |
|------|----------|--------|
| **Master encryption secret** | `src/core/key_manager.py:228` branches to `_macos_keychain_secret()` (line 232), which uses the macOS `security` CLI to read and create a Keychain item | Works |
| **Linux secret store** | `src/core/key_manager.py:262` uses `secret-tool` | Works |
| **Server platform reporting** | `src/core/cortex_api.py:33` maps `darwin` to `macos` | Works |
| **MCP server resolving** | `src/services/mcp_manager.py:123` has an explicit `darwin` branch (the `npx.cmd` shim problem is Windows-only) | Works |
| **Recycle Bin / Trash** | `src/utils/safe_delete.py:117` tries `send2trash` first, which is cross-platform. The Windows-only `SHFileOperationW` path is guarded by `sys.platform != "win32"` and returns unavailable | Works |
| **Single instance guard** | `src/core/instance_ipc.py:41` selects `AF_UNIX` on non-Windows and puts the socket in `$XDG_RUNTIME_DIR` or `~/.cortex` | Works |
| **GPU compatibility probe** | `src/core/gpu_compat.py:106` returns early on non-Windows | Works |
| **Process spawning** | `src/ui/components/sidebar_bridge.py:1331` already uses the macOS and Linux fire-and-forget path | Works |
| **Startup sequence** | Every `sys.platform == 'win32'` block in `src/main.py` (lines 22, 40, 53, 244, 286, 297, 331, 458, 811) is an `if`, not an assumption. They no-op on macOS | Works |
| **Bash tool backend** | `src/agent/src/tools/BashTool/BashTool.py:139` resolves the shell with `shutil.which("bash")`, which finds `/bin/bash` on macOS. Line 160 already sets the Windows-only `CREATE_NO_WINDOW` flag to `0` elsewhere | Works |
| **Grep fallback chain** | `src/agent/src/tools/GrepTool/GrepTool.py:639` ends at line 720 with `return shutil.which('rg')` | Works once `rg` is installed |
| **Terminal UI** | `src/ui/components/xterm_terminal.py:991` falls back to a `QProcess` shell when pywinpty is absent | Partial, see below |
| **The four Chromium panels** | Monaco, xterm.js, Mermaid and the HTML panels are all vendored web assets. Qt WebEngine is identical on macOS | Works |

---

## What needs changing

Ranked by how much it hurts. Rows 1 to 3 are the real work. Rows 4 and below
are correctness and polish.

| # | Priority | File | Problem on macOS | What to do |
|---|----------|------|------------------|------------|
| 1 | **Blocker** | `src/core/terminal_session.py:390` | `if sys.platform != "win32" or not _WINPTY: return False`. The shared AI shell session can **never start** on macOS. This is what the agent's persistent shell is built on | Add a POSIX PTY backend |
| 2 | **Blocker** | `src/core/terminal_session.py:67` | `_INTEGRATION_PS1` is a PowerShell integration script. The command start/end/cwd markers the AI relies on are written by a PowerShell prompt | Write the zsh/bash equivalent |
| 3 | **High** | `src/ui/components/xterm_terminal.py:798` | `_SHELL_EXE` and `_SHELL_ARGS` only contain `powershell.exe`, `cmd.exe`, `bash.exe`, `wsl.exe`. Line 813 defaults to `"powershell"`, line 828 hardcodes `powershell.exe` | Map the names to `/bin/zsh` and `/bin/bash`, default to `zsh` |
| 4 | **High** | `src/core/key_manager.py:491` | `if os.name != 'nt': return None`. The **master secret** has a Keychain path, but per-provider **API keys** do not. Same at `store_key` (line 402, Windows branch at 424) and both delete sites | Add a Keychain branch, or accept the fallback |
| 5 | **Medium** | `src/ui/components/xterm_terminal.py:24` | `import winpty` fails, so `WINPTY_AVAILABLE` is `False` and the terminal drops to the `QProcess` fallback (line 991). The source comment at line 23 says it plainly: this "doesn't support interactive terminal apps like vim or python repl well" | Point it at a POSIX PTY |
| 6 | **Medium** | `src/ai/agent_bridge.py:1341` | The Recycle Bin rewrite builds a **PowerShell** command to move files to Trash | Use `send2trash` or `osascript` |
| 7 | **Medium** | `src/ai/agent_bridge.py:1407`, `2161` | The `PowerShell` tool description and the shell preamble tell the model it is on Windows | Gate the tool off on macOS, prefer `Bash` |
| 8 | **Medium** | `src/ai/agent_bridge.py:2566` | `PowerShell` sits in the always-exposed tool list | Hide it when `sys.platform != "win32"` |
| 9 | **Build** | `bin/rg.exe` | A 5.4 MB **Windows** binary cannot run on macOS | Do not ship it. Use `brew install ripgrep` or the npm package |
| 10 | **Build** | `src/utils/pyinstaller_hooks/hook-winpty.py` | Bundles `conpty.dll`, `winpty.dll`, `winpty-agent.exe`, `OpenConsole.exe` | Mac spec must not reference it |
| 11 | **Build** | `src/utils/runtime_hook_noconsole.py` | Hides the Windows console window via the Win32 API | Skip it in the mac spec |
| 12 | **Build** | `requirements-dev.txt` | `pywin32-ctypes==0.2.3` and `pefile==2024.8.26` are Windows-only. They install but do nothing | Leave them or strip them |
| 13 | **Packaging** | `src/assets/logo/logo.ico` | macOS wants `.icns` | Convert `app.png` with `iconutil` |
| 14 | **Nice to have** | `src/agent/src/utils/powershell/` and `src/agent/src/tools/PowerShellTool/` | Entire PowerShell tool subtree, Windows by design | Leave in place, gate at runtime |

Note on `requirements.txt`: `pywinpty>=2.0` is already correctly marked
`; sys_platform == "win32"`, so it will not install on your Mac. That is
deliberate and correct. It is also exactly why row 1 exists: the terminal
session code was written against pywinpty and has no POSIX twin.

---

## The 30 to 45 minute build

Everything here is a development build. It gets you a real, running Cortex.
Packaging comes later.

### Step 1: Install the toolchain (about 10 minutes)

You need Homebrew, Python 3.11 or newer, Node.js 18 or newer, and ripgrep.

```bash
# Homebrew, if you do not already have it
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# The rest
brew install python@3.12 node ripgrep git
```

Verify all four:

```bash
python3 --version    # expect 3.11 or newer
node --version       # expect v18 or newer
rg --version         # expect ripgrep 14.x
git --version
```

Two notes on Python. Cortex was built and tested on 3.14 on Windows, and 3.11
through 3.14 are all intended to work. If `python3.12` fails to build a
dependency later, `brew install python@3.13` is a good fallback. Also, on macOS
always call `python3`, never a bare `python`, unless you have made an alias.

### Step 2: Clone and install the JavaScript dependency (about 5 minutes)

```bash
git clone https://github.com/Cortex-AI-IDE/Cortex-Desktop.git
cd Cortex-Desktop
npm install
```

`npm install` pulls in exactly one package: `monaco-editor@0.52.2`. It is
mandatory. Skip it and the editor pane shows `Monaco loader.js missing`
instead of your code, because the app reads
`node_modules/monaco-editor/min/vs/loader.js` at startup.

### Step 3: Create the virtual environment (about 3 minutes)

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
```

Your prompt should now begin with `(.venv)`. Every command from here assumes it
is still active. If you open a new terminal tab, run
`source .venv/bin/activate` again.

Qt and PyQt6-WebEngine are the large downloads. This step is the slowest part
of the whole process.

If you would rather not install the development tooling, `requirements.txt`
alone is enough to run the app. Use `requirements-dev.txt` if you intend to
build a `.dmg` later, since PyInstaller is in it.

### Step 4: First run (about 2 minutes)

```bash
python src/main.py
```

**Right now, before any code change.** Expect this to open a window. The first
launch is slow, because Qt WebEngine unpacks and warms its profile. Give it
fifteen or twenty seconds.

What to check on that first launch:

| Panel | Expected now | Note |
|-------|--------------|------|
| Window and splitters | Opens | Native PyQt6, fully portable |
| Chat transcript | Renders | HTML in Qt WebEngine |
| Model provider | Configure it in Settings with your own API key | See the Keychain section |
| File explorer | Works | HTML panel |
| Editor | Works if you ran `npm install` | Monaco |
| Terminal | Opens, but degraded | Drops to the `QProcess` fallback, no real PTY |
| Agent shell commands | Run, but with no memory between calls | The session never starts, see row 1 |

If the window opens and the editor shows code, you have a working Cortex. Fix 1
and 2 below turn the terminal from "usable" into "correct".

### Step 5: Make the three essential changes

Work through [Terminal: replacing PowerShell](#terminal-replacing-powershell-with-zsh-or-bash)
next. That section has the exact edits. Then run
`python src/main.py` again and confirm the terminal accepts `ls -la`, `git status`
and `python3 -c "print(1)"`, with a real interactive prompt.

That is the 30 to 45 minute path. Steps 1 to 4 get a running app. Step 5 makes
the terminal correct.

---

## Terminal: replacing PowerShell with zsh or bash

This is the heart of the port. There are two separate terminal systems, and
both are Windows-shaped:

1. **`src/ui/components/xterm_terminal.py`** is the visible terminal panel the
   user sees and types into.
2. **`src/core/terminal_session.py`** is the shared "Cortex AI" session that the
   agent's shell commands run inside, so that `cd`, variables, an activated
   virtualenv and `$env:` changes survive between tool calls.

### Problem A: the shell names are hardcoded Windows executables

At `src/ui/components/xterm_terminal.py:798`:

```python
_SHELL_EXE = {
    "powershell": "powershell.exe",
    "cmd": "cmd.exe",
    "bash": "bash.exe",
    "wsl": "wsl.exe",
}
```

And at line 813 the default is `"powershell"`, with line 828 falling back to
`powershell.exe -NoLogo <ctrl-c fix>`.

**Change it to select by platform.** The smallest correct version:

```python
import sys

_IS_MAC = sys.platform == "darwin"

if _IS_MAC:
    _SHELL_EXE = {
        "zsh": "/bin/zsh",
        "bash": "/bin/bash",
        "sh": "/bin/sh",
    }
    _SHELL_ARGS = {
        "zsh": "-l",       # login shell, so ~/.zprofile and PATH load
        "bash": "--login",
        "sh": "",
    }
    _DEFAULT_SHELL = "zsh"
else:
    _SHELL_EXE = {
        "powershell": "powershell.exe",
        "cmd": "cmd.exe",
        "bash": "bash.exe",
        "wsl": "wsl.exe",
    }
    _SHELL_ARGS = {
        "powershell": "-NoLogo",
        "cmd": "",
        "bash": "--login -i",
        "wsl": "",
    }
    _DEFAULT_SHELL = "powershell"
```

Then, in the same method, change the two defaults (`line 813`) from
`default="powershell"` to `default=_DEFAULT_SHELL`, and change the hardcoded
return on line 828 so the PowerShell Ctrl+C fix is applied **only** on Windows.
The `_PS_ENABLE_CTRL_C` blob is a C# `kernel32.dll` `P/Invoke`; it is meaningless
on macOS and passing it to `/bin/zsh` would be treated as a filename.

**Do not try to shortcut this by editing `~/.cortex/settings.json`.** It looks
tempting: set `terminal.default_shell` to `"zsh"` and skip the code. It will not
work. The read is `settings.get("terminal", "default_shell", default="powershell")`
at line 813, but the very next line resolves it through the dictionary:

```python
cmd = _SHELL_EXE.get(shell, "powershell.exe")
```

`_SHELL_EXE` has no `"zsh"` key, so `.get` returns its fallback and you launch
`powershell.exe` anyway, with no error to explain it. Either add the key to the
dictionary or the setting is inert. This is the single most likely way to lose
twenty minutes on the port.

Also fix the fallback at line 1014:

```python
shell = parts[0] if parts else "powershell.exe"
```

That must become platform-aware too, or an empty shell string still yields
`powershell.exe` on your Mac.

There is a second default to catch. `src/ui/components/xterm_terminal.py:251`
reads the setting the same way:

```python
_shell_name = _s.get("terminal", "default_shell", default="powershell")
```

That one only labels the terminal tab, but it should agree with the others.
Better still, make the default live in one place, and have both call sites read
it.

### Problem B: the agent's shared session never starts

At `src/core/terminal_session.py:389`:

```python
def start(self) -> bool:
    if sys.platform != "win32" or not _WINPTY:
        return False
```

Windows gets pywinpty, which is a real ConPTY. macOS has no pywinpty, so the
method returns `False` immediately and the agent falls back to one-shot
subprocess calls. Concretely, the agent loses all state between commands: a `cd`
does not persist, an activated virtualenv does not persist, and an exported
environment variable disappears. Commands still run, so this is not a crash. It
is a correctness gap, and it is the single most valuable thing to fix.

**Two ways to fix it.** Pick one.

**Option 1, recommended: add `ptyprocess`.** It is a small, pure-Python, well
maintained library that gives you a real PTY on macOS, and its API is close
enough to pywinpty's that the rest of the file barely changes.

```bash
python -m pip install ptyprocess
```

Then install it conditionally in `requirements.txt` and add a backend selection:

```python
try:
    import winpty
    _PTY_BACKEND = "winpty"
except ImportError:
    _PTY_BACKEND = None

if sys.platform != "win32":
    try:
        from ptyprocess import PtyProcessUnicode
        _PTY_BACKEND = "ptyprocess"
    except ImportError:
        _PTY_BACKEND = None
```

Then replace the Windows-only guard at line 390 with a backend check, and give
the spawn at line 411 a POSIX twin. The Windows call is:

```python
self.pty = winpty.PtyProcess.spawn(cmd, cwd=self.cwd, env=env, dimensions=(rows, cols))
```

The `ptyprocess` equivalent is:

```python
self.pty = PtyProcessUnicode.spawn(
    [shell, "-l"], cwd=self.cwd, env=env, dimensions=(rows, cols)
)
```

**Option 2, no new dependency: use the standard library.** The `pty`, `termios`,
`fcntl`, `struct` and `os` modules ship with Python and can give you a working
PTY on macOS with `pty.fork()` or `os.openpty()`. It is more code than option 1
and you own the termios plumbing, but it keeps the dependency list exactly as
it is today. Choose this if you want a zero-dependency port.

Note that `ptyprocess` is not currently in `requirements.txt`, so option 1 adds
one conditional line. That is a reasonable trade.

### Problem C: the shell integration script is PowerShell

At `src/core/terminal_session.py:67`, `_INTEGRATION_PS1` is a PowerShell script
that Cortex writes to `~/.cortex/shell/cortex_integration.ps1` and sources at
startup. It is how the AI knows a command finished and what its exit code was,
using OSC 633 escape sequences that xterm.js ignores:

```
ESC ] 633 ; C BEL             command accepted, output starts
ESC ] 633 ; D ; <code> BEL    command finished
ESC ] 633 ; P ; Cwd=<dir> BEL current directory
```

**Write the zsh and bash equivalent.** In zsh the standard hook pair is
`preexec` and `precmd`; in bash it is `PROMPT_COMMAND` plus a `DEBUG` trap or
`PS0`. A minimal zsh version looks like this:

```zsh
# ~/.cortex/shell/cortex_integration.zsh
_cortex_preexec() {
  printf '\033]633;C\007'
}

_cortex_precmd() {
  local _code=$?
  printf '\033]633;D;%d\007' "$_code"
  printf '\033]633;P;Cwd=%s\007' "$PWD"
}

autoload -Uz add-zsh-hook
add-zsh-hook preexec _cortex_preexec
add-zsh-hook precmd _cortex_precmd
```

Load it the way the Windows path does, with a flag that keeps the shell alive:

```bash
/bin/zsh -l -c "source ~/.cortex/shell/cortex_integration.zsh; exec /bin/zsh -l"
```

The parser on the Python side already understands these sequences, because it
parses the byte stream rather than PowerShell's output. Check
`_SHELL_PROMPT_RE` at `src/core/terminal_session.py:121` while you are here: it
matches `[$#%>]\s*$`, and that pattern works for a `%` or `#` zsh prompt as well
as a `$` bash one, so it likely needs no change.

### Problem D: the visible terminal uses the QProcess fallback

`src/ui/components/xterm_terminal.py:840` branches on `WINPTY_AVAILABLE`. On
macOS that is `False`, so control goes to the `QProcess` path at line 991. A
`QProcess` is not a PTY. The source comment at line 23 is candid about the
consequence: it "doesn't support interactive terminal apps like vim or python
repl well". Simple commands work, full-screen programs do not.

Once you have a working PTY backend from Problem B, route the visible terminal
through it too, for the same reason and the same payoff. This is a good second
session task, not part of the 45 minutes.

---

## Monaco editor and Node.js

This part needs no porting. It is worth documenting because it is the first
thing that looks broken if you skip a step.

**Monaco is pure web content.** It is a JavaScript editor running inside Qt
WebEngine, so `darwin` versus `win32` is irrelevant to it. What matters is that
the files exist on disk.

Cortex injects Monaco's AMD loader into `src/assets/editor.html` at startup,
reading it from:

```
node_modules/monaco-editor/min/vs/loader.js
```

So the requirement is simply that you ran `npm install` in the directory that
contains `package.json`. On macOS that is `npm install` exactly as written.
There is no `.cmd` shim problem for this path; that problem is specific to
resolving `npx` for MCP servers, and `src/services/mcp_manager.py:123` already
handles the `darwin` case.

**The version is pinned on purpose.** `package.json` holds
`monaco-editor: "0.52.2"`. Cortex needs the **AMD** build under `min/vs`.
Newer major lines are expected to drop it, so keep 0.52 until the editor is
migrated to the ESM bundle. If you upgrade Monaco and the editor goes blank,
that is why.

**Node itself** is needed for three separate things:

1. Providing the Monaco files above.
2. Starting MCP servers, since almost every Model Context Protocol server is
   published to npm and launched with `npx`.
3. Running your own project's tooling (`npm test`, `npx tsc`, `npm run build`)
   in the integrated terminal, which is your project's business, not Cortex's.

Node does **not** build the app's own UI. xterm.js and Mermaid are vendored in
the repository, and the explorer, chat and memory manager are plain HTML, CSS
and JavaScript. Nothing bundles at startup.

If you see `Monaco loader.js missing`, you ran `npm install` in the wrong
directory or skipped it. It is the most common false alarm on a fresh Mac.

---

## Grep and ripgrep

`bin/rg.exe` is a Windows binary and will not run on macOS. Do not add it to
your mac build. The good news is that the search tool already has a correct
non-Windows path.

Look at `src/agent/src/tools/GrepTool/GrepTool.py:639`, the `_find_ripgrep()`
function. It tries, in order:

1. The npm `ripgrep` package, queried with
   `node -e "console.log(require('ripgrep').rgPath)"`. This returns the
   **platform-correct** binary, so on macOS it resolves a macOS `rg`. This is
   the preferred path and it already works.
2. The bundled binary in `bin/`, cached to `%LOCALAPPDATA%\Cortex\bin`. This is
   Windows-specific and will simply not find anything on your Mac.
3. `return shutil.which('rg')` at line 720. This is your path, once you have run
   `brew install ripgrep`.

So installing ripgrep is the whole fix:

```bash
brew install ripgrep
rg --version
```

Because you did that in step 1, `shutil.which('rg')` resolves it and Grep runs at
full speed. You do not need to touch `bin/rg.exe`, and you should not copy it
into a mac build.

If ripgrep is ever missing, the tool does not fail. It falls back to a pure
Python search and prefixes the result with a note saying so, which you can see
in the source at line 300. Slower, still correct. That is your safety net.

One macOS-specific gotcha worth knowing: by default `rg` and the Grep tool both
respect `.gitignore`. If you are searching a folder that is not a git repository
and getting nothing, that is usually why.

---

## API keys and the macOS Keychain

Cortex stores secrets in two tiers, and on macOS only the first tier is
currently ported. Understand this before you are surprised by it.

### Tier 1: the master secret. Already works.

`src/core/key_manager.py:228` sends macOS straight to
`_macos_keychain_secret()` at line 232, which shells out to the `security`
command:

```bash
security find-generic-password -s <target> -a cortex -w     # read
security add-generic-password -U -s <target> -a cortex -w <secret>   # create
```

That is the correct macOS mechanism and it requires no extra Python package,
because it uses `subprocess` and the system `security` binary. Nothing to do
here.

The same file gives Linux `secret-tool` at line 262. Windows uses the
Credential Manager through raw `ctypes` in `src/core/win_cred.py`.

### Tier 2: per-provider API keys. Windows-only today.

`src/core/key_manager.py:491`:

```python
def _get_os_keyring_key(self, provider: str) -> Optional[str]:
    if os.name != 'nt':
        return None
```

The same Windows-only shape appears in `store_key` (definition at line 402, the
Windows write at line 424) and in both delete paths. So on macOS:

- Your **API keys are still safe and still work.** They are written to the
  encrypted backup file, `keys.enc`, encrypted with AES-GCM under the master
  secret that *is* in your Keychain.
- What you lose is the **primary** store. The Keychain will not hold your
  provider keys, only the master secret that protects them.

**This is a real but modest gap. The app functions.** Your keys persist, they
are encrypted at rest, and the master secret protecting them lives in the
Keychain. If you want key parity with Windows, add a darwin branch that writes
each provider key with `security add-generic-password`, mirroring
`_macos_keychain_secret`. It is a small, self-contained change and a good
follow-up once the terminal works.

### Where things live

| Path | Contents |
|------|----------|
| `~/.cortex/` | Settings, memories, rules, logs, semantic index |
| `~/.cortex/memory/` | Project and user memories, as Markdown |
| `~/.cortex/logs/cortex.log` | The main diagnostic log. **Start here when something breaks** |
| macOS Keychain | The master encryption secret |

`~/.cortex/logs/cortex.log` is the first place to look for any problem. Cortex
also writes stall reports there: if the window freezes, the log records how long
it was frozen and the Python stack that was blocking, which usually names the
exact function. On macOS those live in your home directory rather than under
`%LOCALAPPDATA%`.

---

## The agent's shell tools on macOS

Cortex exposes two shell tools to the model. On macOS you want one of them.

| Tool | macOS verdict |
|------|---------------|
| `Bash` | **Correct choice.** `src/agent/src/tools/BashTool/BashTool.py:139` resolves `/bin/bash` with `shutil.which("bash")`, and line 160 already zeroes the Windows-only `CREATE_NO_WINDOW` flag elsewhere. It runs `bash -c`, which is right |
| `PowerShell` | **Should be off.** It runs Windows PowerShell. Keep it available only when `sys.platform == "win32"` |

Three specific places carry Windows assumptions into the agent:

**The tool list.** `src/ai/agent_bridge.py:2566` includes `'PowerShell'` among the
always-exposed tools. Gate that entry on `sys.platform == "win32"` so a macOS
model is not offered a shell it cannot use.

**The tool description.** `src/ai/agent_bridge.py:1407` describes the tool as
"Run a command in Windows PowerShell". That text is a prompt. It teaches the
model Windows habits. Either hide the tool or rewrite the description.

**The Recycle Bin rewrite.** `src/ai/agent_bridge.py:1341` builds a PowerShell
command to send files to the Recycle Bin instead of deleting them permanently.
On macOS, replace that with `send2trash`, which is already a dependency and
already used by `src/utils/safe_delete.py:117`, or with `osascript` to move the
item to Trash:

```bash
osascript -e 'tell application "Finder" to delete POSIX file "/path/to/file"'
```

Do not simply delete the rewrite. It exists to make destructive commands
recoverable, and that guarantee should hold on macOS too.

**The shell preamble.** `src/ai/agent_bridge.py:1997` tells the model
"On this PC your Bash commands run hidden, in a one-off PowerShell". That is a
Windows statement injected into the system prompt. It needs a macOS variant
describing zsh.

None of this stops the app from running, which is why it sits below the terminal
work in priority. But a model told it is on Windows will write PowerShell, so
fixing it materially improves agent behavior on your Mac.

---

## Packaging a .dmg

Do this only after `python src/main.py` is working. A build freezes whatever
bugs you have.

**Read this first: there is no spec in this repository.** The `cortex.spec`
referenced by the Windows build lives in a separate, private folder that is not
published here. What follows is a macOS spec written for you. Save it as
`cortex-macos.spec` in the project root.

### What differs from the Windows spec

| Concern | Windows | macOS |
|---------|---------|-------|
| Bundle layout | `EXE` plus `COLLECT` into a folder | `BUNDLE` into a `.app` |
| Icon | `logo.ico` | `AppIcon.icns` |
| Console | `console=False` hides a Win32 window | Not applicable, skip the noconsole hook |
| Terminal binaries | pywinpty `.dll`/`.pyd`/`.exe` collected | None, use the POSIX PTY |
| Grep binary | `bin/rg.exe` | Nothing, rely on `shutil.which('rg')` |
| Runtime hooks | Includes `runtime_hook_noconsole.py` | Omit it |
| Signing | Code signing certificate | `codesign` and `notarytool`, or unsigned |

### The .app bundle

Build the icon first. macOS wants `.icns`, and `src/assets/logo/app.png` is
already in the tree:

```bash
mkdir -p icon.iconset
sips -z 16 16     src/assets/logo/app.png --out icon.iconset/icon_16x16.png
sips -z 32 32     src/assets/logo/app.png --out icon.iconset/icon_16x16@2x.png
sips -z 32 32     src/assets/logo/app.png --out icon.iconset/icon_32x32.png
sips -z 64 64     src/assets/logo/app.png --out icon.iconset/icon_32x32@2x.png
sips -z 128 128   src/assets/logo/app.png --out icon.iconset/icon_128x128.png
sips -z 256 256   src/assets/logo/app.png --out icon.iconset/icon_128x128@2x.png
sips -z 256 256   src/assets/logo/app.png --out icon.iconset/icon_256x256.png
sips -z 512 512   src/assets/logo/app.png --out icon.iconset/icon_256x256@2x.png
sips -z 512 512   src/assets/logo/app.png --out icon.iconset/icon_512x512.png
sips -z 1024 1024 src/assets/logo/app.png --out icon.iconset/icon_512x512@2x.png
iconutil -c icns icon.iconset
```

That produces `icon.icns`.

### A working macOS spec

This mirrors the Windows spec's structure and its important lessons: the
`collect_data_files` calls for certifi and spellchecker exist because without
them HTTPS requests and the spellchecker silently died in the frozen build, and
the `hiddenimports` list covers modules PyInstaller cannot see through lazy
`importlib` calls. Keep all of that.

```python
# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None
ROOT = os.path.abspath('.')

datas = [
    (os.path.join(ROOT, 'src', 'ui', 'html'), os.path.join('src', 'ui', 'html')),
    (os.path.join(ROOT, 'src', 'ui', 'components'), os.path.join('src', 'ui', 'components')),
    (os.path.join(ROOT, 'src', 'ui', 'themes'), os.path.join('src', 'ui', 'themes')),
    (os.path.join(ROOT, 'src', 'assets'), os.path.join('src', 'assets')),
    (os.path.join(ROOT, 'plugins'), 'plugins'),
]

# Neither of these is optional. Without certifi's cacert.pem every HTTPS
# request dies before it leaves the machine, and without the spellchecker
# dictionary data the chat spellcheck turns itself off. Both worked in dev
# and broke only in the frozen build.
datas += collect_data_files('spellchecker')
datas += collect_data_files('certifi')

# The bundled agent skills are data files. collect_submodules only walks
# .py files, so without this the frozen app ships zero builtin skills.
datas += [('src/agent/src/skills/bundled', 'src/agent/src/skills/bundled')]

# Monaco, read at runtime from node_modules. Run `npm install` first.
for nm_sub in ('monaco-editor',):
    nm_path = os.path.join(ROOT, 'node_modules', nm_sub)
    if os.path.isdir(nm_path):
        datas.append((nm_path, os.path.join('node_modules', nm_sub)))

hiddenimports = [
    'src.version',
    'src._build_info',
    'src.commands',
    'PyQt6',
    'PyQt6.QtWidgets',
    'PyQt6.QtCore',
    'PyQt6.QtGui',
    'PyQt6.QtWebEngineWidgets',
    'PyQt6.QtWebEngineCore',
    'PyQt6.QtWebChannel',
    'PyQt6.QtNetwork',
    'PyQt6.sip',
    'PyQt6.QtSvg',
    'PyQt6.QtMultimedia',
    # stdlib
    'asyncio',
    'multiprocessing',
    'sqlite3',
    'json',
    'logging',
    'ctypes',
    'xml',
    'xml.etree',
    'xml.etree.ElementTree',
    'html.parser',
    'http.server',
    'socketserver',
    'ssl',
    'pty',            # macOS PTY backend
    'termios',
    'fcntl',
    # crypto and TLS
    'certifi',
    'cryptography',
    'cffi',
    '_cffi_backend',
    'pycparser',
    # http
    'dotenv',
    'requests',
    'httpx',
    'aiohttp',
    'urllib3',
    # providers
    'openai',
    'anthropic',
    'src',
    'src.ai',
    'src.ai.providers',
    # agent runtime
    'src.agent',
    'src.agent.src',
    'src.core',
    'src.services.update_checker',
    'src.ui.dialogs',
    'src.ui.dialogs.update_dialog',
    'src.ui.dialogs.memory_manager',
    'src.ui.dialogs.health_map',
    'src.ui.dialogs.diff_viewer',
    'src.ui.render_health',
    'src.ui.native_menu',
    'src.core.voice_input',
    'src.core.voice_stream',
    'websocket',
    # data and config
    'yaml',
    'pydantic',
    'numpy',
    'PIL',
    'typing_extensions',
    'psutil',
    'pygments',
    'mistune',
    'bs4',
    'lxml',
    'orjson',
    'mcp',
    # file formats
    'PyPDF2',
    'docx',
    'openpyxl',
    'xlrd',
    'pymupdf',
    'fitz',
    'spellchecker',
    'flatlatex',
    'send2trash',
]

hiddenimports += collect_submodules('asyncio')
hiddenimports += collect_submodules('multiprocessing')
hiddenimports += collect_submodules('pydantic')
hiddenimports += collect_submodules('cryptography')
hiddenimports += collect_submodules('openai')
hiddenimports += collect_submodules('mcp')
hiddenimports += collect_submodules('anyio')
hiddenimports += ['pydantic_settings', 'httpx_sse', 'jsonschema']

# Every Cortex src.* submodule, so every lazy importlib.import_module call
# resolves in the frozen app exactly as it does in development.
hiddenimports += collect_submodules('src.ai')
hiddenimports += collect_submodules('src.core')
hiddenimports += collect_submodules('src.ui')
hiddenimports += collect_submodules('src.utils')
hiddenimports += collect_submodules('src.config')
hiddenimports += collect_submodules('src.coordinator')
hiddenimports += collect_submodules('src.plugin')
hiddenimports += collect_submodules('src.services')
hiddenimports += collect_submodules('src.agent')

a = Analysis(
    [os.path.join(ROOT, 'src', 'main.py')],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[os.path.join(ROOT, 'src', 'utils', 'pyinstaller_hooks')],
    hooksconfig={},
    runtime_hooks=[
        # NOTE: runtime_hook_noconsole.py is deliberately absent. It hides a
        # Windows console via the Win32 API and is meaningless on macOS.
        os.path.join(ROOT, 'src', 'utils', 'runtime_hook_encodings.py'),
        os.path.join(ROOT, 'src', 'utils', 'runtime_hook_certifi.py'),
        os.path.join(ROOT, 'src', 'utils', 'runtime_hook_asyncio.py'),
        os.path.join(ROOT, 'src', 'utils', 'runtime_hook_agent_path.py'),
    ],
    excludes=[
        'tkinter', 'matplotlib', 'scipy', 'test', 'tests', 'unittest',
        'xmlrpc', 'pydoc', 'doctest',
        'pandas', 'litellm', 'tokenizers', 'speech_recognition',
        'sqlalchemy', 'boto3', 'botocore', 'grpc', 'mem0',
        'qdrant_client', 'redis', 'mistralai', 'git', 'tiktoken',
        # Windows-only, must not be pulled in transitively on macOS
        'winpty', 'pywinpty', 'win32ctypes', 'pefile',
    ],
    cipher=block_cipher,
    noarchive=False,
)

# Qt WebEngine ships a debug twin of every resource pack, about 81 MB.
a.datas = [d for d in a.datas if '.debug.' not in os.path.basename(d[0])]
a.binaries = [b for b in a.binaries if '.debug.' not in os.path.basename(b[0])]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Cortex',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,            # UPX breaks macOS signing
    console=False,
    argv_emulation=False,
    target_arch=None,     # set to 'arm64' or 'x86_64' to force one
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name='Cortex',
)

app = BUNDLE(
    coll,
    name='Cortex.app',
    icon=os.path.join(ROOT, 'icon.icns'),
    bundle_identifier='app.cortex.ide.unofficial',
    info_plist={
        'CFBundleName': 'Cortex',
        'CFBundleDisplayName': 'Cortex AI IDE',
        'CFBundleShortVersionString': '3.0.47',
        'NSHighResolutionCapable': True,
        # Qt WebEngine needs this on macOS. Without it the app may be
        # killed for using an unsigned executable memory region.
        'NSRequiresAquaSystemAppearance': False,
    },
)
```

Two things to notice. The `excludes` list now blocks Windows-only packages so a
transitive import cannot drag them into your mac bundle. And `upx=False` is not
optional: UPX compression breaks code signing, and macOS refuses to launch a
binary whose signature does not match.

Also correct the version in `CFBundleShortVersionString` to match
`src/version.py` (currently `3.0.47`) when you build.

### Build it

```bash
source .venv/bin/activate
python -m pip install pyinstaller

# The build script normally generates this. Make it yourself, or the app
# reports channel "dev". For a personal build, "dev" is fine, so this is
# optional.
cat > src/_build_info.py <<'EOF'
CHANNEL = "dev"
EOF

python -m PyInstaller cortex-macos.spec --noconfirm
```

You get `dist/Cortex.app`. Test it before packaging:

```bash
open dist/Cortex.app
```

**Watch for the classic frozen-build failure.** If the app launches but chat
errors on every provider, that is almost always a missing or misplaced
`cacert.pem`, which is exactly why the spec calls
`collect_data_files('certifi')`. If the editor pane says
`Monaco loader.js missing`, the Monaco data files did not make it into the
bundle. Both are build-configuration problems, not code problems.

### Make the .dmg

```bash
mkdir -p dmg-staging
cp -R dist/Cortex.app dmg-staging/
ln -s /Applications dmg-staging/Applications

hdiutil create -volname "Cortex" \
  -srcfolder dmg-staging \
  -ov -format UDZO \
  Cortex-3.0.47-macos.dmg
```

### About Gatekeeper

An unsigned `.dmg` will be blocked by Gatekeeper on other people's Macs, and
often on yours. You have three options:

1. **Ad-hoc sign it**, enough for local use:
   `codesign --deep --force --sign - dist/Cortex.app`
2. **Tell the user to right-click and choose Open**, which bypasses the block
   once per app.
3. **Sign and notarize properly.** This needs a paid Apple Developer account,
   `codesign` with a Developer ID certificate, and `xcrun notarytool submit`
   plus `xcrun stapler staple`. This is the only option that gives a clean
   double-click for everyone, and it is the reason official builds take longer.

Also note: if you build on an Apple Silicon Mac you get an `arm64` binary. If
you need one build that runs on both Intel and Apple Silicon, build twice and
combine them with `lipo`, or set `target_arch` and build two separate `.dmg`
files. Building for the other architecture from scratch is simpler and more
reliable.

---

## Verification checklist

Work down this list. Each item is observable, and each one maps to a claim
earlier in this document.

| # | Check | Expected |
|---|-------|----------|
| 1 | `python3 --version` | 3.11 or newer |
| 2 | `node --version` | v18 or newer |
| 3 | `rg --version` | ripgrep 14.x or newer |
| 4 | `npm install` completed | `node_modules/monaco-editor/` exists |
| 5 | `python src/main.py` | Window opens within about 20 seconds |
| 6 | Editor pane | Shows your code, not `Monaco loader.js missing` |
| 7 | Settings, add an API key | Saves without error |
| 8 | Restart, send a chat message | The model replies |
| 9 | File explorer | Opens a project folder, lists files |
| 10 | Terminal, `ls -la` | Real output, real prompt |
| 11 | Terminal, `git status` | Works inside a repo |
| 12 | Terminal, `python3 -c "print(1)"` | Prints `1` |
| 13 | Terminal, `vim` then `:q` | **After the PTY fix only.** Full-screen apps work |
| 14 | Ask the agent to run `pwd` twice, with a `cd` between | Second call remembers the new directory, **after the session fix only** |
| 15 | Ask the agent to search for a string | Grep results, no "ripgrep was unavailable" note |
| 16 | `~/.cortex/logs/cortex.log` | No repeated errors, in particular nothing about winpty or PowerShell |
| 17 | Frozen build: `open dist/Cortex.app` | Same behavior as item 5 onwards |
| 18 | Frozen build: chat works | Proves `certifi` data files are bundled |

Items 13 and 14 are the two that distinguish a ported terminal from a working
but degraded one. Everything else should pass before you touch a line of code.

---

## Troubleshooting

**`python` is not found, only `python3`.**
Normal on macOS. Use `python3` everywhere, or add an alias. Inside an activated
virtual environment `python` also works, because the venv provides it.

**`xcrun: error: invalid active developer path` when running git.**
You need the Xcode command line tools: `xcode-select --install`.

**The editor pane says `Monaco loader.js missing`.**
You skipped `npm install` or ran it in the wrong directory. Run it where
`package.json` lives, then restart Cortex.

**The terminal opens but `vim` or a Python REPL renders garbage.**
Expected before the PTY fix. The visible terminal is on the `QProcess` fallback,
which is not a real PTY. See
[Terminal: replacing PowerShell](#terminal-replacing-powershell-with-zsh-or-bash).

**The agent forgets a `cd` or an activated virtualenv between commands.**
Expected before the session fix. `src/core/terminal_session.py:390` returns
`False` on macOS, so commands run one-shot with no shared state. This is the
most valuable single fix in the port.

**The app is killed on launch, or Gatekeeper says it is damaged.**
Signing. Try `codesign --deep --force --sign - dist/Cortex.app`, or right-click
the app and choose Open. For a distributable build you need a Developer ID and
notarization.

**Chat errors on every provider in the frozen build, but works from source.**
Almost always the missing TLS bundle. Confirm the spec calls
`collect_data_files('certifi')` and that the resulting bundle contains
`certifi/cacert.pem`.

**The window opens blank or shows grey boxes.**
Qt WebEngine failed to start its render process, usually after an unclean exit.
Close Cortex fully, delete the Qt WebEngine profile under your home directory,
and relaunch. On macOS the profile is not under `%LOCALAPPDATA%`, which is a
Windows-only path.

**A provider returns 401.**
Check the key and the endpoint. The Alibaba Token Plan is the usual trap:
`sk-sp-` keys are a separate billing channel needing the Token Plan endpoint,
and the standard endpoint rejects them. This is not macOS-specific, but it is
the most common "it is broken" report, so it belongs here.

**The window stops responding during a long agent run.**
`~/.cortex/logs/cortex.log` will contain a `UI-STALL` entry naming the blocking
Python function. Attach it to an issue, or fix it in your fork.

**Grep is slow.**
`rg` is not on your `PATH`. Run `brew install ripgrep`. Without it Cortex falls
back to a pure-Python search and says so in the result.

---

## What to do with your build

You have a working Cortex on macOS. Here is the honest summary of what that is
and what the licence lets you do with it.

### What you have

A fully functional ported IDE. The agent loop, the model providers, the editor,
chat, project memory, skills, MCP servers, the file explorer and the terminal
all work. The code is the same code that runs on Windows, with a POSIX terminal
backend and shell names where yours needed them.

### Known remaining gaps, stated plainly

| Gap | Impact | Effort to close |
|-----|--------|-----------------|
| Per-provider API keys are not in the Keychain, only the encrypted file | None for security, in that both are encrypted under a Keychain-held master secret. Only the storage location differs | Small |
| The visible terminal uses `QProcess` if you only fixed the AI session | Full-screen terminal apps misbehave | Medium |
| No signed or notarized build | Gatekeeper warns on other people's Macs | Needs a paid Apple Developer account |
| The PowerShell tool stays in the tool list unless you gate it | The model may offer PowerShell on macOS | Small |
| No macOS `.dmg` on the official download page | Expected. There is no official macOS release | Not yours to fix |

### Licence and naming

Cortex is **Apache-2.0**. You may use, modify, redistribute and sell software
built on this code, including commercially, provided you keep the licence and
copyright notices and state what you changed. There is an explicit patent grant.

**The Cortex name and logo are trademarks, and the licence does not grant
rights to them.** A modified build must not be presented as the official Cortex
product. If you distribute your port, give it your own name, or make it
unmistakably clear that it is an unofficial community build.

### Contributing back

If you fix the POSIX PTY backend or the shell integration script properly,
upstreaming it would be genuinely useful, because both are cleanly separable
improvements that do not alter Windows behavior. Open a pull request at
[Cortex-AI-IDE/Cortex-Desktop](https://github.com/Cortex-AI-IDE/Cortex-Desktop).
See [CONTRIBUTING.md](CONTRIBUTING.md) for the workflow.

### Where to get the official builds

If you also use Windows or Linux, the official and supported releases are:

- **Windows**, including the Microsoft Store build: [cortex-ide.app](https://cortex-ide.app/)
- **Linux**, `.deb` and `.rpm`: [cortex-ide.app/download](https://cortex-ide.app/download/)

Those builds are tested, signed and updated. Yours is not, and that is the
trade you made for having Cortex on a Mac today.

---

<div align="center">

**Cortex AI IDE** &nbsp;·&nbsp; Think Limitless. Build Beyond.

*Unofficial macOS port guide. Not supported, not endorsed, and yours to improve.*

</div>
