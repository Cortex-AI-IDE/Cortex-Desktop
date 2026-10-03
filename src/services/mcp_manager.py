"""
mcp_manager.py, Model Context Protocol client manager
=======================================================

Connects Cortex to external MCP servers (databases, APIs, SaaS tools…) and
exposes their tools to the agent as regular tool definitions.

Design:
  - Config uses the INDUSTRY-STANDARD mcp.json format, so any server
    README's config snippet works as-is:
        {"mcpServers": {"postgres": {"command": "npx", "args": [...]}}}
    Global config:  ~/.cortex/mcp.json
    Project config: <project>/.cortex/mcp.json   (overrides same names)
  - The official `mcp` SDK is asyncio-based; Cortex's agent loop is not.
    A dedicated daemon thread runs a private asyncio loop; every public
    method here is SYNCHRONOUS and thread-safe.
  - Each connected server's tools are namespaced `mcp__<server>__<tool>`
    and returned as OpenAI-style schemas, the agent treats them exactly
    like built-in tools (including the permission/autonomy gate).
  - Failures NEVER crash the app: a broken server shows status="error"
    with the message in Settings → MCP Servers.
  - A project's config file is UNTRUSTED INPUT, it arrives inside a git
    clone. A server declared there does not launch until the user approves
    it, and approvals are stored in ~/.cortex/mcp_trust.json, never in the
    project itself, so a repository cannot approve its own commands.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.utils.logger import get_logger

log = get_logger("mcp_manager")

_NAME_RE = re.compile(r"[^A-Za-z0-9_-]")

GLOBAL_CONFIG = Path.home() / ".cortex" / "mcp.json"

_SUBSCRIPTION_MSG = ("MCP servers require an active Cortex subscription "
                     "($5/month or $40/year) and a signed-in account, "
                     "see https://cortex-ide.app/pricing/")

_APPROVAL_MSG = ("This server is declared inside the project folder, which is "
                 "untrusted input. Review its command in Settings > MCP "
                 "Servers and approve it before it runs.")

TRUST_FILE_NAME = "mcp_trust.json"


def _cortex_home() -> Path:
    """~/.cortex, or $CORTEX_HOME when a harness redirects it.

    Resolved per call rather than at import so a test can point the trust
    store at a temporary directory without reloading this module.
    """
    env = os.environ.get("CORTEX_HOME")
    return Path(env) if env else Path.home() / ".cortex"


def _has_active_subscription() -> bool:
    """MCP is a SUBSCRIPTION feature: signed in + active plan required.
    Fails CLOSED, if the check can't run, MCP stays locked."""
    try:
        from src.core.cortex_api import get_api_client
        return bool(get_api_client().has_subscription())
    except Exception:
        return False


def _sanitize(name: str) -> str:
    return _NAME_RE.sub("_", name)[:48] or "server"


_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")


def _expand_env_str(v: str) -> str:
    """Expand ${VAR} / $VAR from the real environment.

    The mcp.json format allows this, and every server
    README that needs a token writes `"GITHUB_TOKEN": "${GITHUB_TOKEN}"`.
    Without expansion Cortex passed those eight literal characters to the
    server, which then failed to authenticate with no useful error. An
    undefined variable is left as-is so the user can see what was missing.
    """
    def _sub(m):
        name = m.group(1) or m.group(2)
        return os.environ.get(name, m.group(0))
    return _VAR_RE.sub(_sub, v)


def _expand_env(items):
    return [_expand_env_str(str(a)) for a in items]


def _foreign_global_configs():
    """Global MCP config files written by OTHER tools, in load order.

    Users who already set up MCP in another editor should not have to
    re-enter every server by hand; Cortex reads those files too. They are
    READ-ONLY: Cortex never writes to another tool's config.
    """
    home = Path.home()
    paths = [
        (home / ".cursor" / "mcp.json", "cursor"),
        (home / ".claude.json", "claude"),
    ]
    # The desktop MCP client stores its config in a per-OS location.
    # Hardcoding the Linux path here would make the whole import silently
    # do nothing on Windows and macOS.
    import sys as _sys
    if _sys.platform == "darwin":
        paths.append((home / "Library" / "Application Support" / "Claude"
                      / "claude_desktop_config.json", "claude"))
    elif _sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA")
        if appdata:
            paths.append((Path(appdata) / "Claude"
                          / "claude_desktop_config.json", "claude"))
    else:
        paths.append((home / ".config" / "Claude"
                      / "claude_desktop_config.json", "claude"))
    return paths


def _shadow_scope(original_scope: str, project_root: Optional[str]) -> str:
    """Scope to write a cortex-owned shadow of an inherited server at.

    A shadow only works if it is read AFTER the entry it hides.
    load_configs() reads foreign global, cortex global, foreign project,
    then cortex project, so a project-scoped foreign server can only be
    shadowed from project scope. Writing it globally, as this used to,
    left it overwritten and still running.

    Falls back to global when no project is open, since a project-scoped
    write would have nowhere to go.
    """
    if original_scope == "project" and project_root:
        return "project"
    return "global"


def _resolve_command(command: str) -> str:
    """Absolute path to `command`, so Windows can actually launch it.

    Every MCP server README says `"command": "npx"`, and on Windows that is
    not an executable: npm installs `npx.cmd`. CreateProcess does no PATHEXT
    lookup, so asyncio's create_subprocess_exec raises

        FileNotFoundError: [WinError 2] The system cannot find the file specified

    which anyio wraps as "unhandled errors in a TaskGroup (1 sub-exception)",
    the message the user sees with the real cause discarded. npx, uvx, bunx
    and pnpm dlx are all shims, so this broke essentially every documented
    MCP server on Windows while working on Linux and macOS.

    shutil.which() does apply PATHEXT and returns the full path with the real
    extension. Resolving here rather than at config time keeps the user's
    file readable and portable: the config still says `npx`.

    Left alone if it is already a path, or if nothing is found, so the
    original error still surfaces rather than being masked.
    """
    if not command or os.sep in command or (os.altsep and os.altsep in command):
        return command
    return shutil.which(command) or command


def _foreign_project_configs(root: str):
    """Per-project MCP config files written by other tools."""
    r = Path(root)
    return [
        (r / ".cursor" / "mcp.json", "cursor"),
        (r / ".mcp.json", "claude"),
    ]


# Variables a server needs to put a window on the user's desktop. The MCP
# SDK's get_default_environment() passes only HOME, LOGNAME, PATH, SHELL,
# TERM and USER on POSIX - a deliberate "leak nothing" default - so every
# server Cortex launched had no display at all. chrome-devtools-mcp starts a
# headed Chrome and died on the spot; Playwright only worked because it runs
# headless. These carry no secrets: they name the display and the session
# bus, nothing more. Windows has no equivalent and is left untouched.
_GUI_ENV_VARS = (
    "DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS", "XDG_SESSION_TYPE",
)


def _server_environment(cfg_env: Dict[str, str]) -> Dict[str, str]:
    """Environment for one MCP server: SDK defaults + display + its config.

    The server's own `env` from its config is applied LAST, so a config that
    sets DISPLAY (or anything else) still wins.
    """
    from mcp.client.stdio import get_default_environment
    env = dict(get_default_environment())
    if os.name != "nt":
        for key in _GUI_ENV_VARS:
            val = os.environ.get(key)
            if val:
                env.setdefault(key, val)
    env.update(cfg_env or {})
    return env


def _server_cwd(project_root: Optional[str]) -> str:
    """Folder an MCP server starts in.

    Without one it inherited Cortex's own working directory - for the
    installed app that is /opt/cortex-ide, owned by root - so any tool that
    writes a relative path (a screenshot "x.png") failed with permission
    denied. The open project is where the user expects such files and the
    folder the agent can read; home is the fallback when nothing is open.
    """
    if project_root and os.path.isdir(project_root):
        return project_root
    return str(Path.home())


def _shots_dir(project_root: Optional[str]) -> Optional[str]:
    """<project>/.cortex/shots, created on demand; None without a project."""
    if not project_root or not os.path.isdir(project_root):
        return None
    d = os.path.join(project_root, ".cortex", "shots")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        return None
    return d


def _launch_args(cfg: "McpServerConfig", project_root: Optional[str]) -> List[str]:
    """Arguments to start a server with: its config, plus Playwright's output
    folder. Without --output-dir, @playwright/mcp writes every file it names
    itself into <cwd>/.playwright-mcp - the project root, now that servers
    start in the project. A --output-dir in the user's own config wins; the
    config file itself is never changed.
    """
    args = list(cfg.args)
    is_playwright = any("@playwright/mcp" in a for a in args) or \
        os.path.basename(cfg.command or "").startswith("playwright-mcp")
    if is_playwright and not any(a.startswith("--output-dir") for a in args):
        shots = _shots_dir(project_root)
        if shots:
            args += ["--output-dir", shots]
    return args


# Tools that write a file the agent made to check its work, and the argument
# names they take it under (playwright: filename, chrome-devtools: filePath).
_SCRATCH_TOOL_RE = re.compile(r"screenshot|snapshot|pdf", re.I)
_FILE_ARG_NAMES = ("filename", "filePath", "file_path", "path", "outputPath")


def _scratch_file_args(tool: str, args: Dict[str, Any],
                       project_root: Optional[str]) -> Dict[str, Any]:
    """Put a RELATIVE screenshot/snapshot/pdf file name under .cortex/shots.

    Servers resolve a relative name against their working folder, which is
    the project root, so "verify_home.png" landed next to the user's files.
    An absolute path is a deliberate choice and is left alone.
    """
    if not _SCRATCH_TOOL_RE.search(tool or ""):
        return args
    changed = None
    for key in _FILE_ARG_NAMES:
        val = args.get(key)
        if not isinstance(val, str) or not val.strip():
            continue
        if os.path.isabs(val) or val.startswith("~"):
            continue
        shots = _shots_dir(project_root)
        if not shots:
            return args
        target = os.path.join(shots, val)
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
        except OSError:
            continue
        changed = dict(args) if changed is None else changed
        changed[key] = target
    return changed if changed is not None else args


def _npx_package(cfg: Optional["McpServerConfig"]) -> Optional[str]:
    """The npm package an npx-launched server runs, without its version:
    npx -y @upstash/context7-mcp@latest -> @upstash/context7-mcp."""
    if cfg is None or cfg.url:
        return None
    if os.path.basename(cfg.command or "").lower() not in ("npx", "npx.cmd", "pnpx", "bunx"):
        return None
    spec = next((a for a in cfg.args if not a.startswith("-")), "")
    if not spec:
        return None
    at = spec.rfind("@")
    return spec[:at] if at > 0 else spec


def _npm_cache_root() -> Path:
    env = os.environ.get("npm_config_cache") or os.environ.get("NPM_CONFIG_CACHE")
    if env:
        return Path(env)
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "npm-cache"
    return Path.home() / ".npm"


def _npx_cache_dirs(package: Optional[str]) -> List[Path]:
    """npx's download folders for `package` (one per version it fetched)."""
    if not package:
        return []
    root = _npm_cache_root() / "_npx"
    out = []
    try:
        for d in root.iterdir():
            try:
                deps = json.loads((d / "package.json").read_text(encoding="utf-8")).get("dependencies") or {}
            except Exception:
                continue
            if package in deps:
                out.append(d)
    except OSError:
        pass
    return out


def _dir_size(d: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(d):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total


def _backup_file(path: Path) -> Path:
    """Copy another tool's config aside before Cortex edits it."""
    bdir = Path.home() / ".cortex" / "backups"
    bdir.mkdir(parents=True, exist_ok=True)
    dest = bdir / f"{path.name.lstrip('.')}.{time.strftime('%Y%m%d-%H%M%S')}.bak"
    shutil.copy2(path, dest)
    return dest


def _remove_keys(path: Path, keys: List[str]) -> None:
    """Delete mcpServers entries from a JSON config, keeping everything else
    (and the file's indent and permissions)."""
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    for k in keys:
        (data.get("mcpServers") or {}).pop(k, None)
    m = re.search(r"\n( +)\S", raw)
    indent = len(m.group(1)) if m else 2
    tmp = str(path) + ".tmp"
    Path(tmp).write_text(json.dumps(data, indent=indent, ensure_ascii=False)
                         + ("\n" if raw.endswith("\n") else ""), encoding="utf-8")
    try:
        shutil.copymode(path, tmp)
    except OSError:
        pass
    os.replace(tmp, path)


# A tool answer that is really a credentials problem.
_AUTH_PROBLEM_RE = re.compile(
    r"invalid (api )?key|api key (is )?(invalid|missing|required|expired)|"
    r"unauthori[sz]ed|authentication (failed|required)|invalid (access )?token|"
    r"token (is )?(invalid|expired)|missing (api )?key|forbidden", re.I)


def _error_text(e: BaseException) -> str:
    """The real reason behind an MCP connection error.

    The SDK runs on anyio task groups, so a failure arrives wrapped as
    "unhandled errors in a TaskGroup (1 sub-exception)" - the text the user
    saw - with the cause (a 401 from a remote server, a missing program)
    inside. Unwraps to the first leaf exception.
    """
    seen = 0
    while isinstance(e, BaseExceptionGroup) and e.exceptions and seen < 10:
        e = e.exceptions[0]
        seen += 1
    status = getattr(getattr(e, "response", None), "status_code", None)
    if status in (401, 403):
        return (f"the server requires authentication ({status}). Remote servers "
                f"that need a browser sign-in (OAuth) are not supported yet; one "
                f"that takes an API key works with \"headers\" in its config.")
    # httpx appends a "For more information check: <mdn link>" line.
    text = (str(e) or type(e).__name__).strip().splitlines()[0]
    if type(e).__name__ not in text and not isinstance(e, (OSError, RuntimeError)):
        text = f"{type(e).__name__}: {text}"
    return text


def _is_auth_error(e: BaseException) -> bool:
    """401/403: retrying cannot fix it, the config has to change."""
    seen = 0
    while isinstance(e, BaseExceptionGroup) and e.exceptions and seen < 10:
        e = e.exceptions[0]
        seen += 1
    return getattr(getattr(e, "response", None), "status_code", None) in (401, 403)


def _unknown_arguments(schema: Optional[Dict[str, Any]],
                       args: Dict[str, Any]) -> List[str]:
    """Argument names the tool's schema does not declare.

    Servers built on zod or pydantic DROP undeclared keys silently. That is
    how a screenshot "of an element" kept coming back as the whole viewport:
    the model passed `selector`, Playwright's parameter is `target`, the key
    vanished and the call did something else without a word. Refusing the
    call with the valid names turns a silent wrong result into a one-step fix.

    Returns [] when the schema allows extra keys or declares no properties.
    """
    if not isinstance(schema, dict) or not args:
        return []
    props = schema.get("properties")
    if not isinstance(props, dict) or not props:
        return []
    if schema.get("additionalProperties") not in (None, False):
        return []                       # explicitly open schema: anything goes
    return sorted(k for k in args if k not in props)


_HTTP_TYPES = {"http", "streamable-http", "streamable_http", "streamablehttp"}


def _is_server_entry(d: Any) -> bool:
    """A usable mcpServers entry: a command to launch, or a URL to connect to."""
    return isinstance(d, dict) and bool(d.get("command") or d.get("url")
                                        or d.get("serverUrl"))


def _split_command(command: str, args: List[str]) -> Tuple[str, List[str]]:
    """`"command": "npx mcp-remote https://..."` with no args -> npx + args.

    Some published configs put the whole command line in `command` (one of
    the 104 claude-code-templates MCP templates does). Launched as is, the OS
    looks for a program literally named "npx mcp-remote https://...". Left
    alone when args are given or the string is an existing path (a program
    under "C:\\Program Files" is one path with a space, not a command line).
    """
    if args or not command or not any(c.isspace() for c in command.strip()) \
            or os.path.exists(command):
        return command, args
    import shlex
    try:
        parts = shlex.split(command, posix=os.name != "nt")
    except ValueError:
        return command, args
    parts = [p.strip('"') for p in parts]
    return (parts[0], parts[1:]) if parts else (command, args)


@dataclass
class McpServerConfig:
    name: str
    command: str
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    scope: str = "global"  # "global" | "project"
    # Which tool's config file this came from: "cortex" (ours, writable) or
    # "cursor"/"claude" (another tool's file, READ-ONLY - we never write there).
    source: str = "cortex"
    # Remote servers: connected over HTTP instead of launched. transport is
    # "stdio" for a command, "http" (streamable HTTP), "sse", or "auto" for
    # a bare url with no type (tries HTTP, then SSE, as Cursor does).
    url: str = ""
    transport: str = "stdio"
    headers: Dict[str, str] = field(default_factory=dict)
    # The user's own steering for this server ("instructions" in its mcp.json
    # entry): what it is for, what never to do with it. Shown to the agent;
    # not part of the signature, since it does not change what runs.
    notes: str = ""

    @classmethod
    def from_dict(cls, name: str, d: Dict[str, Any], scope: str,
                  source: str = "cortex") -> "McpServerConfig":
        enabled = bool(d.get("enabled", True)) and not bool(d.get("disabled", False))
        command = str(d.get("command", "") or "")
        args = [str(a) for a in _expand_env(d.get("args", []) or [])]
        url = "" if command else _expand_env_str(str(d.get("url") or d.get("serverUrl") or ""))
        if url:
            kind = str(d.get("type") or d.get("transport") or "").lower()
            transport = "sse" if kind == "sse" else ("http" if kind in _HTTP_TYPES else "auto")
        else:
            transport = "stdio"
            command, args = _split_command(command, args)
        return cls(
            name=name,
            command=command,
            args=args,
            env={str(k): _expand_env_str(str(v))
                 for k, v in (d.get("env", {}) or {}).items()},
            enabled=enabled,
            scope=scope,
            source=source,
            url=url,
            transport=transport,
            headers={str(k): _expand_env_str(str(v))
                     for k, v in (d.get("headers", {}) or {}).items()},
            notes=str(d.get("instructions") or "") if isinstance(d.get("instructions"), str) else "",
        )

    def to_dict(self) -> Dict[str, Any]:
        if self.url:
            out: Dict[str, Any] = {"url": self.url}
            if self.transport in ("http", "sse"):
                out["type"] = self.transport
            if self.headers:
                out["headers"] = self.headers
        else:
            out = {"command": self.command, "args": self.args}
        if self.env:
            out["env"] = self.env
        if self.notes:
            out["instructions"] = self.notes
        if not self.enabled:
            out["disabled"] = True
        return out

    @property
    def display(self) -> str:
        """What the Settings list shows for this server."""
        if self.url:
            return f"{self.url} ({'SSE' if self.transport == 'sse' else 'HTTP'})"
        return " ".join([self.command] + self.args)

    @property
    def signature(self) -> str:
        """Fingerprint of what this server would actually run.

        Approvals are keyed on this, so changing the command, its arguments
        or the environment invalidates the approval instead of quietly
        reusing it. Only the hash is stored, never the expanded env values,
        which can hold tokens.
        """
        fp: Dict[str, Any] = {"command": self.command,
                              "args": list(self.args),
                              "env": dict(sorted(self.env.items()))}
        if self.url:
            # Only for remote servers, so every approval of a command-based
            # server made before remote support keeps the same fingerprint.
            fp.update(url=self.url, transport=self.transport,
                      headers=dict(sorted(self.headers.items())))
        payload = json.dumps(fp, sort_keys=True, ensure_ascii=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class _ProjectTrust:
    """Which project-supplied MCP servers this user has approved.

    `<project>/.cortex/mcp.json` and the other per-project files ride along
    inside a git clone, so the command they name is untrusted input: opening
    a folder would otherwise be enough to make Cortex launch whatever that
    folder asked for. Two properties matter here and both are deliberate:

      - the store lives in ~/.cortex, never in the project, so a repository
        cannot grant itself trust;
      - entries are keyed by the full command signature, so editing the
        command, its arguments or its env re-arms the gate rather than
        inheriting the earlier approval.

    Mirrors the workspace-trust model other editors use for exactly this
    threat, and stays inside Cortex's own folder.
    """

    def __init__(self, path: Optional[Path] = None):
        self._path = Path(path) if path else (_cortex_home() / TRUST_FILE_NAME)
        self._lock = threading.Lock()

    @staticmethod
    def _project_key(project_root: Optional[str]) -> str:
        """Canonical key for a project folder (case-insensitive on Windows)."""
        if not project_root:
            return ""
        try:
            return str(Path(project_root).resolve()).casefold()
        except Exception:
            return str(project_root).casefold()

    @staticmethod
    def _projects(data: Dict[str, Any]) -> Dict[str, Any]:
        projects = data.get("projects")
        return projects if isinstance(projects, dict) else {}

    def _load(self) -> Dict[str, Any]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except Exception as e:
            # A corrupt store must not block the app; it just means every
            # project server asks again, which fails safe.
            log.warning(f"[MCP] Ignoring unreadable trust store {self._path}: {e}")
            return {}
        return data if isinstance(data, dict) else {}

    def is_approved(self, project_root: Optional[str], name: str,
                    signature: str) -> bool:
        with self._lock:
            per_project = self._projects(self._load()).get(
                self._project_key(project_root))
        if not isinstance(per_project, dict):
            return False
        return bool(signature) and per_project.get(name) == signature

    def approve(self, project_root: Optional[str], name: str,
                signature: str) -> None:
        with self._lock:
            data = self._load()
            projects = data["projects"] = self._projects(data)
            entry = projects.setdefault(self._project_key(project_root), {})
            if not isinstance(entry, dict):
                entry = projects[self._project_key(project_root)] = {}
            entry[name] = signature
            self._save(data)

    def revoke(self, project_root: Optional[str], name: str) -> None:
        with self._lock:
            data = self._load()
            projects = self._projects(data)
            entry = projects.get(self._project_key(project_root))
            if not isinstance(entry, dict) or name not in entry:
                return
            entry.pop(name, None)
            data["projects"] = projects
            self._save(data)

    def _save(self, data: Dict[str, Any]) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = str(self._path) + ".tmp"
            Path(tmp).write_text(json.dumps(data, indent=2), encoding="utf-8")
            os.replace(tmp, self._path)
        except Exception as e:
            log.warning(f"[MCP] Could not write trust store {self._path}: {e}")


class _ServerState:
    """Runtime state of one server (lives on the MCP thread)."""

    def __init__(self, config: McpServerConfig):
        self.config = config
        self.status: str = "connecting"   # connecting | connected | error | disabled | stopped
        self.error: str = ""
        self.session = None               # mcp.ClientSession when connected
        self.tools: List[Any] = []        # mcp Tool objects
        # The server's own "how to use me" text from its initialize response
        # (MCP standard). Was discarded; see src/ai/mcp_guidance.py.
        self.instructions: str = ""
        self.title: str = ""
        # Set when a call "succeeded" but the server's answer says its key or
        # login is wrong (Context7 answers "Invalid API key..." as a normal
        # result). Shown in Settings; cleared by the next normal answer.
        self.warning: str = ""
        self.stop_event: Optional[asyncio.Event] = None
        self.task: Optional[asyncio.Task] = None


class MCPManager:
    """Singleton MCP client manager. All public methods are sync + thread-safe."""

    CONNECT_TIMEOUT = 30.0
    CALL_TIMEOUT = 90.0

    def __init__(self):
        self._lock = threading.Lock()
        self._states: Dict[str, _ServerState] = {}
        self._project_root: Optional[str] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._trust = _ProjectTrust()
        self._started = False
        self._config_checked_at = 0.0
        self._sync_lock = threading.Lock()

    # ── event-loop thread ────────────────────────────────────────────────

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop and self._loop.is_running():
                return self._loop
            loop = asyncio.new_event_loop()

            def _run():
                asyncio.set_event_loop(loop)
                loop.run_forever()

            t = threading.Thread(target=_run, daemon=True, name="MCPLoop")
            t.start()
            self._loop, self._thread = loop, t
            return loop

    def _submit(self, coro, timeout: float):
        """Run a coroutine on the MCP loop from any thread; return its result."""
        loop = self._ensure_loop()
        fut = asyncio.run_coroutine_threadsafe(coro, loop)
        return fut.result(timeout=timeout)

    # ── config ───────────────────────────────────────────────────────────

    def _project_config_path(self) -> Optional[Path]:
        if not self._project_root:
            return None
        return Path(self._project_root) / ".cortex" / "mcp.json"

    @staticmethod
    def _read_config(path: Optional[Path], scope: str,
                     source: str = "cortex") -> Dict[str, McpServerConfig]:
        if not path or not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            servers = data.get("mcpServers", {}) or {}
            out = {}
            for raw_name, d in servers.items():
                name = _sanitize(raw_name)
                # Command (stdio) and url (HTTP/SSE) servers both. Remote ones
                # used to be skipped here without a word: 19 of the 104
                # claude-code-templates MCP templates are url-only, so after
                # `npx claude-code-templates --mcp <one of them>` the server
                # was in .mcp.json and nowhere in Cortex.
                if _is_server_entry(d):
                    out[name] = McpServerConfig.from_dict(name, d, scope, source)
                else:
                    log.warning(f"[MCP] {path}: '{raw_name}' has neither a "
                                f"command nor a url, skipped")
            return out
        except Exception as e:
            log.warning(f"[MCP] Failed to read {path}: {e}")
            return {}

    def load_configs(self) -> Dict[str, McpServerConfig]:
        """Every MCP config Cortex can see, merged.

        Load order (later wins on a name clash):
            1. ~/.cursor/mcp.json, ~/.claude.json, desktop client (global, foreign)
            2. ~/.cortex/mcp.json                                  (global, ours)
            3. <project>/.cursor/mcp.json, <project>/.mcp.json     (project, foreign)
            4. <project>/.cortex/mcp.json                          (project, ours)

        So a project entry beats a global one, and Cortex's own file always
        beats another tool's - which is what lets the user disable an
        inherited server from Settings without editing another tool's file.
        """
        configs: Dict[str, McpServerConfig] = {}
        for path, src in _foreign_global_configs():
            configs.update(self._read_config(path, "global", src))
        configs.update(self._read_config(GLOBAL_CONFIG, "global"))
        if self._project_root:
            for path, src in _foreign_project_configs(self._project_root):
                configs.update(self._read_config(path, "project", src))
        configs.update(self._read_config(self._project_config_path(), "project"))
        return configs

    def _write_scope(self, scope: str, servers: Dict[str, McpServerConfig]) -> None:
        path = GLOBAL_CONFIG if scope == "global" else self._project_config_path()
        if path is None:
            raise RuntimeError("No project open for project-scope MCP config")
        path.parent.mkdir(parents=True, exist_ok=True)
        # Only servers that live in OUR file are written back. A server
        # inherited from another tool stays in its own file; copying it here
        # would fork the config and let the two drift apart.
        data = {"mcpServers": {c.name: c.to_dict() for c in servers.values()
                               if c.scope == scope and c.source == "cortex"}}
        tmp = str(path) + ".tmp"
        Path(tmp).write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def _save_all(self, configs: Dict[str, McpServerConfig]) -> None:
        self._write_scope("global", configs)
        if self._project_config_path():
            self._write_scope("project", configs)

    # ── lifecycle ────────────────────────────────────────────────────────

    def set_project_root(self, root: Optional[str]) -> None:
        self._project_root = root

    def _needs_approval(self, cfg: McpServerConfig) -> bool:
        """True when this server comes from the project folder, unapproved.

        Trust is separate from `enabled` on purpose. A repository ships
        `"enabled": true` in its own config; that is the repository talking,
        not the user. Only an explicit approval recorded in ~/.cortex counts.
        """
        if cfg.scope != "project":
            return False
        return not self._trust.is_approved(self._project_root, cfg.name,
                                           cfg.signature)

    def approve_server(self, name: str) -> None:
        """Record the user's approval for a project server, then start it."""
        cfg = self.load_configs().get(name)
        if cfg is None:
            return
        if cfg.scope == "project":
            self._trust.approve(self._project_root, cfg.name, cfg.signature)
            log.info(f"[MCP] project server '{name}' approved by the user")
        self.reconnect(name)

    def start(self) -> None:
        """(Re)start all enabled servers from config. Non-blocking.

        Subscription gate: without a signed-in account + active plan, the
        configs are kept but NO server process is launched, every entry
        shows status 'subscription' in Settings.

        Trust gate: a server declared inside the project folder is untrusted
        input, so it is listed but not launched until the user approves its
        exact command. See _ProjectTrust.
        """
        configs = self.load_configs()
        self.stop()
        self._started = True
        self._config_checked_at = time.monotonic()
        self._subscribed = _has_active_subscription()
        for name, cfg in configs.items():
            state = _ServerState(cfg)
            if not self._subscribed:
                state.status = "subscription"
                state.error = _SUBSCRIPTION_MSG
            elif not cfg.enabled:
                state.status = "disabled"
            elif self._needs_approval(cfg):
                state.status = "needs_approval"
                state.error = _APPROVAL_MSG
            self._states[name] = state
            # "connecting" is the initial value, so it only survives when no
            # gate above applied, which is exactly when the server may launch.
            if state.status == "connecting":
                self._launch(state)

    def _launch(self, state: _ServerState) -> None:
        loop = self._ensure_loop()

        def _schedule():
            state.stop_event = asyncio.Event()
            state.task = loop.create_task(self._server_task(state))

        loop.call_soon_threadsafe(_schedule)

    # Bounded auto-retry with backoff. Real-world evidence (a customer log):
    # a server failed 12+ times over 6 minutes with WinError 2 / TaskGroup
    # errors, needing the user to manually remove+re-add it before ONE
    # attempt happened to succeed, nothing about the config had changed.
    # That means the failure was transient (PATH/env not yet settled, first
    # npx/uvx invocation downloading+antivirus-scanning a package, etc.), and
    # a plain retry-with-backoff would have fixed it without the user
    # touching anything. Capped so a genuinely broken command (uv not
    # installed at all) still settles into "error" instead of retrying
    # forever.
    _RETRY_DELAYS = (3.0, 8.0, 20.0)  # seconds between attempts 1→2, 2→3, 3→4

    async def _server_task(self, state: _ServerState) -> None:
        """Own one server connection for its whole lifetime, retrying
        transient startup failures before settling into 'error'."""
        cfg = state.config
        # A url with no "type" is tried as streamable HTTP, then SSE: older
        # remote servers only speak SSE and their configs rarely say so.
        transports = ["http", "sse"] if cfg.transport == "auto" else [cfg.transport]
        attempt = 0
        while True:
            attempt += 1
            try:
                first_error: Optional[BaseException] = None
                for transport in transports:
                    try:
                        await self._run_session(state, transport, attempt)
                        return  # clean stop (user disabled/removed it), do not retry
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        if state.status == "connected":
                            raise   # dropped after connecting: not a transport mismatch
                        first_error = first_error or e
                raise first_error  # type: ignore[misc]
            except asyncio.CancelledError:
                return  # task cancelled (manager shutting down), never retry
            except Exception as e:
                reason = _error_text(e)
                state.status = "error"
                state.error = reason[:300]
                if attempt <= len(self._RETRY_DELAYS) and not _is_auth_error(e):
                    delay = self._RETRY_DELAYS[attempt - 1]
                    log.warning(f"[MCP] '{cfg.name}' failed (attempt {attempt}): {reason} "
                                f"- retrying in {delay:.0f}s")
                    state.error = f"{reason[:250]} (retrying in {delay:.0f}s…)"
                    try:
                        await asyncio.wait_for(state.stop_event.wait(), delay)
                        return  # stop() was called while we were waiting to retry
                    except asyncio.TimeoutError:
                        continue  # delay elapsed, try again
                else:
                    log.warning(f"[MCP] '{cfg.name}' failed permanently after "
                                f"{attempt} attempts: {reason}")
                    return
            finally:
                state.session = None
                if state.status == "connected":
                    state.status = "stopped"

    async def _run_session(self, state: _ServerState, transport: str,
                           attempt: int) -> None:
        """Connect over one transport, list the tools, hold until stopped."""
        from mcp import ClientSession
        cfg = state.config
        async with self._open_streams(cfg, transport) as (read, write):
            async with ClientSession(read, write) as session:
                _init = await asyncio.wait_for(session.initialize(), self.CONNECT_TIMEOUT)
                state.instructions = str(getattr(_init, "instructions", None) or "")[:4000]
                _info = getattr(_init, "serverInfo", None)
                state.title = str(getattr(_info, "title", None) or "")[:80]
                tools_resp = await asyncio.wait_for(session.list_tools(), self.CONNECT_TIMEOUT)
                state.tools = list(tools_resp.tools)
                state.session = session
                state.status = "connected"
                state.error = ""
                log.info(f"[MCP] '{cfg.name}' connected"
                         f"{'' if transport == 'stdio' else ' over ' + transport.upper()}, "
                         f"{len(state.tools)} tool(s): "
                         f"{[t.name for t in state.tools][:8]} "
                         f"(attempt {attempt}, manager id={id(self)})")
                await state.stop_event.wait()  # hold contexts open until stopped

    @asynccontextmanager
    async def _open_streams(self, cfg: McpServerConfig, transport: str):
        """(read, write) streams to the server over `transport`."""
        if transport == "stdio":
            from mcp import StdioServerParameters
            from mcp.client.stdio import stdio_client
            params = StdioServerParameters(
                command=_resolve_command(cfg.command),
                args=_launch_args(cfg, self._project_root),
                env=_server_environment(cfg.env),
                cwd=_server_cwd(self._project_root),
            )
            # errlog MUST have a real OS file descriptor. stdio_client
            # defaults to sys.stderr, but in the frozen console=False build
            # the no-console runtime hook replaced sys.stderr with a null
            # writer whose fileno() returned -1, the npx child spawn then
            # failed with [Errno 9] Bad file descriptor (every server,
            # instantly, .exe only; dev runs worked because a real console
            # provided real streams). os.devnull is a genuine fd.
            with open(os.devnull, "w", encoding="utf-8") as _errlog:
                async with stdio_client(params, errlog=_errlog) as (read, write):
                    yield read, write
        elif transport == "sse":
            from mcp.client.sse import sse_client
            async with sse_client(cfg.url, headers=cfg.headers or None,
                                  timeout=self.CONNECT_TIMEOUT) as (read, write):
                yield read, write
        else:
            import httpx
            from mcp.client.streamable_http import (create_mcp_http_client,
                                                    streamable_http_client)
            async with create_mcp_http_client(
                    headers=cfg.headers or None,
                    timeout=httpx.Timeout(self.CONNECT_TIMEOUT, read=300.0)) as client:
                async with streamable_http_client(cfg.url, http_client=client) as (read, write, _):
                    yield read, write

    def stop(self) -> None:
        """Stop every running server (best effort, fast)."""
        loop = self._loop
        for state in list(self._states.values()):
            if loop and state.stop_event is not None:
                loop.call_soon_threadsafe(state.stop_event.set)
        self._states = {}

    # How often the config files are re-read for changes made outside
    # Settings: `npx claude-code-templates --mcp ...`, `claude mcp add`, an
    # edit by hand or by the agent.
    _CONFIG_CHECK_INTERVAL = 2.0

    def sync(self, force: bool = False) -> bool:
        """Apply config changes made while Cortex runs. True if any.

        Servers were read only by start() - at project open and sign-in - so a
        server added to .mcp.json or ~/.cortex/mcp.json afterwards was listed
        in Settings (get_status reads the files) but never started, and an
        edited or removed one kept running the old command. A new or changed
        entry goes through reconnect(), so the same gates apply: subscription,
        disabled, and approval for anything declared in the project.
        """
        if not self._started:
            return False    # nothing launched yet; start() reads everything
        now = time.monotonic()
        if not force and now - self._config_checked_at < self._CONFIG_CHECK_INTERVAL:
            return False
        if not self._sync_lock.acquire(blocking=False):
            return False    # another thread is already syncing
        try:
            self._config_checked_at = now
            configs = self.load_configs()
            changed = [n for n, c in configs.items()
                       if n not in self._states
                       or self._states[n].config.signature != c.signature
                       or self._states[n].config.enabled != c.enabled]
            removed = [n for n in list(self._states) if n not in configs]
            for name in removed:
                state = self._states.pop(name, None)
                if state and self._loop and state.stop_event is not None:
                    self._loop.call_soon_threadsafe(state.stop_event.set)
            for name in changed:
                self.reconnect(name)
            if changed or removed:
                log.info(f"[MCP] config changed on disk: started/updated {changed}, "
                         f"stopped {removed}")
            return bool(changed or removed)
        except Exception as e:
            log.warning(f"[MCP] config sync failed: {e}")
            return False
        finally:
            self._sync_lock.release()

    def reconnect(self, name: str) -> None:
        cfg = self.load_configs().get(name)
        if not cfg:
            return
        old = self._states.get(name)
        if old and self._loop and old.stop_event is not None:
            self._loop.call_soon_threadsafe(old.stop_event.set)
        state = _ServerState(cfg)
        self._subscribed = _has_active_subscription()
        if not self._subscribed:
            state.status = "subscription"
            state.error = _SUBSCRIPTION_MSG
            self._states[name] = state
            return
        if not cfg.enabled:
            state.status = "disabled"
            self._states[name] = state
            return
        if self._needs_approval(cfg):
            state.status = "needs_approval"
            state.error = _APPROVAL_MSG
            self._states[name] = state
            return
        self._states[name] = state
        self._launch(state)

    # ── agent-facing API ─────────────────────────────────────────────────

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        """OpenAI-style schemas for every tool of every CONNECTED server."""
        self.sync()
        if not getattr(self, "_subscribed", False):
            return []
        defs: List[Dict[str, Any]] = []
        for name, state in list(self._states.items()):
            if state.status != "connected":
                continue
            for tool in state.tools:
                schema = getattr(tool, "inputSchema", None) or \
                    {"type": "object", "properties": {}}
                defs.append({
                    "type": "function",
                    "function": {
                        "name": f"mcp__{name}__{_sanitize(tool.name)}",
                        "description": (tool.description or f"MCP tool '{tool.name}' "
                                        f"from server '{name}'")[:1024],
                        "parameters": schema,
                    },
                })
        return defs

    def get_server_guides(self) -> List[Dict[str, Any]]:
        """Connected servers with what identifies them and their instructions,
        for the system prompt's per-server usage guide."""
        out = []
        # Notes come from the file as it is now: editing only a server's notes
        # does not restart it (they do not change what runs), but the agent
        # should see the edit on its next message.
        try:
            current = self.load_configs()
        except Exception:
            current = {}
        for name, state in list(self._states.items()):
            if state.status != "connected":
                continue
            cfg = state.config
            notes = (current.get(name) or cfg).notes
            tools = []
            for t in state.tools:
                ann = getattr(t, "annotations", None)
                tools.append({
                    "name": _sanitize(t.name),
                    "description": t.description or "",
                    "inputSchema": getattr(t, "inputSchema", None) or {},
                    "annotations": ann.model_dump(exclude_none=True) if ann is not None else {},
                })
            out.append({"name": name, "title": state.title,
                        "instructions": state.instructions, "notes": notes,
                        "tools": tools})
        return out

    @staticmethod
    def is_mcp_tool(tool_name: str) -> bool:
        return tool_name.startswith("mcp__")

    def call_tool(self, qualified_name: str, args: Dict[str, Any],
                  timeout: float = CALL_TIMEOUT) -> Tuple[bool, str]:
        """Execute mcp__<server>__<tool>. Returns (success, result_text).

        Kept for callers that can only show text. Images come back as a
        placeholder here; call_tool_full() returns them.
        """
        ok, text, images = self.call_tool_full(qualified_name, args, timeout)
        if images:
            note = "[image content returned, not displayable in this context]"
            text = f"{text}\n{note}" if text and text != "(empty result)" else note
        return ok, text

    def _note_auth_problem(self, qualified_name: str, ok: bool, text: str) -> None:
        """Flag a server whose answer says its credentials are wrong.

        Some servers report a bad key as an ordinary result, not an error:
        every Context7 call on 2026-10-03 "succeeded" with the 87-char text
        "Invalid API key. Please check your API key...", Settings showed the
        server healthy, and nothing pointed at the key.
        """
        parts = qualified_name.split("__", 2)
        state = self._states.get(parts[1]) if len(parts) == 3 else None
        if state is None:
            return
        short = (text or "").strip()
        if len(short) <= 400 and _AUTH_PROBLEM_RE.search(short):
            if state.warning != short[:200]:
                log.warning(f"[MCP] '{parts[1]}' says its credentials are wrong: {short[:200]}")
            state.warning = short[:200]
        elif ok and short:
            state.warning = ""

    def tool_schema(self, qualified_name: str) -> Optional[Dict[str, Any]]:
        """The input schema of one connected tool, or None."""
        try:
            _, server, tool = qualified_name.split("__", 2)
        except ValueError:
            return None
        state = self._states.get(server)
        if state is None:
            return None
        for t in state.tools:
            if _sanitize(t.name) == tool:
                return getattr(t, "inputSchema", None)
        return None

    def call_tool_full(self, qualified_name: str, args: Dict[str, Any],
                       timeout: float = CALL_TIMEOUT
                       ) -> Tuple[bool, str, List[Tuple[str, str]]]:
        """Execute an MCP tool. Returns (success, text, images).

        `images` is a list of (mime_type, base64_data). They used to be
        replaced by a placeholder string, so a screenshot tool's actual
        screenshot never reached the model - it saw a sentence saying an
        image existed and went hunting for the file on disk instead.

        Every failure is logged. MCP errors never reached cortex.log before,
        so a run that failed five times in a row left only "1 tool call(s)"
        lines behind. Argument NAMES are logged, never values: a value can be
        anything the user typed, including a password into a form field.
        """
        args = _scratch_file_args(qualified_name.split("__")[-1], args or {},
                                  self._project_root)
        ok, text, images = self._call_tool_inner(qualified_name, args, timeout)
        self._note_auth_problem(qualified_name, ok, text)
        if ok:
            log.info(f"[MCP] {qualified_name} ok | args={sorted(args)} | "
                     f"{len(text)} chars{f', {len(images)} image(s)' if images else ''}")
        else:
            log.warning(f"[MCP] {qualified_name} FAILED | args={sorted(args)} | "
                        f"{text[:300]}")
        return ok, text, images

    def _call_tool_inner(self, qualified_name: str, args: Dict[str, Any],
                         timeout: float) -> Tuple[bool, str, List[Tuple[str, str]]]:
        if not getattr(self, "_subscribed", False):
            return False, _SUBSCRIPTION_MSG, []
        try:
            _, server, tool = qualified_name.split("__", 2)
        except ValueError:
            return False, f"Malformed MCP tool name: {qualified_name}", []
        state = self._states.get(server)
        if state is None or state.status != "connected" or state.session is None:
            return False, (f"MCP server '{server}' is not connected "
                           f"(status: {state.status if state else 'unknown'}"
                           f"{': ' + state.error if state and state.error else ''})"), []
        # The namespaced name was sanitized, map back to the real tool name.
        real_tool = next((t for t in state.tools if _sanitize(t.name) == tool), None)
        real = real_tool.name if real_tool is not None else tool

        schema = getattr(real_tool, "inputSchema", None) if real_tool is not None else None
        unknown = _unknown_arguments(schema, args)
        if unknown:
            valid = ", ".join(sorted((schema or {}).get("properties", {}))) or "(none)"
            return False, (
                f"{qualified_name} has no parameter named "
                f"{', '.join(repr(u) for u in unknown)}. The server would have "
                f"silently ignored it and done something else. Valid parameters: "
                f"{valid}. Load the full definition with MCPToolSearch "
                f"(query \"select:{qualified_name}\") if unsure."), []

        try:
            result = self._submit(state.session.call_tool(real, args), timeout)
        except Exception as e:
            return False, f"MCP call failed: {e}", []

        texts: List[str] = []
        images: List[Tuple[str, str]] = []
        for item in getattr(result, "content", []) or []:
            t = getattr(item, "text", None)
            if t:
                texts.append(t)
            elif getattr(item, "type", "") == "image" and getattr(item, "data", None):
                images.append((getattr(item, "mimeType", None) or "image/png",
                               item.data))
        text = "\n".join(texts) if texts else ("" if images else "(empty result)")
        if getattr(result, "isError", False):
            return False, text or "(the MCP server reported an error with no message)", images
        return True, text, images

    # ── settings-UI API ──────────────────────────────────────────────────

    def get_status(self) -> List[Dict[str, Any]]:
        self.sync()
        configs = self.load_configs()
        out = []
        seen = set()
        for name, state in list(self._states.items()):
            cfg = configs.get(name, state.config)
            out.append({
                "name": name,
                "command": cfg.display,
                "scope": cfg.scope,
                "source": cfg.source,
                "enabled": cfg.enabled,
                "status": state.status,
                "error": state.error,
                "needs_approval": state.status == "needs_approval",
                "tools": [t.name for t in state.tools],
                "warning": state.warning,
            })
            seen.add(name)
        for name, cfg in configs.items():   # configured but not yet started
            if name not in seen:
                if not cfg.enabled:
                    pending = "disabled"
                elif self._needs_approval(cfg):
                    pending = "needs_approval"
                else:
                    pending = "stopped"
                out.append({"name": name,
                            "command": cfg.display,
                            "scope": cfg.scope, "source": cfg.source,
                            "enabled": cfg.enabled,
                            "status": pending,
                            "error": _APPROVAL_MSG if pending == "needs_approval" else "",
                            "needs_approval": pending == "needs_approval",
                            "tools": []})
        return sorted(out, key=lambda s: s["name"])

    def add_server(self, name: str, command_line: str,
                   env: Optional[Dict[str, str]] = None, scope: str = "global") -> None:
        """Add a server from Settings: a command line, or a URL.

        A URL (http/https) is a remote server and the second field holds its
        headers (e.g. Authorization=Bearer ...), the way Claude Code's
        `claude mcp add --transport http` and Cursor's url entries work. The
        form only took commands, so a remote server such as Context7's
        https://mcp.context7.com/mcp could not be added from Settings at all.
        """
        if not _has_active_subscription():
            raise PermissionError(_SUBSCRIPTION_MSG)
        configs = self.load_configs()
        name = _sanitize(name)
        line = (command_line or "").strip()
        if re.match(r"^https?://", line, re.I):
            url = line.split()[0]
            configs[name] = McpServerConfig(
                name=name, command="", url=url, transport="auto",
                headers=dict(env or {}), enabled=True, scope=scope,
            )
            log.info(f"[MCP] added '{name}' ({scope}): URL {url}, "
                     f"{len(env or {})} header(s): {sorted(env or {})}")
        else:
            import shlex
            parts = shlex.split(line, posix=False)
            if not parts:
                raise ValueError("Empty command")
            # shlex posix=False keeps quotes, strip them from each part
            parts = [p.strip('"') for p in parts]
            configs[name] = McpServerConfig(
                name=name, command=parts[0], args=parts[1:],
                env=env or {}, enabled=True, scope=scope,
            )
            # Names only for env (values can be keys); args can hold a key
            # too, so only how many there are.
            log.info(f"[MCP] added '{name}' ({scope}): command {parts[0]!r} with "
                     f"{len(parts) - 1} arg(s), env {sorted(env or {})}")
        self._write_scope(scope, configs)
        if scope == "project":
            # Typing it into Settings is the user vouching for it.
            self._trust.approve(self._project_root, name,
                                configs[name].signature)
        self.reconnect(name)

    def remove_server(self, name: str) -> None:
        configs = self.load_configs()
        cfg = configs.get(name)
        if cfg is None:
            return
        if cfg.scope == "project":
            # Removing it withdraws the approval too, so a later copy of the
            # same project file cannot ride on the old one.
            self._trust.revoke(self._project_root, name)
        state = self._states.pop(name, None)
        if state and self._loop and state.stop_event is not None:
            self._loop.call_soon_threadsafe(state.stop_event.set)
        if cfg.source != "cortex":
            # Cannot delete another tool's entry from its own file. Shadow it
            # with a disabled cortex-owned copy so it stops launching here and
            # other tools keep working unchanged.
            cfg.enabled = False
            cfg.source = "cortex"
            cfg.scope = _shadow_scope(cfg.scope, self._project_root)
            self._write_scope(cfg.scope, configs)
            return
        configs.pop(name, None)
        self._write_scope(cfg.scope, configs)

    # ── full removal (Settings ✕) ─────────────────────────────────────────

    def _config_sources(self) -> List[Tuple[Path, str, str]]:
        """Every config file Cortex reads, as (path, scope, source)."""
        out = [(Path(p), "global", src) for p, src in _foreign_global_configs()]
        out.append((GLOBAL_CONFIG, "global", "cortex"))
        if self._project_root:
            out += [(Path(p), "project", src) for p, src in _foreign_project_configs(self._project_root)]
            pc = self._project_config_path()
            if pc:
                out.append((pc, "project", "cortex"))
        return out

    def _files_defining(self, name: str) -> List[Tuple[Path, str, List[str]]]:
        """(file, source, raw keys) for every file that defines `name`."""
        hits = []
        for path, _scope, src in self._config_sources():
            try:
                if not path.exists():
                    continue
                servers = (json.loads(path.read_text(encoding="utf-8")).get("mcpServers") or {})
            except Exception:
                continue
            keys = [k for k in servers if _sanitize(k) == name]
            if keys:
                hits.append((path, src, keys))
        return hits

    def removal_plan(self, name: str) -> Dict[str, Any]:
        """What removing `name` completely would touch - shown before it runs."""
        cfg = self.load_configs().get(name)
        files = self._files_defining(name)
        pkg = _npx_package(cfg) if cfg else None
        dirs = [d for d in _npx_cache_dirs(pkg)] if pkg else []
        still_used = bool(pkg) and any(
            _npx_package(c) == pkg for n, c in self.load_configs().items() if n != name)
        return {
            "name": name,
            "cortex_files": [str(p) for p, src, _ in files if src == "cortex"],
            "other_tool_files": [{"path": str(p), "tool": src} for p, src, _ in files if src != "cortex"],
            "package": pkg or "",
            "package_dirs": [] if still_used else [str(d) for d in dirs],
            "package_mb": 0 if still_used else round(sum(_dir_size(d) for d in dirs) / 1e6, 1),
            "package_shared": still_used,
        }

    def remove_fully(self, name: str, other_tools: bool = True,
                     delete_files: bool = True) -> Dict[str, Any]:
        """Remove a server for real, not hide it.

        The ✕ in Settings used to remove only Cortex's own entry, and for a
        server inherited from another tool (Claude Code's ~/.claude.json,
        Cursor's mcp.json) it wrote a disabled copy into ~/.cortex/mcp.json -
        so the server stayed listed as "disabled", stayed in the other tool's
        file, and its downloaded package (63 MB for one context7 copy) stayed
        in the npm cache.

        Now: every Cortex entry for it goes (including those disabled
        copies); with other_tools, its entry in the other tools' files goes
        too, after a backup of each file to ~/.cortex/backups/; with
        delete_files, its npx download folders go, unless another configured
        server still runs the same package.
        """
        plan = self.removal_plan(name)
        cfg = self.load_configs().get(name)
        if cfg is not None and cfg.scope == "project":
            self._trust.revoke(self._project_root, name)
        state = self._states.pop(name, None)
        if state and self._loop and state.stop_event is not None:
            self._loop.call_soon_threadsafe(state.stop_event.set)
        removed, failed = [], []
        for path, src, keys in self._files_defining(name):
            if src != "cortex" and not other_tools:
                continue
            try:
                if src != "cortex":
                    _backup_file(path)
                _remove_keys(path, keys)
                removed.append(str(path))
            except Exception as e:
                failed.append(f"{path}: {e}")
        if not other_tools and self._files_defining(name):
            # Still in another tool's file, which the user chose to keep: hide
            # it from Cortex with a disabled entry of its own, as before.
            allc = self.load_configs()
            hidden = allc.get(name)
            if hidden is not None:
                hidden.enabled = False
                hidden.source = "cortex"
                hidden.scope = _shadow_scope(hidden.scope, self._project_root)
                self._write_scope(hidden.scope, allc)
        freed = 0.0
        deleted_dirs = []
        if delete_files and plan["package_dirs"]:
            if state is not None:
                time.sleep(0.5)   # let the stopped server's process exit first
            for d in plan["package_dirs"]:
                try:
                    size = _dir_size(Path(d))
                    shutil.rmtree(d)
                    freed += size
                    deleted_dirs.append(d)
                except Exception as e:
                    failed.append(f"{d}: {e}")
        result = {"removed_from": removed, "deleted_dirs": deleted_dirs,
                  "freed_mb": round(freed / 1e6, 1), "failed": failed}
        log.info(f"[MCP] removed '{name}' completely: config {removed}, "
                 f"deleted {len(deleted_dirs)} download folder(s) ({result['freed_mb']} MB)"
                 + (f", failed: {failed}" if failed else ""))
        return result

    def set_enabled(self, name: str, enabled: bool) -> None:
        configs = self.load_configs()
        cfg = configs.get(name)
        if cfg is None:
            return
        cfg.enabled = enabled
        if cfg.source != "cortex":
            # Inherited from another tool: we must not edit their file, so
            # write a cortex-owned copy instead, at the SAME scope.
            #
            # This used to force scope="global" on the belief that ours is
            # always applied last. It is not: load_configs() reads foreign
            # global, then cortex global, then foreign PROJECT, then cortex
            # project. A global shadow is therefore overwritten by any
            # project-scoped foreign entry, so disabling a server that came
            # from <project>/.mcp.json did nothing at all - it kept starting
            # and kept failing, with the config screen showing it disabled.
            cfg.source = "cortex"
            cfg.scope = _shadow_scope(cfg.scope, self._project_root)
        if cfg.scope == "project":
            # Toggling a project server in Settings is a deliberate user act,
            # so use it as the approval instead of asking twice. Turning it
            # off withdraws the approval.
            if enabled:
                self._trust.approve(self._project_root, cfg.name, cfg.signature)
            else:
                self._trust.revoke(self._project_root, cfg.name)
        self._write_scope(cfg.scope, configs)
        self.reconnect(name)

    def import_json(self, text: str, scope: str = "global") -> int:
        """Import a standard {"mcpServers": {...}} blob. Returns servers added."""
        if not _has_active_subscription():
            raise PermissionError(_SUBSCRIPTION_MSG)
        data = json.loads(text)
        servers = data.get("mcpServers", data if isinstance(data, dict) else {})
        if not isinstance(servers, dict) or not servers:
            raise ValueError('No "mcpServers" object found in the JSON')
        configs = self.load_configs()
        added = 0
        added_names: List[str] = []
        for raw_name, d in servers.items():
            if not _is_server_entry(d):
                continue
            name = _sanitize(raw_name)
            configs[name] = McpServerConfig.from_dict(name, d, scope)
            added_names.append(name)
            added += 1
        if not added:
            raise ValueError("No valid servers in JSON (each needs a 'command' or a 'url')")
        self._write_scope(scope, configs)
        if scope == "project":
            # Pasted into Settings by the user, so treat it as approved.
            for name in added_names:
                self._trust.approve(self._project_root, name,
                                    configs[name].signature)
        self.start()
        return added


_manager: Optional[MCPManager] = None
_manager_lock = threading.Lock()


def get_mcp_manager() -> MCPManager:
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                _manager = MCPManager()
    return _manager
