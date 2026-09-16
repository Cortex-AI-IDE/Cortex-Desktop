"""
defense.py -- blue-team scanning for the code you actually own.

The offensive modules in this package answer "what can be reached from
outside?". This module answers the other half: "what is wrong in here?".
It reads local source, configuration and lockfiles, so it needs no scope
declaration and opens no sockets.

Three jobs:

  scan_secrets        Find credentials committed into the tree. Pattern
                      matches plus a Shannon-entropy check, because the
                      dangerous ones are the keys with no recognisable
                      prefix, and a pure regex list always misses those.
  check_hardening     Configuration-level problems that ship by accident:
                      an uncommitted .env, DEBUG left on, a wildcard CORS
                      policy, a default framework secret key, raw SQL built
                      by string formatting, verify=False.
  audit_dependencies  Known CVEs in the installed dependency set, via
                      pip-audit / npm audit / safety when they are present.

All three are read-only. Nothing here writes, installs or mutates.
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from src.core.security_toolkit.finding import (
    Confidence,
    Finding,
    ScanReport,
    Severity,
    truncate,
)

try:
    from src.utils.logger import get_logger
except ImportError:
    import logging

    def get_logger(name: str):
        return logging.getLogger(name)

log = get_logger("security_toolkit.defense")

SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "venv", ".venv", "env", "__pycache__",
    "dist", "build", ".next", ".nuxt", "target", "vendor", ".mypy_cache",
    ".pytest_cache", ".tox", "site-packages", ".idea", ".vscode", "msix_output",
    "installer_output", "store_assets", ".playwright-mcp", ".agents",
}

SCANNABLE_SUFFIXES = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".json", ".yaml", ".yml", ".toml",
    ".ini", ".cfg", ".conf", ".env", ".properties", ".xml", ".sh", ".ps1",
    ".bat", ".sql", ".rb", ".php", ".go", ".java", ".cs", ".tf", ".dockerfile",
    ".md", ".txt", ".pem", ".key",
}

MAX_FILE_BYTES = 2_000_000
MAX_FILES = 4000


# --------------------------------------------------------------------------
# Secret scanning
# --------------------------------------------------------------------------

# (name, regex, severity, remediation). Ordered most-specific first.
SECRET_PATTERNS: List[Tuple[str, re.Pattern, Severity, str]] = [
    ("AWS access key ID", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), Severity.CRITICAL,
     "Revoke the key in IAM immediately and move to environment variables."),
    ("AWS secret access key", re.compile(
        r"(?i)aws.{0,20}(?:secret|private).{0,20}['\"]([A-Za-z0-9/+=]{40})['\"]"), Severity.CRITICAL,
     "Revoke the key in IAM immediately."),
    ("GitHub token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b"), Severity.CRITICAL,
     "Revoke in GitHub settings and use Actions secrets."),
    ("GitHub fine-grained token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b"), Severity.CRITICAL,
     "Revoke in GitHub settings."),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), Severity.CRITICAL,
     "Revoke the token in the Slack app configuration."),
    ("Slack webhook", re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/]{40,}"), Severity.HIGH,
     "Revoke the webhook and issue a new one."),
    ("Stripe secret key", re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{20,}\b"), Severity.CRITICAL,
     "Roll the key in the Stripe dashboard."),
    ("OpenAI-style API key", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b"), Severity.CRITICAL,
     "Revoke the key and load it from an environment variable."),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), Severity.HIGH,
     "Restrict or revoke the key in Google Cloud Console."),
    ("Google OAuth client secret", re.compile(r"\bGOCSPX-[A-Za-z0-9_\-]{20,}\b"), Severity.CRITICAL,
     "Rotate the OAuth client secret."),
    ("Anthropic API key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}\b"), Severity.CRITICAL,
     "Revoke the key in the Anthropic console."),
    ("Alibaba Cloud key", re.compile(r"\bLTAI[A-Za-z0-9]{12,}\b"), Severity.CRITICAL,
     "Rotate the AccessKey in RAM."),
    ("SendGrid API key", re.compile(r"\bSG\.[A-Za-z0-9_\-]{22}\.[A-Za-z0-9_\-]{43}\b"), Severity.CRITICAL,
     "Revoke the key in SendGrid."),
    ("Twilio account SID", re.compile(r"\bAC[a-f0-9]{32}\b"), Severity.HIGH,
     "Rotate the auth token."),
    ("Private key block", re.compile(
        r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----"), Severity.CRITICAL,
     "Remove the key from the repository and rotate it. If it was ever pushed, "
     "treat it as compromised."),
    ("JSON Web Token", re.compile(
        r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"), Severity.HIGH,
     "Do not commit issued tokens; they may still be valid."),
    ("Database URL with password", re.compile(
        r"(?i)\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^:\s/]+:[^@\s]+@"), Severity.CRITICAL,
     "Move the connection string to configuration and rotate the password."),
    ("Authorization header", re.compile(
        r"(?i)authorization['\"]?\s*[:=]\s*['\"]?(?:bearer|basic)\s+[A-Za-z0-9._\-+/=]{16,}"), Severity.HIGH,
     "Remove the hardcoded credential."),
    ("Hardcoded password assignment", re.compile(
        r"(?i)\b(password|passwd|pwd|secret|api_?key|auth_?token|access_?token)\b\s*[:=]\s*"
        r"['\"]([^'\"${}<>]{8,80})['\"]"), Severity.HIGH,
     "Load the value from configuration or a secret store, not source."),
]

# Values that look like secrets but never are.
SECRET_ALLOWLIST = {
    "password", "passwd", "secret", "changeme", "example", "placeholder",
    "your_password", "yourpassword", "somepassword", "test", "testpassword",
    "dummy", "fake", "redacted", "none", "null", "undefined", "todo", "xxx",
    "your_api_key", "your_api_key_here", "api_key", "token", "bearer",
    "{{password}}", "$password", "${password}", "os.environ", "process.env",
    "getenv", "settings.password", "config.password",
}

ENTROPY_MIN_LENGTH = 20
ENTROPY_THRESHOLD = 4.2


def shannon_entropy(value: str) -> float:
    """Bits per character. Random secrets land near 4.5-6; prose lands near 3."""
    if not value:
        return 0.0
    counts = Counter(value)
    length = len(value)
    return -sum(
        (count / length) * math.log2(count / length) for count in counts.values()
    )


def _is_probably_real(value: str) -> bool:
    """Reject a pattern match that cannot be a secret.

    Deliberately narrow, because it runs on explicit pattern hits. Only the
    allowlist and obvious interpolation expressions disqualify one. Prose
    heuristics live in _looks_like_prose and are applied only to the entropy
    path: a real secret can contain spaces, and a PEM header does contain
    three of them, so rejecting on whitespace here would silently drop every
    committed private key.
    """
    low = value.strip().lower()
    if low in SECRET_ALLOWLIST:
        return False
    if low.startswith(("${", "$(", "{{", "%(", "os.", "process.", "settings.", "config.")):
        return False
    if re.fullmatch(r"[\W_]+", value):
        return False
    if len(set(value)) < 4:
        return False
    if re.fullmatch(r"(?i)(a+|0+|1+|x+|\.+|\*+)", value):
        return False
    return True


def _looks_like_prose(value: str) -> bool:
    """Entropy-path filter: a sentence or a long path is not a key."""
    return value.count("/") > 4 or value.count(" ") > 2


def _iter_files(root: Path) -> Iterable[Path]:
    seen = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".git")]
        for name in filenames:
            if seen >= MAX_FILES:
                return
            path = Path(dirpath) / name
            suffix = path.suffix.lower()
            if suffix and suffix not in SCANNABLE_SUFFIXES and name.lower() not in (
                "dockerfile", ".env", ".gitignore", ".npmrc", ".pypirc",
            ):
                continue
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            seen += 1
            yield path


def scan_secrets(
    root: str,
    max_findings: int = 200,
) -> ScanReport:
    """Scan a directory tree for committed credentials."""
    base = Path(root).resolve()
    report = ScanReport(tool="defense.scan_secrets", target=str(base))

    if not base.exists():
        report.errors.append(f"path does not exist: {base}")
        return report

    files_scanned = 0
    for path in _iter_files(base):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except (OSError, UnicodeDecodeError):
            continue
        files_scanned += 1
        rel = _rel(path, base)

        for line_no, line in enumerate(text.splitlines(), 1):
            if len(line) > 2000:
                continue

            for name, pattern, severity, fix in SECRET_PATTERNS:
                m = pattern.search(line)
                if not m:
                    continue
                # Group 2 is the captured value for patterns that have one.
                candidate = m.group(m.lastindex) if m.lastindex else m.group(0)
                if not _is_probably_real(candidate):
                    continue
                report.add(Finding(
                    title=f"{name} in {rel}",
                    severity=severity,
                    category="secret",
                    target=f"{rel}:{line_no}",
                    description=(
                        f"A {name} pattern appears in source. Anything committed is "
                        f"in the history and in every clone, so deleting the line is "
                        f"not sufficient on its own."
                    ),
                    evidence=f"line {line_no}: {truncate(_redact(line.strip()), 200)}",
                    remediation=fix,
                    confidence=Confidence.FIRM,
                    references=["https://cwe.mitre.org/data/definitions/798.html"],
                ))
                break  # one finding per line is enough

            else:
                # No named pattern hit. Fall back to entropy on quoted values.
                entropy_hit = _entropy_secret(line)
                if entropy_hit:
                    report.add(Finding(
                        title=f"High-entropy string in {rel}",
                        severity=Severity.MEDIUM,
                        category="secret",
                        target=f"{rel}:{line_no}",
                        description=(
                            "A long random-looking string appears in an assignment. It "
                            "may be a key with no vendor prefix, which pattern matching "
                            "cannot identify."
                        ),
                        evidence=f"line {line_no}: {truncate(_redact(line.strip()), 200)}",
                        remediation="Confirm whether this is a credential; if so, move it to configuration.",
                        confidence=Confidence.TENTATIVE,
                    ))

            if len(report.findings) >= max_findings:
                report.notes.append(f"stopped at {max_findings} findings")
                break
        if len(report.findings) >= max_findings:
            break

    report.notes.append(f"scanned {files_scanned} file(s) under {base}")
    if not report.findings:
        report.add(Finding(
            title="No committed secrets detected",
            severity=Severity.INFO,
            category="secret",
            target=str(base),
            description=f"Scanned {files_scanned} file(s) with {len(SECRET_PATTERNS)} patterns.",
        ))
    return report


def _entropy_secret(line: str) -> bool:
    for m in re.finditer(r"['\"]([A-Za-z0-9+/=_\-]{20,100})['\"]", line):
        value = m.group(1)
        if not _is_probably_real(value):
            continue
        if _looks_like_prose(value):
            continue
        # Require a mix: a long run of hex or base64 with real randomness.
        if len(set(value)) < 10:
            continue
        if shannon_entropy(value) >= ENTROPY_THRESHOLD:
            return True
    return False


def _redact(line: str) -> str:
    """Show enough to locate the secret, never enough to use it."""
    return re.sub(r"([A-Za-z0-9+/=_\-]{8})[A-Za-z0-9+/=_\-]{8,}", r"\1...[REDACTED]", line)


def _rel(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base)).replace("\\", "/")
    except ValueError:
        return str(path)


# --------------------------------------------------------------------------
# Hardening checks
# --------------------------------------------------------------------------

# (name, regex, severity, description, remediation)
HARDENING_RULES: List[Tuple[str, re.Pattern, Severity, str, str]] = [
    ("Django DEBUG enabled", re.compile(r"(?i)^\s*DEBUG\s*=\s*True"), Severity.HIGH,
     "DEBUG=True returns full stack traces, settings and SQL to any visitor who "
     "triggers an error.",
     "Read DEBUG from the environment and default it to False."),
    ("Django ALLOWED_HOSTS wildcard", re.compile(r"(?i)ALLOWED_HOSTS\s*=\s*\[[^\]]*['\"]\*['\"]"), Severity.HIGH,
     "A wildcard host header allows host-header poisoning of password-reset links.",
     "List the real hostnames explicitly."),
    ("Django SECRET_KEY hardcoded", re.compile(
        r"(?i)^\s*SECRET_KEY\s*=\s*['\"][^'\"]{20,}['\"]"), Severity.CRITICAL,
     "A committed SECRET_KEY lets an attacker forge session cookies and signed tokens.",
     "Load SECRET_KEY from the environment and rotate it."),
    ("Flask secret key hardcoded", re.compile(
        r"(?i)app\.secret_key\s*=\s*['\"][^'\"]{8,}['\"]"), Severity.CRITICAL,
     "A committed secret key allows session forgery.",
     "Load from the environment and rotate."),
    ("CORS allow all origins", re.compile(
        r"(?i)(allow_origins\s*=\s*\[[^\]]*['\"]\*['\"]|Access-Control-Allow-Origin['\"]?\s*[:,]\s*['\"]\*)"), Severity.MEDIUM,
     "Any site can read responses from this API.",
     "Replace the wildcard with an explicit origin allowlist."),
    ("TLS verification disabled", re.compile(
        r"(?i)(verify\s*=\s*False|rejectUnauthorized\s*:\s*false|verify_ssl\s*=\s*false|InsecureSkipVerify\s*:\s*true|CURLOPT_SSL_VERIFYPEER\s*,\s*0)"), Severity.HIGH,
     "Disabling certificate verification makes every HTTPS request interceptable.",
     "Enable verification and fix the underlying certificate problem."),
    ("subprocess with shell=True", re.compile(
        r"subprocess\.(?:run|call|Popen|check_output)\([^)]*shell\s*=\s*True"), Severity.MEDIUM,
     "shell=True passes the command to a shell, so any interpolated user input "
     "can inject a second command.",
     "Pass an argument list and drop shell=True."),
    ("os.system call", re.compile(r"\bos\.system\s*\("), Severity.MEDIUM,
     "os.system always runs through a shell and cannot be parameterised safely.",
     "Use subprocess with an argument list."),
    ("eval of dynamic input", re.compile(r"(?<![\w.])eval\s*\("), Severity.HIGH,
     "eval executes arbitrary Python from a string.",
     "Replace with a lookup table, ast.literal_eval, or explicit parsing."),
    ("exec of dynamic input", re.compile(r"(?<![\w.])exec\s*\("), Severity.HIGH,
     "exec executes arbitrary Python from a string.",
     "Remove it, or restrict input to a validated allowlist."),
    ("pickle deserialisation", re.compile(r"pickle\.loads?\s*\("), Severity.HIGH,
     "Unpickling untrusted data executes arbitrary code.",
     "Use JSON, or a signed and verified format."),
    ("yaml.load without SafeLoader", re.compile(r"yaml\.load\s*\((?![^)]*Loader)"), Severity.HIGH,
     "yaml.load can instantiate arbitrary Python objects.",
     "Use yaml.safe_load."),
    ("SQL built by f-string", re.compile(
        r"(?i)execute\s*\(\s*f['\"][^'\"]*\b(select|insert|update|delete)\b"), Severity.CRITICAL,
     "Formatting values into SQL is injection, even when the value looks safe.",
     "Use parameterised queries: execute('... WHERE id = %s', [user_id])."),
    ("SQL built by concatenation", re.compile(
        r"(?i)execute\s*\(\s*['\"][^'\"]*\b(select|insert|update|delete)\b[^'\"]*['\"]\s*(\+|%|\.format)"), Severity.CRITICAL,
     "Concatenated SQL is injection when any part is user-controlled.",
     "Use parameterised queries."),
    ("hardcoded JWT secret", re.compile(
        r"(?i)(jwt[_.]?(secret|key)|JWT_SECRET)\s*[:=]\s*['\"][^'\"]{8,}['\"]"), Severity.CRITICAL,
     "A committed signing secret allows forging any token.",
     "Load from the environment and rotate."),
    ("MD5 or SHA1 used for hashing", re.compile(
        r"(?i)hashlib\.(md5|sha1)\s*\("), Severity.LOW,
     "Both are broken for security purposes, though they remain fine for "
     "non-security checksums.",
     "Use SHA-256 or better; for passwords use bcrypt/scrypt/argon2."),
]

GITIGNORE_MUST_HAVE = [
    ".env", "*.pem", "*.key", "id_rsa", ".credentials", "secrets.json",
    "*.pfx", "*.p12",
]


def check_hardening(root: str, max_findings: int = 200) -> ScanReport:
    """Static checks for configuration and coding mistakes that ship by accident."""
    base = Path(root).resolve()
    report = ScanReport(tool="defense.check_hardening", target=str(base))
    if not base.exists():
        report.errors.append(f"path does not exist: {base}")
        return report

    files_scanned = 0
    for path in _iter_files(base):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except (OSError, UnicodeDecodeError):
            continue
        files_scanned += 1
        rel = _rel(path, base)
        lines = text.splitlines()

        for line_no, line in enumerate(lines, 1):
            stripped = line.strip()
            # A rule is documentation or a suppression if it sits in a comment.
            if stripped.startswith(("#", "//", "*")) and "noqa" not in stripped:
                pass  # still scanned; comments often carry the bad pattern verbatim
            for name, pattern, severity, why, fix in HARDENING_RULES:
                if pattern.search(line):
                    if "noqa" in line or "nosec" in line:
                        continue
                    report.add(Finding(
                        title=f"{name} ({rel}:{line_no})",
                        severity=severity,
                        category="hardening",
                        target=f"{rel}:{line_no}",
                        description=why,
                        evidence=truncate(line.strip(), 200),
                        remediation=fix,
                        confidence=Confidence.FIRM,
                    ))
                    break
            if len(report.findings) >= max_findings:
                break
        if len(report.findings) >= max_findings:
            report.notes.append(f"stopped at {max_findings} findings")
            break

    report.notes.append(f"scanned {files_scanned} file(s)")
    if not report.findings:
        report.add(Finding(
            title="No hardening issues detected",
            severity=Severity.INFO,
            category="hardening",
            target=str(base),
            description=f"Applied {len(HARDENING_RULES)} rules across {files_scanned} file(s).",
        ))
    return report


def check_gitignore(root: str) -> ScanReport:
    """Check that .gitignore covers secret-bearing file types."""
    base = Path(root).resolve()
    report = ScanReport(tool="defense.check_gitignore", target=str(base))

    gi = base / ".gitignore"
    if not gi.exists():
        report.add(Finding(
            title="No .gitignore in the project root",
            severity=Severity.MEDIUM,
            category="hardening",
            target=str(base),
            description=(
                "Without a .gitignore, a .env or a key file is one `git add .` away "
                "from being committed, and history keeps it forever."
            ),
            remediation="Add a .gitignore covering .env, *.pem, *.key and similar.",
        ))
        return report

    text = gi.read_text(encoding="utf-8", errors="ignore")
    entries = {line.strip() for line in text.splitlines() if line.strip()}

    missing = []
    for needed in GITIGNORE_MUST_HAVE:
        if not any(needed == e or e.startswith(needed) or needed in e for e in entries):
            missing.append(needed)

    if missing:
        report.add(Finding(
            title=".gitignore missing secret-bearing patterns",
            severity=Severity.LOW,
            category="hardening",
            target=str(gi),
            description="Patterns not covered: " + ", ".join(missing),
            remediation="Add the missing patterns to .gitignore.",
        ))

    environment_file = base / ".env"
    if environment_file.exists():
        report.add(Finding(
            title="A .env file exists in the project root",
            severity=Severity.INFO,
            category="hardening",
            target=str(environment_file),
            description=(
                "Expected in development. Verify it is gitignored and that it does "
                "not hold production credentials."
            ),
            remediation="Confirm .env is ignored, and keep production secrets out of it.",
        ))
    return report


# --------------------------------------------------------------------------
# Dependency audit
# --------------------------------------------------------------------------


def _run(cmd: List[str], cwd: str, timeout: int = 180) -> Tuple[int, str, str]:
    kwargs: Dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, **kwargs
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except FileNotFoundError:
        return -1, "", "not installed"
    except subprocess.TimeoutExpired:
        return -2, "", f"timed out after {timeout}s"
    except Exception as exc:
        return -3, "", str(exc)


def audit_dependencies(root: str, timeout: int = 180) -> ScanReport:
    """Run whichever dependency auditors are available on this machine."""
    base = Path(root).resolve()
    report = ScanReport(tool="defense.audit_dependencies", target=str(base))
    ran_any = False

    # pip-audit reads the environment or a requirements file.
    if (base / "requirements.txt").exists() or (base / "pyproject.toml").exists():
        code, out, err = _run(
            [sys.executable, "-m", "pip_audit", "--format", "json",
             "--progress-spinner", "off"],
            str(base), timeout,
        )
        if code == -1 or "No module named" in err:
            report.notes.append("pip-audit not installed (pip install pip-audit)")
        elif code == 0 and out.strip():
            ran_any = True
            report.extend(_parse_pip_audit(out))
        elif code not in (0, -1) and not out.strip():
            report.notes.append(f"pip-audit returned {code}: {truncate(err, 200)}")

    # npm audit needs a lockfile and a node toolchain.
    if (base / "package-lock.json").exists():
        code, out, err = _run(["npm", "audit", "--json"], str(base), timeout)
        if "not installed" in err or code == -1:
            report.notes.append("npm not available for dependency audit")
        elif out.strip():
            ran_any = True
            report.extend(_parse_npm_audit(out))

    if not ran_any:
        report.notes.append(
            "no dependency auditor available; install pip-audit "
            "(pip install pip-audit) or run npm audit in a Node project"
        )
        report.add(Finding(
            title="Dependency audit could not run",
            severity=Severity.INFO,
            category="dependencies",
            target=str(base),
            description="Known-CVE checking needs pip-audit or npm available locally.",
            remediation="pip install pip-audit, then re-run.",
        ))
    return report


def _parse_pip_audit(payload: str) -> List[Finding]:
    findings: List[Finding] = []
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return findings

    deps = data.get("dependencies") if isinstance(data, dict) else data
    for dep in deps or []:
        name = dep.get("name", "unknown")
        version = dep.get("version", "?")
        for vuln in dep.get("vulns", []):
            vuln_id = vuln.get("id", "CVE-?")
            fixes = vuln.get("fix_versions") or []
            findings.append(Finding(
                title=f"{name} {version} affected by {vuln_id}",
                severity=_severity_from_cvss(_cvss_from_vuln(vuln)),
                category="dependencies",
                target=f"{name}=={version}",
                description=truncate(vuln.get("description", ""), 500),
                evidence=f"{vuln_id}; affected: {', '.join(vuln.get('aliases', []) or ['-'])}",
                remediation=(
                    f"Upgrade to {', '.join(fixes)}" if fixes
                    else "No fixed version published yet; monitor or remove the dependency."
                ),
                confidence=Confidence.CONFIRMED,
                references=[f"https://osv.dev/vulnerability/{vuln_id}"],
            ))
    return findings


def _parse_npm_audit(payload: str) -> List[Finding]:
    findings: List[Finding] = []
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return findings

    vulnerabilities = data.get("vulnerabilities") or {}
    for name, info in vulnerabilities.items():
        severity = _severity_from_label(info.get("severity", "low"))
        via = info.get("via") or []
        first = via[0] if via and isinstance(via[0], dict) else {}
        fix = (info.get("fixAvailable") or {})
        findings.append(Finding(
            title=f"{name} vulnerable ({severity.value})",
            severity=severity,
            category="dependencies",
            target=str(name),
            description=truncate(first.get("title", "Vulnerable dependency"), 400),
            evidence=f"range: {info.get('range', '?')}",
            remediation=(
                f"Upgrade to {fix.get('version')}" if isinstance(fix, dict) and fix.get("version")
                else "Run npm audit fix, or pin a patched version."
            ),
            confidence=Confidence.CONFIRMED,
            references=[first.get("url")] if first.get("url") else [],
        ))
    return findings


def _cvss_from_vuln(vuln: Dict[str, Any]) -> float:
    for key in ("cvss", "cvss_score", "severity"):
        value = vuln.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    for entry in vuln.get("severity") or []:
        if isinstance(entry, dict) and entry.get("score") is not None:
            return float(entry["score"])
    return 5.0


def _severity_from_cvss(score: float) -> Severity:
    if score >= 9.0:
        return Severity.CRITICAL
    if score >= 7.0:
        return Severity.HIGH
    if score >= 4.0:
        return Severity.MEDIUM
    return Severity.LOW


def _severity_from_label(label: str) -> Severity:
    return {
        "critical": Severity.CRITICAL,
        "high": Severity.HIGH,
        "moderate": Severity.MEDIUM,
        "medium": Severity.MEDIUM,
        "low": Severity.LOW,
    }.get(str(label).lower(), Severity.MEDIUM)


# --------------------------------------------------------------------------
# Local SAST tools
# --------------------------------------------------------------------------

SAST_TOOLS: List[Tuple[str, List[str], str]] = [
    ("bandit", [sys.executable, "-m", "bandit", "-r", ".", "-f", "json", "-q"], "Python"),
    ("semgrep", ["semgrep", "--json", "--config", "auto", "--quiet"], "multi-language"),
]


def run_sast(root: str, timeout: int = 300) -> ScanReport:
    """Run bandit or semgrep if the user has them installed.

    Neither is a dependency of Cortex: this reports what is present rather
    than installing anything, because installing a scanner into the user's
    environment as a side effect of a scan is not acceptable.
    """
    base = Path(root).resolve()
    report = ScanReport(tool="defense.run_sast", target=str(base))
    ran = False

    for name, cmd, language in SAST_TOOLS:
        code, out, err = _run(cmd, str(base), timeout)
        if "not installed" in err or "No module named" in err or code in (-1, -3):
            continue
        if not out.strip():
            continue
        ran = True
        report.notes.append(f"{name} ({language}) executed")
        if name == "bandit":
            report.extend(_parse_bandit(out))
        else:
            report.extend(_parse_semgrep(out))

    if not ran:
        report.add(Finding(
            title="No local SAST engine available",
            severity=Severity.INFO,
            category="sast",
            target=str(base),
            description=(
                "Neither bandit nor semgrep is installed, so taint-style analysis "
                "was skipped. check_hardening still covers the common patterns."
            ),
            remediation="pip install bandit (or semgrep) for deeper analysis.",
        ))
    return report


def _parse_bandit(payload: str) -> List[Finding]:
    findings: List[Finding] = []
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return findings

    for issue in data.get("results", []):
        severity = _severity_from_label(issue.get("issue_severity", "low"))
        findings.append(Finding(
            title=issue.get("issue_text", "Bandit finding"),
            severity=severity,
            category="sast",
            target=f"{_strip_root(issue.get('filename'))}:{issue.get('line_number')}",
            description=issue.get("issue_text", ""),
            evidence=truncate(issue.get("code", ""), 300),
            remediation=f"See {issue.get('more_info', 'bandit documentation')}",
            confidence=Confidence.FIRM,
            metadata={"test_id": issue.get("test_id", "")},
        ))
    return findings


def _parse_semgrep(payload: str) -> List[Finding]:
    findings: List[Finding] = []
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return findings

    for result in data.get("results", []):
        meta = result.get("extra", {}) or {}
        severity = _severity_from_label(meta.get("severity", "warning"))
        findings.append(Finding(
            title=meta.get("message", "Semgrep finding"),
            severity=severity,
            category="sast",
            target=f"{_strip_root(result.get('path'))}:{result.get('start', {}).get('line')}",
            description=meta.get("message", ""),
            evidence=truncate(meta.get("lines", ""), 300),
            remediation=f"Rule: {result.get('check_id', 'unknown')}",
            confidence=Confidence.FIRM,
        ))
    return findings


def _strip_root(path: Optional[str]) -> str:
    if not path:
        return "?"
    return str(path).lstrip("./")


# --------------------------------------------------------------------------
# Orchestrator
# --------------------------------------------------------------------------


def scan_project(
    root: str,
    include_dependencies: bool = True,
    include_sast: bool = False,
) -> ScanReport:
    """Run the full defensive sweep and merge everything into one report."""
    base = Path(root).resolve()
    report = ScanReport(tool="defense.scan_project", target=str(base))

    for part in (scan_secrets(str(base)), check_hardening(str(base)), check_gitignore(str(base))):
        report.extend([
            f for f in part.findings if f.severity is not Severity.INFO
        ])
        report.notes.extend(part.notes)
        report.errors.extend(part.errors)

    if include_dependencies:
        dep = audit_dependencies(str(base))
        report.extend([f for f in dep.findings if f.severity is not Severity.INFO])
        report.notes.extend(dep.notes)

    if include_sast:
        sast = run_sast(str(base))
        report.extend([f for f in sast.findings if f.severity is not Severity.INFO])
        report.notes.extend(sast.notes)

    if not report.findings:
        report.add(Finding(
            title="No defensive findings",
            severity=Severity.INFO,
            category="summary",
            target=str(base),
            description="Secrets, hardening rules, .gitignore and dependency checks are all clean.",
        ))
    return report
