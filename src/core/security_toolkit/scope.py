"""
scope.py -- the authorization gate for every active security action.

Cortex ships tools that open sockets against a target and send probe payloads.
That is a real capability, so it gets a real control: nothing runs against a
host the owner has not explicitly declared. This module is the single
chokepoint. Every network-touching function in this package calls
ScopeGuard.check() before it resolves a name or opens a connection.

The gate lives in CODE, not in a prompt. A model that has been talked into
"just scan this one extra IP" by a poisoned file still hits this function and
still gets a ScopeViolation, because the refusal is an exception raised before
any syscall, not a sentence in a system prompt.

Scope file -- .cortex/security/scope.json (project) or ~/.cortex/security/scope.json:

    {
      "authorized": true,
      "note": "own lab VM, Juice Shop on 3000",
      "expires": "2026-12-31",
      "targets": ["127.0.0.1", "10.0.2.15", "juice-shop.local", "*.lab.internal"]
    }

Defaults, and the reasoning for each:

  loopback (127.0.0.0/8, ::1, localhost)  allowed with no declaration. It is
      this machine, which is unambiguously the owner's.
  private LAN ranges (10/8, 172.16/12, 192.168/16)  DENIED by default. They
      are usually the owner's own network, but they are also the office, the
      university and the coffee shop. Declaring them is one line.
  link-local / cloud metadata (169.254.0.0/16, fd00:ec2::254,
      metadata.google.internal)  DENIED unconditionally. These hand out cloud
      IAM credentials. There is no legitimate reason for a desktop IDE to
      probe them, so no scope declaration can re-enable them.
  everything else  DENIED until declared.
"""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import time
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from src.utils.logger import get_logger
except ImportError:  # standalone import (tests, tooling)
    import logging

    def get_logger(name: str):
        return logging.getLogger(name)

log = get_logger("security_toolkit.scope")


class ScopeViolation(Exception):
    """Raised when an action targets something outside the declared scope."""

    def __init__(self, message: str, target: str = "", reason: str = ""):
        super().__init__(message)
        self.target = target
        self.reason = reason


# Ranges that are never a legitimate target, regardless of scope file.
_ALWAYS_BLOCKED_NETS = [
    ipaddress.ip_network("169.254.0.0/16"),    # link-local, incl. AWS/GCP metadata
    ipaddress.ip_network("fd00:ec2::254/128"),  # AWS IMDSv6
    ipaddress.ip_network("0.0.0.0/32"),
    ipaddress.ip_network("255.255.255.255/32"),
]

_ALWAYS_BLOCKED_HOSTS = {
    "metadata.google.internal",
    "metadata",
    "instance-data",
}

_LOOPBACK_NETS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
]

_LOOPBACK_NAMES = {"localhost", "localhost.localdomain", "ip6-localhost"}


def _is_loopback(ip: "ipaddress._BaseAddress") -> bool:
    """True when an address is this machine, by v4 or v6 rule."""
    return any(ip in net for net in _LOOPBACK_NETS)


def _resolve_all(host: str) -> List[str]:
    """Every address a name actually maps to, right now.

    A hostname is not an address. Policy that authorises on the name alone
    cannot see where the name points, so any IP-range rule is invisible to a
    declared name. This is what lets the caller re-apply the address rules.

    Best-effort by design: a name that does not resolve yields an empty list,
    which callers treat as "nothing to check" rather than "blocked". A target
    that cannot resolve cannot be connected to anyway, and failing closed here
    would refuse declared lab hosts that are simply not up yet.
    """
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, OSError, UnicodeError):
        return []
    return sorted({info[4][0] for info in infos if info[4]})

# Ports that are infrastructure rather than application surface. A desktop
# IDE has no business scanning another box's SSH for practice, and hammering
# 135/445/3389 on a LAN is indistinguishable from a worm.
_INFRASTRUCTURE_PORTS = {
    135, 137, 138, 139, 445,     # Windows NetBIOS/SMB
    3389,                         # RDP
    5900,                         # VNC
    23,                           # telnet
}


@dataclass
class ScopeEntry:
    raw: str
    note: str = ""


@dataclass
class ScopeDecision:
    allowed: bool
    target: str
    reason: str = ""
    matched_rule: str = ""
    # Every address the target actually resolves to at check time. A name is
    # not an address, so the decision records both: the string that was
    # declared and the addresses it maps to right now. Callers that want to
    # close the resolve-then-connect window can connect to a pinned entry
    # from this list instead of resolving again.
    resolved: List[str] = field(default_factory=list)


@dataclass
class ScopePolicy:
    """Parsed scope file."""

    authorized: bool = False
    note: str = ""
    expires: Optional[str] = None
    targets: List[str] = field(default_factory=list)
    max_ports_per_scan: int = 1024
    max_requests_per_minute: int = 120
    source_path: str = ""

    def is_expired(self) -> bool:
        if not self.expires:
            return False
        # ISO date or datetime; compare on the leading date part only.
        try:
            stamp = self.expires.replace("Z", "").split("T")[0]
            t = time.strptime(stamp, "%Y-%m-%d")
            return time.mktime(t) < time.time()
        except Exception:
            # Unparseable expiry is treated as expired: fail closed.
            return True


class ScopeGuard:
    """Loads the scope policy and answers 'may I touch this?'."""

    def __init__(
        self,
        project_root: Optional[str] = None,
        policy: Optional[ScopePolicy] = None,
    ):
        self._project_root = project_root
        # An injected policy bypasses file loading entirely. Tests use this so
        # they never depend on the developer's real ~/.cortex scope file.
        self._policy: Optional[ScopePolicy] = policy

    # ---------------------------------------------------------------- loading

    def policy_paths(self) -> List[Path]:
        paths: List[Path] = []
        # An explicit path wins over the defaults. Useful for keeping tests
        # hermetic, and for pointing at a scope file kept outside the project
        # (a per-engagement file in a pentest repo, for example).
        override = os.environ.get("CORTEX_SCOPE_FILE")
        if override:
            paths.append(Path(override))
        if self._project_root:
            paths.append(Path(self._project_root) / ".cortex" / "security" / "scope.json")
        env_home = os.environ.get("CORTEX_HOME")
        home = Path(env_home) if env_home else Path.home()
        paths.append(home / ".cortex" / "security" / "scope.json")
        return paths

    def load(self, force: bool = False) -> ScopePolicy:
        if self._policy is not None and not force:
            return self._policy
        for path in self.policy_paths():
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                log.warning(f"[SCOPE] unreadable scope file {path}: {exc}")
                continue
            self._policy = ScopePolicy(
                authorized=bool(data.get("authorized", False)),
                note=str(data.get("note", "")),
                expires=data.get("expires"),
                targets=[str(t).strip() for t in data.get("targets", []) if str(t).strip()],
                max_ports_per_scan=int(data.get("max_ports_per_scan", 1024)),
                max_requests_per_minute=int(data.get("max_requests_per_minute", 120)),
                source_path=str(path),
            )
            log.info(
                f"[SCOPE] loaded {path}: authorized={self._policy.authorized} "
                f"targets={len(self._policy.targets)}"
            )
            return self._policy
        self._policy = ScopePolicy()
        return self._policy

    def status(self) -> Dict[str, Any]:
        p = self.load()
        return {
            "authorized": p.authorized,
            "note": p.note,
            "expires": p.expires,
            "expired": p.is_expired(),
            "targets": list(p.targets),
            "source": p.source_path or "(no scope file found)",
            "default_allowed": ["127.0.0.0/8", "::1", "localhost"],
            "always_blocked": ["169.254.0.0/16", "cloud metadata hostnames"],
        }

    # ------------------------------------------------------------- the check

    def check(self, target: str, purpose: str = "security scan") -> ScopeDecision:
        """Decide whether `target` is in scope.

        Never raises; callers that must stop use `require`. Returning a
        decision lets a UI show a blocked row instead of a stack trace.
        """
        if not target or not str(target).strip():
            return ScopeDecision(False, target, "empty target")

        host = normalize_target(target)
        policy = self.load()

        # 1. Unconditional blocks. Checked first so nothing can override them.
        blocked = self._always_blocked_reason(host)
        if blocked:
            self._audit(host, False, blocked, purpose)
            return ScopeDecision(False, host, blocked, "always-blocked")

        # 2. Loopback is always permitted: it is this machine.
        if host in _LOOPBACK_NAMES:
            return ScopeDecision(True, host, "loopback", "default-loopback")
        try:
            ip = ipaddress.ip_address(host)
            for net in _LOOPBACK_NETS:
                if ip in net:
                    return ScopeDecision(True, host, "loopback", "default-loopback")
        except ValueError:
            pass  # a hostname, not a literal IP

        # 3. A declared target that has not expired. A match on the NAME is
        #    not the same as authorisation to reach the ADDRESS, so resolve
        #    and re-apply the address rules before allowing it.
        if policy.authorized:
            if policy.is_expired():
                reason = f"scope declaration expired ({policy.expires})"
                self._audit(host, False, reason, purpose)
                return ScopeDecision(False, host, reason, "expired")
            for entry in policy.targets:
                if self._matches(host, entry):
                    return self._verify_resolution(host, entry, policy, purpose)

        reason = (
            "not in the declared scope (see .cortex/security/scope.json). "
            "Loopback is allowed by default; everything else must be listed."
        )
        self._audit(host, False, reason, purpose)
        return ScopeDecision(False, host, reason, "not-declared")

    def _verify_resolution(
        self, host: str, entry: str, policy: ScopePolicy, purpose: str
    ) -> ScopeDecision:
        """Re-apply the address rules to the addresses a declared name maps to.

        Without this, the always-blocked rule is a string comparison: any
        declaration whose name resolves into a blocked range reaches the
        address the module promises is untouchable. Resolution is best-effort,
        so a name that does not resolve is allowed on its declaration; only an
        address we can actually see can fail this check.
        """
        resolved = _resolve_all(host)
        for addr in resolved:
            # Metadata / link-local / reserved: never reachable, whatever the
            # scope file says. This is the rule that must not be bypassable.
            blocked = self._always_blocked_reason(addr)
            if blocked:
                reason = (
                    f"{host} resolves to {addr}, which is blocked unconditionally. "
                    f"No declaration can re-enable it."
                )
                self._audit(host, False, reason, purpose)
                return ScopeDecision(False, host, reason, "resolved-to-blocked", resolved)

            try:
                ip = ipaddress.ip_address(addr)
            except ValueError:
                continue
            if _is_loopback(ip) or self._ip_declared(ip, policy):
                continue
            # Private ranges are denied by default, and a name is not a
            # declaration for the range it happens to point at today. DNS can
            # change after the declaration was written, so the address itself
            # must be covered.
            if ip.is_private:
                reason = (
                    f"{host} resolves to {addr}, a private address that is not "
                    f"itself declared. Add the address or its range to the scope "
                    f"file if this is intended."
                )
                self._audit(host, False, reason, purpose)
                return ScopeDecision(False, host, reason, "resolved-private", resolved)

        return ScopeDecision(True, host, f"declared: {entry}", "declared", resolved)

    def require(self, target: str, purpose: str = "security scan") -> str:
        """check() but raises ScopeViolation. Returns the normalized host."""
        decision = self.check(target, purpose)
        if not decision.allowed:
            raise ScopeViolation(
                f"Blocked by scope policy: {decision.target} -- {decision.reason}",
                target=decision.target,
                reason=decision.reason,
            )
        return decision.target

    def require_port(self, port: int) -> None:
        if port in _INFRASTRUCTURE_PORTS:
            raise ScopeViolation(
                f"Port {port} is infrastructure (SMB/RDP/VNC/telnet) and is out of "
                f"scope for this toolkit.",
                reason="infrastructure-port",
            )

    # --------------------------------------------------------------- helpers

    def _always_blocked_reason(self, host: str) -> str:
        if host in _ALWAYS_BLOCKED_HOSTS:
            return (
                f"{host} is a cloud metadata endpoint. It hands out infrastructure "
                f"credentials and is never a valid target."
            )
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            return ""
        for net in _ALWAYS_BLOCKED_NETS:
            if ip in net:
                return (
                    f"{ip} is in {net}, a link-local/metadata range that is never a "
                    f"valid target."
                )
        return ""

    def _matches(self, host: str, entry: str) -> bool:
        raw = str(entry).strip().lower()
        # CIDR must be handled BEFORE normalize_target. That function strips
        # everything after the first slash to drop a URL path, which would
        # turn "10.0.2.0/24" into "10.0.2.0" and make the declaration match
        # nothing at all.
        if "/" in raw:
            _head, _, prefix = raw.partition("/")
            if prefix.isdigit():
                try:
                    return ipaddress.ip_address(host) in ipaddress.ip_network(
                        raw, strict=False
                    )
                except ValueError:
                    return False
        entry = normalize_target(entry)
        if host == entry:
            return True
        # wildcard domain, e.g. *.lab.internal
        if "*" in entry:
            return fnmatch(host, entry)
        # a bare domain covers its own subdomains
        return host.endswith("." + entry)

    def _ip_declared(self, ip: "ipaddress._BaseAddress", policy: ScopePolicy) -> bool:
        """True when a declared entry covers this address.

        Only address-shaped declarations count here. A name entry such as
        "juice-shop.local" deliberately does not, because it says nothing
        about which address the name will point at on the next lookup.
        """
        for entry in policy.targets:
            raw = str(entry).strip().lower()
            if "/" in raw:
                _head, _, prefix = raw.partition("/")
                if prefix.isdigit():
                    try:
                        if ip in ipaddress.ip_network(raw, strict=False):
                            return True
                    except ValueError:
                        continue
            try:
                if ip == ipaddress.ip_address(raw):
                    return True
            except ValueError:
                continue  # a name, not an address
        return False

    def _audit(self, host: str, allowed: bool, reason: str, purpose: str) -> None:
        """Record refusals in the security audit trail.

        Only denials are recorded. A denial is a security-relevant access
        control event and maps onto the existing PERMISSION_DENIED type, so
        adding scope events needs no change to the audit enum. Allowed
        loopback traffic is not an event worth a tamper-evident log line.
        """
        log.info(f"[SCOPE] {'ALLOW' if allowed else 'DENY'} {host} ({reason})")
        if allowed:
            return
        try:
            from src.core.security_audit import SecurityEvent, log_security_event

            log_security_event(
                SecurityEvent.PERMISSION_DENIED,
                {"target": host, "reason": reason, "purpose": purpose},
            )
        except Exception:
            # Auditing must never be the thing that raises instead of the
            # refusal itself. The log line above is the fallback record.
            pass


def normalize_target(target: str) -> str:
    """Turn a URL, host:port or bare host into a lowercase bare host."""
    t = str(target).strip().lower()
    for scheme in ("http://", "https://", "ftp://", "//"):
        if t.startswith(scheme):
            t = t[len(scheme):]
            break
    t = t.split("/", 1)[0]
    t = t.split("@")[-1]           # user:pass@host
    if t.startswith("[") and "]" in t:   # [::1]:8080
        return t[1:t.index("]")]
    if t.count(":") == 1:
        host, _, maybe_port = t.partition(":")
        if maybe_port.isdigit():
            return host
    return t


_GUARD: Optional[ScopeGuard] = None


def get_scope_guard(project_root: Optional[str] = None) -> ScopeGuard:
    global _GUARD
    if _GUARD is None or (project_root and _GUARD._project_root != project_root):
        _GUARD = ScopeGuard(project_root)
    return _GUARD


def reset_scope_guard() -> None:
    global _GUARD
    _GUARD = None