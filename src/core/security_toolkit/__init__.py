"""
Cortex security toolkit - offensive and defensive security testing.

Offensive (recon, web_probe) runs active testing against a declared target,
gated by ScopeGuard so an undeclared host is refused before a socket opens.
Defensive (defense) scans local source for committed secrets, hardening
mistakes and dependency CVEs. The agent's entry point is tools.security_tool(),
exposed as the single `SecurityScan` tool with an `action` enum, so the toolkit
costs one schema; the scope gate is enforced in code, not by the agent.
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
