"""
mcp_manager.py — Model Context Protocol client manager
=======================================================

Connects Cortex to external MCP servers (databases, APIs, SaaS tools…) and
exposes their tools to the agent as regular tool definitions.

Design:
  - Config uses the INDUSTRY-STANDARD mcp.json format (same as Claude
    Desktop / Cursor), so any server README's config snippet works as-is:
        {"mcpServers": {"postgres": {"command": "npx", "args": [...]}}}
    Global config:  ~/.cortex/mcp.json
    Project config: <project>/.cortex/mcp.json   (overrides same names)
  - The official `mcp` SDK is asyncio-based; Cortex's agent loop is not.
    A dedicated daemon thread runs a private asyncio loop; every public
    method here is SYNCHRONOUS and thread-safe.
  - Each connected server's tools are namespaced `mcp__<server>__<tool>`
    and returned as OpenAI-style schemas — the agent treats them exactly
    like built-in tools (including the permission/autonomy gate).
  - Failures NEVER crash the app: a broken server shows status="error"
    with the message in Settings → MCP Servers.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.utils.logger import get_logger

log = get_logger("mcp_manager")

_NAME_RE = re.compile(r"[^A-Za-z0-9_-]")

GLOBAL_CONFIG = Path.home() / ".cortex" / "mcp.json"

_SUBSCRIPTION_MSG = ("MCP servers require an active Cortex subscription "
                     "($10/month or $80/year) and a signed-in account — "
                     "see https://cortex-ide.app/pricing/")


def _has_active_subscription() -> bool:
    """MCP is a SUBSCRIPTION feature: signed in + active plan required.
    Fails CLOSED — if the check can't run, MCP stays locked."""
    try:
        from src.core.cortex_api import get_api_client
        return bool(get_api_client().has_subscription())
    except Exception:
        return False


def _sanitize(name: str) -> str:
    return _NAME_RE.sub("_", name)[:48] or "server"


@dataclass
class McpServerConfig:
    name: str
    command: str
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    scope: str = "global"  # "global" | "project"

    @classmethod
    def from_dict(cls, name: str, d: Dict[str, Any], scope: str) -> "McpServerConfig":
        enabled = bool(d.get("enabled", True)) and not bool(d.get("disabled", False))
        return cls(
            name=name,
            command=str(d.get("command", "")),
            args=[str(a) for a in d.get("args", []) or []],
            env={str(k): str(v) for k, v in (d.get("env", {}) or {}).items()},
            enabled=enabled,
            scope=scope,
        )

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"command": self.command, "args": self.args}
        if self.env:
            out["env"] = self.env
        if not self.enabled:
            out["disabled"] = True
        return out


class _ServerState:
    """Runtime state of one server (lives on the MCP thread)."""

    def __init__(self, config: McpServerConfig):
        self.config = config
        self.status: str = "connecting"   # connecting | connected | error | disabled | stopped
        self.error: str = ""
        self.session = None               # mcp.ClientSession when connected
        self.tools: List[Any] = []        # mcp Tool objects
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
    def _read_config(path: Optional[Path], scope: str) -> Dict[str, McpServerConfig]:
        if not path or not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            servers = data.get("mcpServers", {}) or {}
            out = {}
            for raw_name, d in servers.items():
                name = _sanitize(raw_name)
                if isinstance(d, dict) and d.get("command"):
                    out[name] = McpServerConfig.from_dict(name, d, scope)
            return out
        except Exception as e:
            log.warning(f"[MCP] Failed to read {path}: {e}")
            return {}

    def load_configs(self) -> Dict[str, McpServerConfig]:
        """Global + project configs; project wins on name clashes."""
        configs = self._read_config(GLOBAL_CONFIG, "global")
        configs.update(self._read_config(self._project_config_path(), "project"))
        return configs

    def _write_scope(self, scope: str, servers: Dict[str, McpServerConfig]) -> None:
        path = GLOBAL_CONFIG if scope == "global" else self._project_config_path()
        if path is None:
            raise RuntimeError("No project open for project-scope MCP config")
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"mcpServers": {c.name: c.to_dict() for c in servers.values()
                               if c.scope == scope}}
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

    def start(self) -> None:
        """(Re)start all enabled servers from config. Non-blocking.

        Subscription gate: without a signed-in account + active plan, the
        configs are kept but NO server process is launched — every entry
        shows status 'subscription' in Settings.
        """
        configs = self.load_configs()
        self.stop()
        self._subscribed = _has_active_subscription()
        for name, cfg in configs.items():
            state = _ServerState(cfg)
            if not self._subscribed:
                state.status = "subscription"
                state.error = _SUBSCRIPTION_MSG
            elif not cfg.enabled:
                state.status = "disabled"
            self._states[name] = state
            if self._subscribed and cfg.enabled:
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
    # attempt happened to succeed — nothing about the config had changed.
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
        attempt = 0
        while True:
            attempt += 1
            try:
                from mcp import ClientSession, StdioServerParameters
                from mcp.client.stdio import stdio_client, get_default_environment

                env = dict(get_default_environment())
                env.update(cfg.env)
                params = StdioServerParameters(command=cfg.command, args=cfg.args, env=env)

                # errlog MUST have a real OS file descriptor. stdio_client
                # defaults to sys.stderr, but in the frozen console=False build
                # the no-console runtime hook replaced sys.stderr with a null
                # writer whose fileno() returned -1 — the npx child spawn then
                # failed with [Errno 9] Bad file descriptor (every server,
                # instantly, .exe only; dev runs worked because a real console
                # provided real streams). os.devnull is a genuine fd.
                with open(os.devnull, "w", encoding="utf-8") as _errlog:
                    async with stdio_client(params, errlog=_errlog) as (read, write):
                        async with ClientSession(read, write) as session:
                            await asyncio.wait_for(session.initialize(), self.CONNECT_TIMEOUT)
                            tools_resp = await asyncio.wait_for(session.list_tools(), self.CONNECT_TIMEOUT)
                            state.tools = list(tools_resp.tools)
                            state.session = session
                            state.status = "connected"
                            state.error = ""
                            log.info(f"[MCP] '{cfg.name}' connected — {len(state.tools)} tool(s): "
                                     f"{[t.name for t in state.tools][:8]} "
                                     f"(attempt {attempt}, manager id={id(self)})")
                            await state.stop_event.wait()  # hold contexts open until stopped
                        return  # clean stop (user disabled/removed it) — do not retry
            except asyncio.CancelledError:
                return  # task cancelled (manager shutting down) — never retry
            except Exception as e:
                state.status = "error"
                state.error = str(e)[:300]
                if attempt <= len(self._RETRY_DELAYS):
                    delay = self._RETRY_DELAYS[attempt - 1]
                    log.warning(f"[MCP] '{cfg.name}' failed (attempt {attempt}): {e} "
                                f"— retrying in {delay:.0f}s")
                    state.error = f"{str(e)[:250]} (retrying in {delay:.0f}s…)"
                    try:
                        await asyncio.wait_for(state.stop_event.wait(), delay)
                        return  # stop() was called while we were waiting to retry
                    except asyncio.TimeoutError:
                        continue  # delay elapsed — try again
                else:
                    log.warning(f"[MCP] '{cfg.name}' failed permanently after "
                                f"{attempt} attempts: {e}")
                    return
            finally:
                state.session = None
                if state.status == "connected":
                    state.status = "stopped"

    def stop(self) -> None:
        """Stop every running server (best effort, fast)."""
        loop = self._loop
        for state in list(self._states.values()):
            if loop and state.stop_event is not None:
                loop.call_soon_threadsafe(state.stop_event.set)
        self._states = {}

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
        self._states[name] = state
        self._launch(state)

    # ── agent-facing API ─────────────────────────────────────────────────

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        """OpenAI-style schemas for every tool of every CONNECTED server."""
        if not getattr(self, "_subscribed", False):
            return []
        defs: List[Dict[str, Any]] = []
        for name, state in self._states.items():
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

    @staticmethod
    def is_mcp_tool(tool_name: str) -> bool:
        return tool_name.startswith("mcp__")

    def call_tool(self, qualified_name: str, args: Dict[str, Any],
                  timeout: float = CALL_TIMEOUT) -> Tuple[bool, str]:
        """Execute mcp__<server>__<tool>. Returns (success, result_text)."""
        if not getattr(self, "_subscribed", False):
            return False, _SUBSCRIPTION_MSG
        try:
            _, server, tool = qualified_name.split("__", 2)
        except ValueError:
            return False, f"Malformed MCP tool name: {qualified_name}"
        state = self._states.get(server)
        if state is None or state.status != "connected" or state.session is None:
            return False, (f"MCP server '{server}' is not connected "
                           f"(status: {state.status if state else 'unknown'}"
                           f"{': ' + state.error if state and state.error else ''})")
        # The namespaced name was sanitized — map back to the real tool name.
        real = next((t.name for t in state.tools if _sanitize(t.name) == tool), tool)
        try:
            result = self._submit(state.session.call_tool(real, args or {}), timeout)
        except Exception as e:
            return False, f"MCP call failed: {e}"

        texts: List[str] = []
        for item in getattr(result, "content", []) or []:
            t = getattr(item, "text", None)
            if t:
                texts.append(t)
            elif getattr(item, "type", "") == "image":
                texts.append("[image content returned — not displayable in this context]")
        text = "\n".join(texts) if texts else "(empty result)"
        if getattr(result, "isError", False):
            return False, text
        return True, text

    # ── settings-UI API ──────────────────────────────────────────────────

    def get_status(self) -> List[Dict[str, Any]]:
        configs = self.load_configs()
        out = []
        seen = set()
        for name, state in self._states.items():
            cfg = configs.get(name, state.config)
            out.append({
                "name": name,
                "command": " ".join([cfg.command] + cfg.args),
                "scope": cfg.scope,
                "enabled": cfg.enabled,
                "status": state.status,
                "error": state.error,
                "tools": [t.name for t in state.tools],
            })
            seen.add(name)
        for name, cfg in configs.items():   # configured but not yet started
            if name not in seen:
                out.append({"name": name,
                            "command": " ".join([cfg.command] + cfg.args),
                            "scope": cfg.scope, "enabled": cfg.enabled,
                            "status": "disabled" if not cfg.enabled else "stopped",
                            "error": "", "tools": []})
        return sorted(out, key=lambda s: s["name"])

    def add_server(self, name: str, command_line: str,
                   env: Optional[Dict[str, str]] = None, scope: str = "global") -> None:
        if not _has_active_subscription():
            raise PermissionError(_SUBSCRIPTION_MSG)
        import shlex
        parts = shlex.split(command_line, posix=False)
        if not parts:
            raise ValueError("Empty command")
        # shlex posix=False keeps quotes — strip them from each part
        parts = [p.strip('"') for p in parts]
        configs = self.load_configs()
        name = _sanitize(name)
        configs[name] = McpServerConfig(
            name=name, command=parts[0], args=parts[1:],
            env=env or {}, enabled=True, scope=scope,
        )
        self._write_scope(scope, configs)
        self.reconnect(name)

    def remove_server(self, name: str) -> None:
        configs = self.load_configs()
        cfg = configs.pop(name, None)
        if cfg is None:
            return
        state = self._states.pop(name, None)
        if state and self._loop and state.stop_event is not None:
            self._loop.call_soon_threadsafe(state.stop_event.set)
        self._write_scope(cfg.scope, configs)

    def set_enabled(self, name: str, enabled: bool) -> None:
        configs = self.load_configs()
        cfg = configs.get(name)
        if cfg is None:
            return
        cfg.enabled = enabled
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
        for raw_name, d in servers.items():
            if not isinstance(d, dict) or not d.get("command"):
                continue
            name = _sanitize(raw_name)
            configs[name] = McpServerConfig.from_dict(name, d, scope)
            added += 1
        if not added:
            raise ValueError("No valid servers in JSON (each needs a 'command')")
        self._write_scope(scope, configs)
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
