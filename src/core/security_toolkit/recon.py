"""
recon.py -- non-destructive reconnaissance against an in-scope target.

Everything here is a read: resolve a name, open a TCP connection, read a
banner, fetch a certificate, request a page. No payloads are sent that
attempt to change state on the target, which is what separates this module
from web_probe.py.

Every entry point calls ScopeGuard.require() before it touches the network,
so an out-of-scope host fails before a socket exists. Port scanning is
threaded but capped, and infrastructure ports (SMB/RDP/VNC/telnet) are
refused outright: hitting those across a LAN is indistinguishable from a
worm, and they are never part of a web app assessment.
"""

from __future__ import annotations

import concurrent.futures
import ipaddress
import socket
import ssl
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.core.security_toolkit.finding import (
    Confidence,
    Finding,
    ScanReport,
    Severity,
    truncate,
)
from src.core.security_toolkit.scope import ScopeGuard, get_scope_guard

try:
    from src.utils.logger import get_logger
except ImportError:
    import logging

    def get_logger(name: str):
        return logging.getLogger(name)

log = get_logger("security_toolkit.recon")

USER_AGENT = "Cortex-SecurityToolkit/1.0 (authorized testing)"

# Ports worth checking on a modern web/API host, with the service we expect.
COMMON_PORTS: Dict[int, str] = {
    21: "ftp", 22: "ssh", 25: "smtp", 53: "dns", 80: "http", 110: "pop3",
    143: "imap", 443: "https", 465: "smtps", 587: "smtp-submission",
    993: "imaps", 995: "pop3s", 1433: "mssql", 1521: "oracle", 2049: "nfs",
    3000: "dev-server", 3306: "mysql", 3389: "rdp", 5000: "dev-server",
    5432: "postgres", 5601: "kibana", 6379: "redis", 8000: "http-alt",
    8080: "http-alt", 8443: "https-alt", 8888: "http-alt", 9000: "http-alt",
    9200: "elasticsearch", 27017: "mongodb",
}

MAX_SCAN_PORTS = 1024
DEFAULT_TIMEOUT = 2.0
DEFAULT_WORKERS = 64


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------
# Port scanning
# --------------------------------------------------------------------------


def parse_ports(spec: Any) -> List[int]:
    """Accept [80, 443], "80,443", "1-1024" or "common"."""
    if spec is None or spec == "" or spec == "common":
        return sorted(COMMON_PORTS)
    if isinstance(spec, int):
        return [spec]
    if isinstance(spec, (list, tuple, set)):
        out: List[int] = []
        for item in spec:
            out.extend(parse_ports(item))
        return sorted({p for p in out if 1 <= p <= 65535})
    text = str(spec).strip()
    if text == "common":
        return sorted(COMMON_PORTS)
    if text == "all":
        return list(range(1, 65536))
    ports: List[int] = []
    for chunk in text.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            lo, _, hi = chunk.partition("-")
            try:
                ports.extend(range(int(lo), int(hi) + 1))
            except ValueError:
                continue
        else:
            try:
                ports.append(int(chunk))
            except ValueError:
                continue
    return sorted({p for p in ports if 1 <= p <= 65535})


def _probe_port(host: str, port: int, timeout: float) -> Optional[Dict[str, Any]]:
    """TCP connect scan plus a best-effort banner read."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            if sock.connect_ex((host, port)) != 0:
                return None
            banner = ""
            # Only passive services volunteer a banner; sending data to an
            # unknown service is an active probe, so keep quiet by default.
            if port in (21, 22, 25, 110, 143, 587, 993, 995):
                try:
                    sock.settimeout(min(timeout, 2.0))
                    banner = sock.recv(512).decode("utf-8", errors="replace").strip()
                except Exception:
                    banner = ""
            return {
                "port": port,
                "service": COMMON_PORTS.get(port, "unknown"),
                "banner": truncate(banner, 200),
            }
    except Exception:
        return None


def scan_ports(
    host: str,
    ports: Any = None,
    timeout: float = DEFAULT_TIMEOUT,
    workers: int = DEFAULT_WORKERS,
    project_root: Optional[str] = None,
    guard: Optional[ScopeGuard] = None,
) -> ScanReport:
    """TCP connect scan. Requires `host` to be in scope."""
    guard = guard or get_scope_guard(project_root)
    target = guard.require(host, purpose="port scan")

    report = ScanReport(tool="recon.scan_ports", target=target)
    port_list = parse_ports(ports)

    if len(port_list) > MAX_SCAN_PORTS:
        report.notes.append(
            f"port list capped at {MAX_SCAN_PORTS} (requested {len(port_list)})"
        )
        port_list = port_list[:MAX_SCAN_PORTS]

    for p in port_list:
        guard.require_port(p)
    port_list = [p for p in port_list if p not in _never_scan()]

    # Resolve once, so a DNS failure reports clearly instead of 1000 timeouts.
    try:
        ip = socket.gethostbyname(target)
    except socket.gaierror as exc:
        report.errors.append(f"DNS resolution failed for {target}: {exc}")
        return report

    report.notes.append(f"resolved {target} -> {ip}")
    report.notes.append(f"scanned {len(port_list)} port(s)")

    started = time.time()
    open_ports: List[Dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(workers, len(port_list) or 1)) as pool:
        futures = [pool.submit(_probe_port, ip, p, timeout) for p in port_list]
        for fut in concurrent.futures.as_completed(futures):
            hit = fut.result()
            if hit:
                open_ports.append(hit)

    open_ports.sort(key=lambda r: r["port"])
    for hit in open_ports:
        report.scanned.append(f"{target}:{hit['port']} ({hit['service']})")

    report.notes.append(f"completed in {time.time() - started:.1f}s")

    _findings_for_open_ports(report, target, open_ports)
    return report


def _never_scan() -> set:
    return {139, 445, 3389, 5900, 23, 135, 137, 138}


def _findings_for_open_ports(
    report: ScanReport, target: str, open_ports: List[Dict[str, Any]]
) -> None:
    exposed = {p["port"] for p in open_ports}

    if not open_ports:
        report.add(Finding(
            title="No open TCP ports found",
            severity=Severity.INFO,
            category="recon",
            target=target,
            description="None of the scanned ports accepted a connection.",
        ))
        return

    report.add(Finding(
        title=f"{len(open_ports)} open TCP port(s)",
        severity=Severity.INFO,
        category="recon",
        target=target,
        description="Ports accepting connections: "
                    + ", ".join(f"{p['port']}/{p['service']}" for p in open_ports),
    ))

    # Database and cache services reachable from off-host are the highest
    # value mistake in a typical deployment: they frequently ship with no
    # authentication at all.
    for port, name, severity in (
        (6379, "Redis", Severity.CRITICAL),
        (27017, "MongoDB", Severity.CRITICAL),
        (9200, "Elasticsearch", Severity.CRITICAL),
        (3306, "MySQL", Severity.HIGH),
        (5432, "PostgreSQL", Severity.HIGH),
        (1433, "MSSQL", Severity.HIGH),
        (1521, "Oracle", Severity.HIGH),
        (5601, "Kibana", Severity.HIGH),
    ):
        if port in exposed:
            report.add(Finding(
                title=f"{name} exposed on port {port}",
                severity=severity,
                category="exposed-service",
                target=f"{target}:{port}",
                description=(
                    f"{name} is reachable over the network. Data stores are meant to "
                    f"be reached by the application, not the network."
                ),
                evidence=f"TCP connect to {target}:{port} succeeded",
                remediation=(
                    f"Bind {name} to 127.0.0.1, or restrict port {port} with a "
                    f"firewall/security group to known application hosts."
                ),
                references=["https://owasp.org/Top10/A05_2021-Security_Misconfiguration/"],
            ))

    # Unauthenticated Redis is a remote code execution primitive via CONFIG SET.
    if 6379 in exposed:
        report.add(Finding(
            title="Redis without authentication may allow arbitrary file write",
            severity=Severity.CRITICAL,
            category="exposed-service",
            target=f"{target}:6379",
            description=(
                "Redis has no authentication by default. An attacker who can reach it "
                "can write files as the Redis user, which is commonly escalated to "
                "remote code execution."
            ),
            remediation="Set requirepass, enable protected-mode, bind to loopback.",
            confidence=Confidence.TENTATIVE,
        ))

    for port in (8000, 8080, 3000, 5000, 9000):
        if port in exposed:
            report.add(Finding(
                title=f"Development server exposed on port {port}",
                severity=Severity.MEDIUM,
                category="exposed-service",
                target=f"{target}:{port}",
                description=(
                    "This port is typically a development server. Those usually run "
                    "with debug mode on, show stack traces, and disable host checks."
                ),
                remediation="Do not expose dev servers; front them with a production WSGI/ASGI server.",
            ))
            break


# --------------------------------------------------------------------------
# DNS
# --------------------------------------------------------------------------


def resolve_dns(host: str, project_root: Optional[str] = None) -> ScanReport:
    guard = get_scope_guard(project_root)
    target = guard.require(host, purpose="DNS resolution")

    report = ScanReport(tool="recon.resolve_dns", target=target)
    try:
        infos = socket.getaddrinfo(target, None)
    except socket.gaierror as exc:
        report.errors.append(f"resolution failed: {exc}")
        return report

    seen: List[str] = []
    for info in infos:
        addr = info[4][0]
        if addr not in seen:
            seen.append(addr)

    report.scanned.extend(seen)
    report.notes.append(f"{len(seen)} address(es): {', '.join(seen)}")

    for addr in seen:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if ip.is_private and not ip.is_loopback:
            report.add(Finding(
                title=f"{target} resolves to a private address ({addr})",
                severity=Severity.INFO,
                category="recon",
                target=target,
                description="Internal addressing visible from this position.",
            ))
    return report


# --------------------------------------------------------------------------
# TLS
# --------------------------------------------------------------------------


def inspect_tls(
    host: str,
    port: int = 443,
    timeout: float = 5.0,
    project_root: Optional[str] = None,
) -> ScanReport:
    guard = get_scope_guard(project_root)
    target = guard.require(host, purpose="TLS inspection")
    guard.require_port(port)

    report = ScanReport(tool="recon.inspect_tls", target=f"{target}:{port}")

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        with socket.create_connection((target, port), timeout=timeout) as raw:
            with ctx.wrap_socket(raw, server_hostname=target) as tls:
                cert = tls.getpeercert()
                version = tls.version()
                cipher = tls.cipher()
    except Exception as exc:
        report.errors.append(f"TLS handshake failed: {exc}")
        return report

    report.notes.append(f"protocol {version}")
    if cipher:
        report.notes.append(f"cipher {cipher[0]}")

    if version in ("TLSv1", "TLSv1.1", "SSLv3", "SSLv2"):
        report.add(Finding(
            title=f"Obsolete TLS protocol in use ({version})",
            severity=Severity.HIGH,
            category="tls",
            target=f"{target}:{port}",
            description=f"The endpoint negotiated {version}, which is deprecated and broken.",
            evidence=f"negotiated {version} with {cipher[0] if cipher else 'unknown cipher'}",
            remediation="Disable TLS 1.0/1.1; require TLS 1.2 or newer.",
            references=["https://cwe.mitre.org/data/definitions/326.html"],
        ))

    subject = _name_from_cert(cert, "subject")
    issuer = _name_from_cert(cert, "issuer")
    if subject:
        report.notes.append(f"subject {subject}")
    if issuer:
        report.notes.append(f"issuer {issuer}")

    not_after = cert.get("notAfter") if cert else None
    if not_after:
        try:
            expiry = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(
                tzinfo=timezone.utc
            )
            days = (expiry - datetime.now(timezone.utc)).days
            report.notes.append(f"expires {not_after} ({days} days)")
            if days < 0:
                report.add(Finding(
                    title="TLS certificate has expired",
                    severity=Severity.CRITICAL,
                    category="tls",
                    target=f"{target}:{port}",
                    description="Clients will refuse the connection or show a warning.",
                    evidence=f"notAfter={not_after}",
                    remediation="Renew the certificate and automate renewal.",
                ))
            elif days < 21:
                report.add(Finding(
                    title=f"TLS certificate expires in {days} day(s)",
                    severity=Severity.MEDIUM,
                    category="tls",
                    target=f"{target}:{port}",
                    description="Imminent expiry.",
                    evidence=f"notAfter={not_after}",
                    remediation="Renew now; add automated renewal with alerting.",
                ))
        except Exception:
            pass
    else:
        report.add(Finding(
            title="TLS certificate could not be validated",
            severity=Severity.MEDIUM,
            category="tls",
            target=f"{target}:{port}",
            description=(
                "The certificate was retrieved but not verified. Either it is "
                "self-signed/invalid, or verification was skipped."
            ),
            remediation="Install a certificate from a trusted CA and serve the full chain.",
            confidence=Confidence.TENTATIVE,
        ))

    return report


def _name_from_cert(cert: Optional[Dict[str, Any]], field: str) -> str:
    if not cert:
        return ""
    parts = []
    for rdn in cert.get(field, ()):
        for key, value in rdn:
            parts.append(f"{key}={value}")
    return ", ".join(parts)


# --------------------------------------------------------------------------
# HTTP security headers
# --------------------------------------------------------------------------


SECURITY_HEADERS: List[Tuple[str, Severity, str, str]] = [
    (
        "strict-transport-security",
        Severity.MEDIUM,
        "HSTS missing",
        "Without Strict-Transport-Security a downgrade to plain HTTP is possible "
        "and is exploitable on hostile networks.",
    ),
    (
        "content-security-policy",
        Severity.MEDIUM,
        "Content-Security-Policy missing",
        "CSP is the main defence-in-depth control against cross-site scripting. "
        "Its absence means any XSS is fully exploitable.",
    ),
    (
        "x-content-type-options",
        Severity.LOW,
        "X-Content-Type-Options missing",
        "Without nosniff, browsers may reinterpret a response as a different "
        "content type, which can turn an upload into script execution.",
    ),
    (
        "x-frame-options",
        Severity.LOW,
        "Clickjacking protection missing",
        "No X-Frame-Options and no CSP frame-ancestors directive means the page "
        "can be framed and used for clickjacking.",
    ),
    (
        "referrer-policy",
        Severity.LOW,
        "Referrer-Policy missing",
        "URLs (which frequently contain tokens and IDs) leak to third parties "
        "through the Referer header.",
    ),
    (
        "permissions-policy",
        Severity.LOW,
        "Permissions-Policy missing",
        "No restrictions on powerful browser features such as camera, "
        "microphone and geolocation.",
    ),
]


def analyze_http_headers(
    url: str, timeout: float = 10.0, project_root: Optional[str] = None
) -> ScanReport:
    guard = get_scope_guard(project_root)
    target = guard.require(url, purpose="header analysis")

    report = ScanReport(tool="recon.analyze_http_headers", target=url)

    status, headers, body = _http_get(url, timeout)
    if status is None:
        report.errors.append(f"request failed for {url}")
        return report

    report.notes.append(f"HTTP {status}")
    report.scanned.append(url)

    lowered = {k.lower(): v for k, v in headers.items()}

    for header, severity, title, why in SECURITY_HEADERS:
        if header == "x-frame-options" and (
            "x-frame-options" in lowered or "frame-ancestors" in lowered.get("content-security-policy", "")
        ):
            continue
        if header not in lowered:
            report.add(Finding(
                title=title,
                severity=severity,
                category="http-headers",
                target=url,
                description=why,
                evidence=f"response headers did not include {header}",
                remediation=f"Add the {header} header at the web server or application layer.",
                references=["https://owasp.org/www-project-secure-headers/"],
            ))

    server = lowered.get("server", "")
    powered = lowered.get("x-powered-by", "")
    if server or powered:
        report.add(Finding(
            title="Technology versions disclosed in response headers",
            severity=Severity.LOW,
            category="information-disclosure",
            target=url,
            description="Version banners let an attacker skip straight to known CVEs for that build.",
            evidence=f"Server: {server or '-'} | X-Powered-By: {powered or '-'}",
            remediation="Remove or genericise Server and X-Powered-By.",
        ))

    acao = lowered.get("access-control-allow-origin", "")
    if acao == "*":
        report.add(Finding(
            title="CORS allows any origin",
            severity=Severity.MEDIUM,
            category="cors",
            target=url,
            description=(
                "Access-Control-Allow-Origin: * lets any site read responses. If the "
                "endpoint is authenticated via cookies or returns private data, this "
                "is a data-exfiltration path. A wildcard cannot be combined with "
                "credentials, which is why this is medium rather than high."
            ),
            evidence=f"Access-Control-Allow-Origin: {acao}",
            remediation="Reflect only an allowlist of known origins.",
        ))
    elif acao and "null" in acao.lower():
        report.add(Finding(
            title="CORS reflects a null origin",
            severity=Severity.HIGH,
            category="cors",
            target=url,
            description="A null origin can be produced by a sandboxed iframe, so this is exploitable.",
            evidence=f"Access-Control-Allow-Origin: {acao}",
            remediation="Never trust the literal null origin.",
        ))

    if lowered.get("access-control-allow-credentials", "").lower() == "true" and acao not in ("", "*"):
        report.add(Finding(
            title="CORS allows credentials from a reflected origin",
            severity=Severity.HIGH,
            category="cors",
            target=url,
            description="Credentials plus a reflected origin lets a hostile site make authenticated requests.",
            evidence=f"ACAO={acao}, ACAC=true",
            remediation="Pin the allowlist and validate the Origin header server-side.",
        ))

    if "set-cookie" in lowered:
        report.extend(_cookie_findings(url, lowered))

    if not url.startswith("https://") and status is not None:
        report.add(Finding(
            title="Plain HTTP endpoint",
            severity=Severity.HIGH,
            category="transport",
            target=url,
            description="Traffic is unencrypted: credentials and session cookies are readable in transit.",
            evidence=f"requested {url}",
            remediation="Serve over HTTPS and redirect HTTP to it.",
        ))

    return report


def _cookie_findings(url: str, headers: Dict[str, str]) -> List[Finding]:
    out: List[Finding] = []
    raw = headers.get("set-cookie", "")
    lower = raw.lower()
    if "httponly" not in lower:
        out.append(Finding(
            title="Session cookie without HttpOnly",
            severity=Severity.MEDIUM,
            category="cookies",
            target=url,
            description="JavaScript can read the cookie, so any XSS becomes full session theft.",
            evidence=truncate(raw, 200),
            remediation="Add HttpOnly to every session cookie.",
        ))
    if "secure" not in lower:
        out.append(Finding(
            title="Session cookie without Secure",
            severity=Severity.MEDIUM,
            category="cookies",
            target=url,
            description="The cookie is sent over plain HTTP and can be intercepted.",
            evidence=truncate(raw, 200),
            remediation="Add Secure to every session cookie.",
        ))
    if "samesite" not in lower:
        out.append(Finding(
            title="Session cookie without SameSite",
            severity=Severity.LOW,
            category="cookies",
            target=url,
            description="Omitting SameSite leaves cross-site request forgery mitigations to the browser default.",
            evidence=truncate(raw, 200),
            remediation="Set SameSite=Lax (or Strict) unless cross-site use is required.",
        ))
    return out


def _http_get(url: str, timeout: float = 10.0) -> Tuple[Optional[int], Dict[str, str], str]:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(65536).decode("utf-8", errors="replace")
            return resp.status, dict(resp.headers), body
    except urllib.error.HTTPError as exc:
        # An error page is still a response, and its headers are still useful.
        body = ""
        try:
            body = exc.read(65536).decode("utf-8", errors="replace")
        except Exception:
            pass
        return exc.code, dict(exc.headers or {}), body
    except Exception as exc:
        log.info(f"[RECON] GET {url} failed: {exc}")
        return None, {}, ""
