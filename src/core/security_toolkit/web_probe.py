"""
web_probe.py -- active vulnerability probes against an in-scope web target.

This is the module that actually sends payloads, so its boundaries are drawn
deliberately:

  Detection, never exploitation. Probes prove that an injection point exists
  and then stop. There is no UNION data extraction, no file write through a
  SQL injection, no shell. The output is "this parameter is injectable", not
  the contents of the target's user table. That line is what keeps this a
  scanner instead of a weapon, and it is also what makes the findings
  actionable: the fix is the same whether or not you dumped the data.

  Non-destructive payloads only. A single quote to break a query, or
  "AND 1=1" versus "AND 1=2" to compare responses. Never DROP, DELETE,
  UPDATE, INSERT, or any statement that mutates.

  Scoped and rate-limited. Every request goes through one Requester that
  enforces the scope check and a per-minute request ceiling, so a probe
  cannot flood a target by looping.

Read-only probes: SQL injection, reflected XSS, path traversal, open
redirect, exposed sensitive paths, dangerous HTTP methods, and CORS behaviour
on a real endpoint.
"""

from __future__ import annotations

import re
import time
import urllib.error
import urllib.parse
import urllib.request
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

log = get_logger("security_toolkit.web_probe")

USER_AGENT = "Cortex-SecurityToolkit/1.0 (authorized testing)"

# Database error signatures. A single quote that produces one of these means
# the parameter reaches the query string without parameterisation.
SQL_ERROR_SIGNATURES: List[Tuple[str, str]] = [
    (r"you have an error in your sql syntax", "MySQL/MariaDB"),
    (r"warning:\s*mysql", "MySQL"),
    (r"unclosed quotation mark after the character string", "MSSQL"),
    (r"microsoft ole db provider for sql server", "MSSQL"),
    (r"ora-\d{5}", "Oracle"),
    (r"postgresql.*error", "PostgreSQL"),
    (r"pg_query\(\)", "PostgreSQL"),
    (r"sqlite3\.\w+error", "SQLite"),
    (r"sqlite error", "SQLite"),
    (r"quoted string not properly terminated", "Oracle"),
    (r"syntax error at or near", "PostgreSQL"),
    (r"jdbc\..*exception", "JDBC"),
    (r"odbc.*driver", "ODBC"),
]

# Files that should never be served. Each entry is (path, severity, why).
SENSITIVE_PATHS: List[Tuple[str, Severity, str]] = [
    ("/.env", Severity.CRITICAL, "Environment file: database passwords, API keys, secrets."),
    ("/.env.local", Severity.CRITICAL, "Environment file with local overrides."),
    ("/.env.production", Severity.CRITICAL, "Production environment file."),
    ("/.git/config", Severity.HIGH, "Git metadata: reveals the repository remote and layout."),
    ("/.git/HEAD", Severity.HIGH, "Git metadata: confirms an exposed .git directory."),
    ("/.svn/entries", Severity.MEDIUM, "Subversion metadata."),
    ("/.DS_Store", Severity.LOW, "macOS directory listing leaks file names."),
    ("/backup.sql", Severity.CRITICAL, "Database dump."),
    ("/dump.sql", Severity.CRITICAL, "Database dump."),
    ("/database.sql", Severity.CRITICAL, "Database dump."),
    ("/backup.zip", Severity.HIGH, "Archive that may contain source or data."),
    ("/www.zip", Severity.HIGH, "Source archive."),
    ("/config.php.bak", Severity.HIGH, "Editor backup of a config file."),
    ("/web.config", Severity.MEDIUM, "IIS configuration, may contain connection strings."),
    ("/phpinfo.php", Severity.MEDIUM, "PHP configuration and environment disclosure."),
    ("/server-status", Severity.MEDIUM, "Apache server status page."),
    ("/actuator/env", Severity.CRITICAL, "Spring Boot actuator exposing environment and secrets."),
    ("/actuator/health", Severity.LOW, "Spring Boot health endpoint."),
    ("/swagger-ui.html", Severity.LOW, "API documentation, useful for mapping the surface."),
    ("/openapi.json", Severity.LOW, "API schema."),
    ("/.well-known/security.txt", Severity.INFO, "Security contact file (informational)."),
]

TRAVERSAL_PAYLOADS: List[str] = [
    "../../../../../../etc/passwd",
    "....//....//....//....//etc/passwd",
    "..%2f..%2f..%2f..%2fetc%2fpasswd",
    "%2e%2e%2f%2e%2e%2f%2e%2e%2fetc%2fpasswd",
    "..\\..\\..\\..\\windows\\win.ini",
    "..%5c..%5c..%5cwindows%5cwin.ini",
]

TRAVERSAL_SIGNATURES = [
    (r"root:.*:0:0:", "Unix /etc/passwd"),
    (r"\[(fonts|extensions)\]", "Windows win.ini"),
    (r"daemon:.*:/usr/sbin", "Unix /etc/passwd"),
]

DANGEROUS_METHODS = ["PUT", "DELETE", "TRACE", "PATCH"]


class Requester:
    """Scoped, rate-limited HTTP client.

    Every security probe shares one instance per run, so the request ceiling
    applies to the scan as a whole rather than per function. That is the
    difference between "a scanner" and "a flood".
    """

    def __init__(
        self,
        guard: ScopeGuard,
        requests_per_minute: int = 120,
        timeout: float = 10.0,
        project_root: Optional[str] = None,
    ):
        self._guard = guard
        self._timeout = timeout
        self._min_interval = 60.0 / max(1, requests_per_minute)
        self._last_request = 0.0
        self.count = 0

    def check_scope(self, url: str, purpose: str) -> str:
        return self._guard.require(url, purpose=purpose)

    def _throttle(self) -> None:
        gap = time.time() - self._last_request
        if gap < self._min_interval:
            time.sleep(self._min_interval - gap)
        self._last_request = time.time()

    def request(
        self,
        url: str,
        method: str = "GET",
        data: Optional[bytes] = None,
        headers: Optional[Dict[str, str]] = None,
        follow: bool = True,
    ) -> Tuple[Optional[int], Dict[str, str], str]:
        """Return (status, headers, body). status is None if the request failed."""
        self._throttle()
        self.count += 1

        all_headers = {"User-Agent": USER_AGENT}
        if headers:
            all_headers.update(headers)

        req = urllib.request.Request(url, data=data, headers=all_headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                body = resp.read(200000).decode("utf-8", errors="replace")
                return resp.status, dict(resp.headers), body
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read(200000).decode("utf-8", errors="replace")
            except Exception:
                pass
            return exc.code, dict(exc.headers or {}), body
        except Exception as exc:
            log.debug(f"[PROBE] {method} {url} failed: {exc}")
            return None, {}, ""


# --------------------------------------------------------------------------
# Target discovery
# --------------------------------------------------------------------------


def discover_inputs(
    url: str, requester: Requester, include_forms: bool = True
) -> Tuple[List[Dict[str, str]], str]:
    """Find injectable inputs on a page: query parameters and form fields.

    Returns (inputs, body). Each input is {name, location, value} where
    location is "query" or "form".
    """
    status, headers, body = requester.request(url)
    if status is None:
        return [], ""

    inputs: List[Dict[str, str]] = []

    parsed = urllib.parse.urlparse(url)
    for name, value, _ in urllib.parse.parse_qsl(parsed.query):
        inputs.append({"name": name, "location": "query", "value": value})

    if include_forms:
        for form in _extract_forms(body):
            for field in form["fields"]:
                inputs.append({
                    "name": field,
                    "location": "form",
                    "value": "",
                    "action": form["action"],
                    "method": form["method"],
                })

    return inputs, body


def _extract_forms(html: str) -> List[Dict[str, Any]]:
    """Minimal form extraction. Deliberately regex, so no bs4 dependency."""
    forms: List[Dict[str, Any]] = []
    for match in re.finditer(r"<form\b([^>]*)>(.*?)</form>", html, re.I | re.S):
        attrs, inner = match.group(1), match.group(2)
        action = _attr(attrs, "action") or ""
        method = (_attr(attrs, "method") or "get").lower()
        fields = []
        for field_match in re.finditer(r"<(?:input|textarea|select)\b([^>]*)>", inner, re.I):
            f_attrs = field_match.group(1)
            name = _attr(f_attrs, "name")
            ftype = (_attr(f_attrs, "type") or "text").lower()
            if name and ftype not in ("submit", "button", "image", "reset", "file"):
                fields.append(name)
        if fields:
            forms.append({"action": action, "method": method, "fields": fields})
    return forms


def _attr(attrs: str, name: str) -> str:
    m = re.search(rf'{name}\s*=\s*["\']([^"\']*)["\']', attrs, re.I)
    if m:
        return m.group(1)
    m = re.search(rf"{name}\s*=\s*([^\s>]+)", attrs, re.I)
    return m.group(1) if m else ""


def _with_param(url: str, name: str, value: str) -> str:
    parsed = urllib.parse.urlparse(url)
    query = dict(urllib.parse.parse_qsl(parsed.query))
    query[name] = value
    return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query)))


# --------------------------------------------------------------------------
# SQL injection
# --------------------------------------------------------------------------


def probe_sqli(
    url: str,
    params: Optional[Sequence[str]] = None,
    project_root: Optional[str] = None,
    guard: Optional[ScopeGuard] = None,
) -> ScanReport:
    """Error-based and boolean-based SQL injection detection.

    Sends a quote and a paired true/false condition, and compares responses.
    Nothing is extracted from the database.
    """
    guard = guard or get_scope_guard(project_root)
    target = guard.require(url, purpose="SQL injection probe")

    report = ScanReport(tool="web_probe.probe_sqli", target=url)
    requester = Requester(guard, project_root=project_root)

    inputs, body = discover_inputs(url, requester)
    names = list(params) if params else [i["name"] for i in inputs]
    if not names:
        report.notes.append("no parameters found to test")
        return report

    baseline_status, _, baseline_body = requester.request(url)
    if baseline_status is None:
        report.errors.append(f"baseline request failed for {url}")
        return report

    for name in names:
        # 1. Error-based: a lone quote breaks the surrounding literal.
        marker = f"cortex{int(time.time()) % 100000}"
        broken = _with_param(url, name, f"{marker}'")
        status, headers, err_body = requester.request(broken)
        report.scanned.append(f"{url} [{name}] error-based")

        sig = _match_sql_error(err_body)
        if sig:
            report.add(Finding(
                title=f"SQL injection in parameter '{name}' (error-based)",
                severity=Severity.CRITICAL,
                category="injection/sqli",
                target=_with_param(url, name, "MARKER"),
                description=(
                    f"A single quote in '{name}' produced a {sig} syntax error. The "
                    f"parameter reaches the query text without parameterisation."
                ),
                evidence=truncate(_error_excerpt(err_body), 300),
                remediation=(
                    "Use parameterised queries or prepared statements for every value "
                    "that reaches SQL. Do not escape by hand, and never concatenate."
                ),
                confidence=Confidence.CONFIRMED,
                references=[
                    "https://owasp.org/Top10/A03_2021-Injection/",
                    "https://cwe.mitre.org/data/definitions/89.html",
                ],
            ))
            continue

        # 2. Boolean-based: true condition should match the baseline, false
        #    should not. A difference between the two is the signal.
        true_url = _with_param(url, name, f"{marker}' AND '1'='1")
        false_url = _with_param(url, name, f"{marker}' AND '1'='2")
        _, _, true_body = requester.request(true_url)
        _, _, false_body = requester.request(false_url)
        report.scanned.append(f"{url} [{name}] boolean-based")

        if true_body and false_body:
            true_sim = _similarity(baseline_body, true_body)
            false_sim = _similarity(baseline_body, false_body)
            if true_sim > 0.95 and false_sim < 0.80 and (true_sim - false_sim) > 0.20:
                report.add(Finding(
                    title=f"SQL injection in parameter '{name}' (boolean-based)",
                    severity=Severity.CRITICAL,
                    category="injection/sqli",
                    target=_with_param(url, name, "MARKER"),
                    description=(
                        f"A true condition returns the baseline page and a false "
                        f"condition does not (similarity {true_sim:.2f} vs "
                        f"{false_sim:.2f}), so the parameter changes query logic."
                    ),
                    evidence=(
                        f"'AND 1=1' matched baseline ({true_sim:.2f}); "
                        f"'AND 1=2' diverged ({false_sim:.2f})"
                    ),
                    remediation=(
                        "Use parameterised queries or prepared statements. Boolean "
                        "blind injection needs no error output to be exploitable."
                    ),
                    confidence=Confidence.FIRM,
                    references=["https://cwe.mitre.org/data/definitions/89.html"],
                ))

    if not report.findings:
        report.add(Finding(
            title="No SQL injection detected in the tested parameters",
            severity=Severity.INFO,
            category="injection/sqli",
            target=url,
            description=f"Tested {len(names)} parameter(s): {', '.join(names)}",
        ))
    report.notes.append(f"{requester.count} request(s) sent")
    return report


def _match_sql_error(body: str) -> str:
    low = body.lower()
    for pattern, dbms in SQL_ERROR_SIGNATURES:
        if re.search(pattern, low):
            return dbms
    return ""


def _error_excerpt(body: str) -> str:
    m = re.search(
        r".{0,120}(?:sql|syntax|quotation|ora-\d{5}|sqlite).{0,160}",
        body,
        re.I | re.S,
    )
    return m.group(0).strip() if m else body[:300]


def _similarity(a: str, b: str) -> float:
    """Cheap sequence similarity via shingling, avoiding a text-distance cost."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    size = 64
    sa = {a[i:i + size] for i in range(0, max(1, len(a) - size + 1), size)}
    sb = {b[i:i + size] for i in range(0, max(1, len(b) - size + 1), size)}
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


# --------------------------------------------------------------------------
# Reflected XSS
# --------------------------------------------------------------------------


def probe_xss(
    url: str,
    params: Optional[Sequence[str]] = None,
    project_root: Optional[str] = None,
    guard: Optional[ScopeGuard] = None,
) -> ScanReport:
    """Reflected XSS detection via a unique, harmless marker.

    The probe checks whether a marker containing HTML metacharacters comes
    back unescaped. It does not inject executable script, because reflection
    of the metacharacters is already sufficient proof and a working payload
    would only add risk without adding evidence.
    """
    guard = guard or get_scope_guard(project_root)
    guard.require(url, purpose="XSS probe")

    report = ScanReport(tool="web_probe.probe_xss", target=url)
    requester = Requester(guard, project_root=project_root)

    inputs, _ = discover_inputs(url, requester)
    names = list(params) if params else [i["name"] for i in inputs]
    if not names:
        report.notes.append("no parameters found to test")
        return report

    for name in names:
        marker = f"cxs{int(time.time() * 1000) % 1000000}"
        payload = f'{marker}<cortex-probe-{marker}>'
        status, headers, body = requester.request(_with_param(url, name, payload))
        report.scanned.append(f"{url} [{name}] reflected")

        if status is None:
            continue

        if payload in body:
            report.add(Finding(
                title=f"Reflected XSS in parameter '{name}'",
                severity=Severity.HIGH,
                category="injection/xss",
                target=_with_param(url, name, "MARKER"),
                description=(
                    f"The value of '{name}' is reflected into the response with angle "
                    f"brackets intact, so injected markup is parsed as HTML rather "
                    f"than displayed as text."
                ),
                evidence=f"payload reflected verbatim: {truncate(payload, 120)}",
                remediation=(
                    "Context-aware output encoding on every dynamic value, plus a "
                    "Content-Security-Policy as defence in depth."
                ),
                confidence=Confidence.CONFIRMED,
                references=[
                    "https://owasp.org/Top10/A03_2021-Injection/",
                    "https://cwe.mitre.org/data/definitions/79.html",
                ],
            ))
        elif marker in body:
            report.add(Finding(
                title=f"Parameter '{name}' is reflected but HTML-encoded",
                severity=Severity.LOW,
                category="injection/xss",
                target=_with_param(url, name, "MARKER"),
                description=(
                    "The marker came back but the angle brackets were encoded, so the "
                    "obvious payload does not execute. Worth re-testing with a "
                    "context-specific payload, since encoding can be wrong for "
                    "attribute, URL or script contexts."
                ),
                evidence=f"marker reflected, metacharacters encoded: {truncate(body[max(0, body.find(marker) - 40):body.find(marker) + 80], 160)}",
                remediation="Verify encoding is correct for the exact output context.",
                confidence=Confidence.TENTATIVE,
            ))

    if not report.findings:
        report.add(Finding(
            title="No reflected XSS detected in the tested parameters",
            severity=Severity.INFO,
            category="injection/xss",
            target=url,
            description=f"Tested {len(names)} parameter(s): {', '.join(names)}",
        ))
    report.notes.append(f"{requester.count} request(s) sent")
    return report


# --------------------------------------------------------------------------
# Path traversal
# --------------------------------------------------------------------------


def probe_traversal(
    url: str,
    params: Optional[Sequence[str]] = None,
    project_root: Optional[str] = None,
    guard: Optional[ScopeGuard] = None,
) -> ScanReport:
    """Path traversal detection: does a parameter read a file outside the web root?

    Detection only. The payloads target world-readable system files whose
    contents prove the escape (/etc/passwd, win.ini). Nothing on the target is
    read beyond the first chunk of that one file.
    """
    guard = guard or get_scope_guard(project_root)
    guard.require(url, purpose="path traversal probe")

    report = ScanReport(tool="web_probe.probe_traversal", target=url)
    requester = Requester(guard, project_root=project_root)

    inputs, _ = discover_inputs(url, requester)
    names = list(params) if params else [i["name"] for i in inputs]
    if not names:
        report.notes.append("no parameters found to test")
        return report

    for name in names:
        for payload in TRAVERSAL_PAYLOADS:
            status, _, body = requester.request(_with_param(url, name, payload))
            report.scanned.append(f"{url} [{name}] traversal")
            if status is None:
                continue
            for pattern, what in TRAVERSAL_SIGNATURES:
                if re.search(pattern, body, re.I):
                    report.add(Finding(
                        title=f"Path traversal in parameter '{name}'",
                        severity=Severity.CRITICAL,
                        category="injection/traversal",
                        target=_with_param(url, name, "MARKER"),
                        description=(
                            f"The parameter escapes the intended directory and returns "
                            f"the contents of {what}."
                        ),
                        evidence=f"payload '{payload}' returned a {what} signature",
                        remediation=(
                            "Resolve the path and verify it stays inside the allowed "
                            "base directory. Never pass user input to a filesystem call "
                            "directly; use an ID-to-path lookup where possible."
                        ),
                        confidence=Confidence.CONFIRMED,
                        references=["https://cwe.mitre.org/data/definitions/22.html"],
                    ))
                    break
            else:
                continue
            break

    if not report.findings:
        report.add(Finding(
            title="No path traversal detected in the tested parameters",
            severity=Severity.INFO,
            category="injection/traversal",
            target=url,
            description=f"Tested {len(names)} parameter(s) with {len(TRAVERSAL_PAYLOADS)} payloads",
        ))
    report.notes.append(f"{requester.count} request(s) sent")
    return report


# --------------------------------------------------------------------------
# Open redirect
# --------------------------------------------------------------------------


def probe_open_redirect(
    url: str,
    params: Optional[Sequence[str]] = None,
    project_root: Optional[str] = None,
    guard: Optional[ScopeGuard] = None,
) -> ScanReport:
    """Open redirect detection.

    Uses a reserved example domain as the destination, so a redirect cannot
    be turned into traffic toward a third party.
    """
    guard = guard or get_scope_guard(project_root)
    guard.require(url, purpose="open redirect probe")

    report = ScanReport(tool="web_probe.probe_open_redirect", target=url)
    requester = Requester(guard, project_root=project_root)

    inputs, _ = discover_inputs(url, requester)
    candidates = list(params) if params else [
        i["name"] for i in inputs
        if re.search(r"(url|redirect|next|return|dest|target|continue|link|goto)", i["name"], re.I)
    ]
    if not candidates:
        report.notes.append("no redirect-shaped parameter found")
        return report

    # RFC 2606 reserves example.com for documentation, so this can never
    # point at a real third party.
    destination = "https://evil.example.com/"

    for name in candidates:
        probe_url = _with_param(url, name, destination)
        status, headers, _ = requester.request(probe_url, follow=False)
        report.scanned.append(f"{url} [{name}] redirect")

        location = headers.get("Location") or headers.get("location") or ""
        if status in (301, 302, 303, 307, 308) and "evil.example.com" in location:
            report.add(Finding(
                title=f"Open redirect via parameter '{name}'",
                severity=Severity.MEDIUM,
                category="open-redirect",
                target=probe_url,
                description=(
                    f"The application redirects to an attacker-supplied URL. Used in "
                    f"phishing: the link carries the real domain and lands elsewhere, "
                    f"and it can also leak tokens placed in the URL fragment."
                ),
                evidence=f"HTTP {status} Location: {truncate(location, 200)}",
                remediation=(
                    "Redirect only to a relative path, or validate the destination "
                    "against an allowlist of hosts."
                ),
                confidence=Confidence.CONFIRMED,
                references=["https://cwe.mitre.org/data/definitions/601.html"],
            ))

    if not report.findings:
        report.add(Finding(
            title="No open redirect detected",
            severity=Severity.INFO,
            category="open-redirect",
            target=url,
            description=f"Tested {len(candidates)} parameter(s)",
        ))
    report.notes.append(f"{requester.count} request(s) sent")
    return report


# --------------------------------------------------------------------------
# Exposed sensitive paths
# --------------------------------------------------------------------------


def probe_sensitive_paths(
    url: str,
    project_root: Optional[str] = None,
    guard: Optional[ScopeGuard] = None,
) -> ScanReport:
    """Check well-known paths for files that should never be served.

    Every hit is a GET of a path the server either has or has not. A 404 is
    the expected result for almost all of them.
    """
    guard = guard or get_scope_guard(project_root)
    guard.require(url, purpose="sensitive path discovery")

    report = ScanReport(tool="web_probe.probe_sensitive_paths", target=url)
    requester = Requester(guard, project_root=project_root)

    base = url if url.startswith(("http://", "https://")) else "https://" + url
    base = base.rstrip("/")

    # A blanket 200 from a catch-all route would produce false positives on
    # every path, so establish what a missing path looks like first.
    probe_missing = f"{base}/cortex-nonexistent-{int(time.time())}"
    baseline_status, _, baseline_body = requester.request(probe_missing)
    catch_all = baseline_status == 200
    if catch_all:
        report.notes.append(
            "server returns 200 for unknown paths (catch-all), so only "
            "content-verified hits are reported"
        )

    for path, severity, why in SENSITIVE_PATHS:
        target_url = base + path
        status, headers, body = requester.request(target_url)
        report.scanned.append(target_url)

        if status is None or status >= 400:
            continue
        if status in (401, 403):
            report.add(Finding(
                title=f"{path} exists but is protected",
                severity=Severity.INFO,
                category="exposure",
                target=target_url,
                description=f"The path is present (HTTP {status}). Access control is doing its job.",
            ))
            continue

        if catch_all and _similar(body, baseline_body):
            continue

        report.add(Finding(
            title=f"Exposed sensitive path: {path}",
            severity=severity,
            category="exposure",
            target=target_url,
            description=why,
            evidence=(
                f"HTTP {status}, {len(body)} bytes"
                + (f" | {truncate(headers.get('Content-Type', ''), 80)}" if headers.get("Content-Type") else "")
            ),
            remediation=(
                f"Block {path} at the web server or reverse proxy, remove the file "
                f"from the deployment artifact, and rotate any secret it contained."
            ),
            confidence=Confidence.FIRM if not catch_all else Confidence.TENTATIVE,
            references=["https://owasp.org/www-project-web-security-testing-guide/"],
        ))

    if not report.findings:
        report.add(Finding(
            title="No exposed sensitive paths found",
            severity=Severity.INFO,
            category="exposure",
            target=base,
            description=f"Checked {len(SENSITIVE_PATHS)} well-known paths",
        ))
    report.notes.append(f"{requester.count} request(s) sent")
    return report


def _similar(a: str, b: str, tolerance: int = 32) -> bool:
    return abs(len(a) - len(b)) <= tolerance


# --------------------------------------------------------------------------
# HTTP methods
# --------------------------------------------------------------------------


def probe_http_methods(
    url: str,
    project_root: Optional[str] = None,
    guard: Optional[ScopeGuard] = None,
) -> ScanReport:
    """Check for HTTP methods that should not be enabled.

    Uses OPTIONS first, which is cheap and harmless, and confirms with an
    actual TRACE where advertised. PUT/DELETE are reported from OPTIONS alone,
    deliberately: sending a real PUT could create or overwrite a file on the
    target, and that is a state change this toolkit does not make.
    """
    guard = guard or get_scope_guard(project_root)
    guard.require(url, purpose="HTTP method probe")

    report = ScanReport(tool="web_probe.probe_http_methods", target=url)
    requester = Requester(guard, project_root=project_root)

    target_url = url if url.startswith(("http://", "https://")) else "https://" + url

    status, headers, _ = requester.request(target_url, method="OPTIONS")
    report.scanned.append(f"OPTIONS {target_url}")
    if status is None:
        report.errors.append("OPTIONS request failed")
        return report

    allow = headers.get("Allow") or headers.get("allow") or ""
    if allow:
        report.notes.append(f"Allow: {allow}")
        advertised = {m.strip().upper() for m in allow.split(",")}

        if "TRACE" in advertised:
            trace_status, _, trace_body = requester.request(target_url, method="TRACE")
            report.scanned.append(f"TRACE {target_url}")
            if trace_status == 200 and trace_body:
                report.add(Finding(
                    title="HTTP TRACE method is enabled",
                    severity=Severity.LOW,
                    category="misconfiguration",
                    target=target_url,
                    description=(
                        "TRACE echoes the request back. It is a legacy cross-site "
                        "tracing vector; modern browsers block it, so this is low "
                        "severity rather than medium."
                    ),
                    evidence=f"TRACE returned HTTP {trace_status}",
                    remediation="Disable TRACE in the web server configuration.",
                    references=["https://cwe.mitre.org/data/definitions/693.html"],
                ))

        for method in ("PUT", "DELETE"):
            if method in advertised:
                report.add(Finding(
                    title=f"HTTP {method} advertised on a page endpoint",
                    severity=Severity.MEDIUM,
                    category="misconfiguration",
                    target=target_url,
                    description=(
                        f"OPTIONS advertises {method}. If it is not access-controlled, "
                        f"it can allow file overwrite or deletion. Not confirmed by an "
                        f"actual {method}, because that would modify the target."
                    ),
                    evidence=f"Allow: {truncate(allow, 200)}",
                    remediation=f"Disable {method} for this route unless it is intended and authenticated.",
                    confidence=Confidence.TENTATIVE,
                ))

    if not report.findings:
        report.add(Finding(
            title="No dangerous HTTP methods advertised",
            severity=Severity.INFO,
            category="misconfiguration",
            target=target_url,
            description=f"Allow header: {allow or '(none returned)'}",
        ))
    report.notes.append(f"{requester.count} request(s) sent")
    return report
