"""
tools.py -- the single entry point the Cortex agent calls.

Why one tool rather than five: every extra tool schema is permanent context
cost and another thing for the model to pick between. `SecurityScan` with an
`action` enum is one schema that covers the whole toolkit, and the enum makes
the available capabilities visible to the model in one place.

THE ONE RULE THAT MATTERS HERE
------------------------------
The `scope` action is READ-ONLY. There is deliberately no action that writes
the scope file, and this is not an oversight.

If the agent could declare its own scope, the scope gate would be decorative:
a model that had been talked into scanning something by a poisoned file could
simply declare that target first and then scan it, and the whole control
would collapse into a prompt instruction. The gate only means something when
the thing being gated cannot open it. Scope is changed by the human, in
.cortex/security/scope.json or through Cortex's own settings UI.

Output budget: an agent turn must not be handed 100 KB of JSON. render_report
produces a compact text block capped well below the context limit, because a
huge tool result on the GUI thread is a freeze waiting to happen.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.security_toolkit import defense, recon, web_probe
from src.core.security_toolkit import report as report_mod
from src.core.security_toolkit.finding import Finding, ScanReport, Severity
from src.core.security_toolkit.scope import (
    ScopeViolation,
    get_scope_guard,
)

try:
    from src.utils.logger import get_logger
except ImportError:
    import logging

    def get_logger(name: str):
        return logging.getLogger(name)

log = get_logger("security_toolkit.tools")

ACTIONS = (
    "scope",
    "scan_code",
    "recon",
    "scan_ports",
    "scan_web",
    "scan_all",
    "report",
)

MAX_RENDER_CHARS = 8000
MAX_FINDINGS_RENDERED = 40

# ------------------------------------------------------------------- session
# A client report covers a whole engagement, which is several tool calls:
# recon, then ports, then the web probes, then the code sweep. Each call
# returns its own payload, so the merged result of each call is kept here and
# `action='report'` renders one document from all of them instead of only the
# last sweep. Capped, because this process runs for a working day and an agent
# that loops must not be able to grow a list without bound.
MAX_SESSION_REPORTS = 60
_SESSION_REPORTS: List[ScanReport] = []


def record_reports(reports: List[ScanReport]) -> None:
    """Log a completed scan for the engagement report, oldest dropped first."""
    _SESSION_REPORTS.extend(r for r in reports if r is not None)
    overflow = len(_SESSION_REPORTS) - MAX_SESSION_REPORTS
    if overflow > 0:
        del _SESSION_REPORTS[:overflow]


def session_reports() -> List[ScanReport]:
    return list(_SESSION_REPORTS)


def clear_session_reports() -> None:
    _SESSION_REPORTS.clear()


def _resolve_root(project_root: Optional[str]) -> Optional[str]:
    if project_root:
        return project_root
    return None


def security_tool(
    action: str,
    target: Optional[str] = None,
    project_root: Optional[str] = None,
    ports: Any = None,
    params: Optional[List[str]] = None,
    port: int = 443,
    include_dependencies: bool = True,
    include_sast: bool = False,
    max_findings: int = 120,
    report_format: str = "markdown",
    engagement: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run one security action and return a capped, renderable result.

    Never raises for an out-of-scope target: the refusal is returned as a
    structured result so the model sees a clear, actionable explanation
    rather than a stack trace it would try to work around.
    """
    action = (action or "").strip().lower()
    root = _resolve_root(project_root)
    guard = get_scope_guard(root)

    if action not in ACTIONS:
        return _error(f"unknown action '{action}'. Valid: {', '.join(ACTIONS)}")

    # ---- scope: read-only, by design -------------------------------------
    if action == "scope":
        status = guard.status()
        lines = [
            "Security scope (read-only here, by design):",
            f"  authorized : {status['authorized']}",
            f"  note       : {status['note'] or '(none)'}",
            f"  expires    : {status['expires'] or '(never)'}"
            + ("  [EXPIRED]" if status["expired"] else ""),
            f"  targets    : {', '.join(status['targets']) if status['targets'] else '(none declared)'}",
            f"  source     : {status['source']}",
            "",
            "Always allowed: 127.0.0.0/8, ::1, localhost",
            "Always blocked: 169.254.0.0/16 and cloud metadata hostnames",
            "",
            "The agent cannot declare scope. To authorize a target, the user edits",
            ".cortex/security/scope.json in the project (or the global file at",
            "~/.cortex/security/scope.json) and adds it to \"targets\".",
        ]
        return {
            "action": "scope",
            "ok": True,
            "scope": status,
            "text": "\n".join(lines),
            "reports": [],
        }

    # ---- report: render the scans so far into a client document ----------
    if action == "report":
        return _write_report_action(root, guard, report_format, engagement)

    # ---- everything else needs a target, except the local code scan ------
    if action == "scan_code":
        base = target or root or str(Path.cwd())
        report = defense.scan_project(
            base,
            include_dependencies=include_dependencies,
            include_sast=include_sast,
        )
        return _pack(action, target=base, reports=[report])

    if not target:
        return _error(f"action '{action}' requires a 'target' argument")

    try:
        reports = _run_network_action(
            action, target, root, guard, ports, params, port, max_findings
        )
    except ScopeViolation as exc:
        return _scope_refusal(action, target, str(exc), guard)
    except Exception as exc:  # a probe bug must not kill the agent turn
        log.exception(f"[SECTOOL] action={action} target={target} failed")
        return _error(f"{type(exc).__name__}: {exc}")

    return _pack(action, target=target, reports=reports)


def _run_network_action(
    action: str,
    target: str,
    root: Optional[str],
    guard: Any,
    ports: Any,
    params: Optional[List[str]],
    port: int,
    max_findings: int,
) -> List[ScanReport]:
    if action == "recon":
        reports = [
            recon.resolve_dns(target, project_root=root),
            recon.inspect_tls(target, port=port, project_root=root),
        ]
        reports.append(recon.analyze_http_headers(target, project_root=root))
        return reports

    if action == "scan_ports":
        return [recon.scan_ports(target, ports=ports, project_root=root)]

    if action == "scan_web":
        return [
            web_probe.probe_sqli(target, params=params, project_root=root),
            web_probe.probe_xss(target, params=params, project_root=root),
            web_probe.probe_traversal(target, params=params, project_root=root),
            web_probe.probe_open_redirect(target, params=params, project_root=root),
            web_probe.probe_sensitive_paths(target, project_root=root),
            web_probe.probe_http_methods(target, project_root=root),
        ]

    if action == "scan_all":
        reports = [
            recon.resolve_dns(target, project_root=root),
            recon.inspect_tls(target, port=port, project_root=root),
            recon.analyze_http_headers(target, project_root=root),
            recon.scan_ports(target, ports=ports, project_root=root),
        ]
        reports.extend([
            web_probe.probe_sqli(target, params=params, project_root=root),
            web_probe.probe_xss(target, params=params, project_root=root),
            web_probe.probe_sensitive_paths(target, project_root=root),
            web_probe.probe_http_methods(target, project_root=root),
        ])
        return reports

    raise ValueError(f"unhandled action {action}")


def _pack(action: str, target: str, reports: List[ScanReport]) -> Dict[str, Any]:
    """Merge reports and render a bounded summary for the model."""
    merged = ScanReport(tool=f"security.{action}", target=target)
    for r in reports:
        merged.extend(r.findings)
        merged.scanned.extend(r.scanned)
        merged.notes.extend(r.notes)
        merged.errors.extend(r.errors)

    # Keep this run for `action='report'`, which renders the whole engagement
    # rather than only the last sweep.
    record_reports([merged])

    payload = {
        "action": action,
        "ok": True,
        "target": target,
        "risk_score": merged.risk_score(),
        "counts": merged.counts(),
        "summary": merged.summary_line(),
        "text": render_report(merged),
        "findings": [
            f.to_dict() for f in merged.sorted_findings()[:MAX_FINDINGS_RENDERED]
        ],
        "notes": merged.notes[:40],
        "errors": merged.errors[:20],
    }
    return payload


def _scope_refusal(action: str, target: str, message: str, guard: Any) -> Dict[str, Any]:
    status = guard.status()
    text = (
        f"BLOCKED by the security scope gate.\n\n{message}\n\n"
        f"Declared targets: {', '.join(status['targets']) or '(none)'}\n"
        f"Scope file: {status['source']}\n\n"
        "Do NOT retry this target and do not attempt to work around this. The "
        "scope file is edited by the user, not by you. Tell the user which host "
        "was refused and that they can authorize it by adding it to "
        ".cortex/security/scope.json if it is theirs and they have permission "
        "to test it."
    )
    return {
        "action": action,
        "ok": False,
        "blocked": True,
        "target": target,
        "scope": status,
        "summary": "blocked by scope policy",
        "text": text,
        "findings": [],
    }


def _error(message: str) -> Dict[str, Any]:
    return {
        "action": "",
        "ok": False,
        "summary": "error",
        "text": f"SecurityScan error: {message}",
        "findings": [],
    }


def render_report(report: ScanReport) -> str:
    """Compact text rendering, hard-capped so one scan cannot flood the turn."""
    findings = report.sorted_findings()
    counts = report.counts()

    lines = [
        f"Security scan: {report.tool}",
        f"Target: {report.target or '(none)'}",
        f"Result: {report.summary_line()}  |  risk score {report.risk_score()}/100",
        "",
    ]

    actionable = [f for f in findings if f.severity is not Severity.INFO]
    informational = [f for f in findings if f.severity is Severity.INFO]

    if actionable:
        lines.append(f"FINDINGS ({len(actionable)}):")
        for i, f in enumerate(actionable[:MAX_FINDINGS_RENDERED], 1):
            lines.append(
                f"{i}. [{f.severity.value.upper()}] {f.title}\n"
                f"   where: {f.target or '-'}\n"
                f"   why:   {f.description or '-'}"
            )
            if f.evidence:
                lines.append(f"   proof: {f.evidence}")
            if f.remediation:
                lines.append(f"   fix:   {f.remediation}")
            if f.confidence.value != "firm":
                lines.append(f"   confidence: {f.confidence.value}")
        if len(actionable) > MAX_FINDINGS_RENDERED:
            lines.append(f"... and {len(actionable) - MAX_FINDINGS_RENDERED} more")
    else:
        lines.append("No actionable findings.")

    for f in informational[:5]:
        lines.append(f"- {f.title}: {f.description}")

    if report.notes:
        lines.append("")
        lines.append("Notes: " + " | ".join(report.notes[:10]))
    if report.errors:
        lines.append("Errors: " + " | ".join(report.errors[:10]))

    text = "\n".join(lines)
    if len(text) > MAX_RENDER_CHARS:
        text = text[:MAX_RENDER_CHARS] + "\n...[report truncated]"
    return text


def summarize_for_review(reports: List[ScanReport]) -> Dict[str, Any]:
    """Aggregate counts across several reports, for a UI panel or a summary."""
    counts = {s.value: 0 for s in Severity}
    total = 0
    top: List[Finding] = []
    for r in reports:
        for f in r.findings:
            counts[f.severity.value] += 1
            if f.severity is not Severity.INFO:
                total += f.severity.weight
            top.append(f)
    top.sort(key=lambda f: f.severity.rank)
    return {
        "counts": counts,
        "risk_score": min(100, total),
        "top_findings": [f.to_dict() for f in top[:10]],
    }


def _write_report_action(
    root: Optional[str],
    guard: Any,
    fmt: str,
    engagement: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Render the engagement so far as a document the user can hand over.

    This is the only action that writes to disk. It writes one file into the
    project's own .cortex/security/reports/ directory, and that is the bound
    of what it can do: it renders findings that the gated scan actions already
    produced, and it cannot declare scope. A report is an output, not a
    control surface.
    """
    reports = session_reports()
    if not reports:
        return _error(
            "no scans recorded in this session yet. Run scan_code, recon, "
            "scan_ports or scan_web first, then create the report."
        )

    # Only known fields are accepted, so a model that invents a key gets a
    # report rather than a TypeError from the dataclass constructor.
    keys = ("client", "name", "assessor", "authorization", "window", "notes")
    fields = {
        k: str(v)
        for k, v in (engagement or {}).items()
        if k in keys and v not in (None, "")
    }
    status = guard.status()
    if status.get("authorized") and not fields.get("notes"):
        # The scope file's own note is the recorded reason those targets were
        # authorized, so it belongs in the report's scope section.
        fields["notes"] = str(status.get("note") or "")
    eng = report_mod.Engagement(**fields)

    base = root or str(Path.cwd())
    out_dir = Path(base) / ".cortex" / "security" / report_mod.REPORT_DIR_NAME
    try:
        path = report_mod.write_report(reports, str(out_dir), eng, fmt)
    except ValueError as exc:
        return _error(str(exc))
    except Exception as exc:
        log.exception("[SECTOOL] report write failed")
        return _error(f"could not write report: {type(exc).__name__}: {exc}")

    bundle = report_mod.build_bundle(reports, eng)
    counts = bundle.counts()
    text = (
        f"Security report written to: {path}\n"
        f"Scans included: {len(reports)}\n"
        f"Result: {counts['critical']} critical, {counts['high']} high, "
        f"{counts['medium']} medium, {counts['low']} low  |  "
        f"risk score {bundle.risk_score()}/100\n\n"
        "Tell the user the file path and summarise the most serious findings "
        "in a short paragraph. Do not paste the whole document into the chat."
    )
    log.info(f"[SECTOOL] report written to {path}")
    return {
        "action": "report",
        "ok": True,
        "path": str(path),
        "format": path.suffix.lstrip("."),
        "scans_included": len(reports),
        "risk_score": bundle.risk_score(),
        "counts": counts,
        "summary": f"report written to {path}",
        "text": text,
        "findings": [
            f.to_dict() for f in bundle.actionable()[:MAX_FINDINGS_RENDERED]
        ],
    }
