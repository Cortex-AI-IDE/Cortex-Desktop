"""
Cortex security toolkit -- offensive and defensive security testing.

Two halves, one gate:

  Offensive (recon, web_probe)  Active testing against a target. Everything
                                goes through ScopeGuard, so a host that has
                                not been declared by the user is refused
                                before a socket is opened.
  Defensive (defense)           Local scanning of your own source: committed
                                secrets, hardening mistakes, dependency CVEs,
                                and any local SAST engine that is installed.

Entry point for the agent: tools.security_tool(). It is exposed as the
single `SecurityScan` tool, with an `action` enum, so the whole toolkit costs
one schema in the model's context.

The scope gate is enforced in code. It cannot be changed by the agent: see
the module docstring in tools.py for why that is deliberate.

Quick manual use:

    from src.core.security_toolkit import security_tool

    security_tool("scope")                          # what am I allowed to test
    security_tool("scan_code", target=r"C:\\proj")   # defensive sweep
    security_tool("scan_ports", target="127.0.0.1") # loopback is allowed by default
    security_tool("scan_web", target="http://localhost:3000")
    security_tool("report", report_format="html")    # client-ready document
"""

from src.core.security_toolkit.finding import (
    Confidence,
    Finding,
    ScanReport,
    Severity,
    truncate,
)
from src.core.security_toolkit.scope import (
    ScopeDecision,
    ScopeGuard,
    ScopePolicy,
    ScopeViolation,
    get_scope_guard,
    reset_scope_guard,
)
from src.core.security_toolkit.report import (
    Engagement,
    ReportBundle,
    build_bundle,
    render_html,
    render_markdown,
    write_report,
)
from src.core.security_toolkit.tools import (
    ACTIONS,
    clear_session_reports,
    record_reports,
    security_tool,
    summarize_for_review,
)

__all__ = [
    "ACTIONS",
    "Confidence",
    "Engagement",
    "Finding",
    "ReportBundle",
    "ScanReport",
    "ScopeDecision",
    "ScopeGuard",
    "ScopePolicy",
    "ScopeViolation",
    "Severity",
    "build_bundle",
    "clear_session_reports",
    "get_scope_guard",
    "record_reports",
    "render_html",
    "render_markdown",
    "reset_scope_guard",
    "security_tool",
    "summarize_for_review",
    "truncate",
    "write_report",
]
