"""
finding.py -- shared result model for every security tool in this package.

One Finding type is used by recon, web probes and the defensive scanners so a
report can mix offensive and defensive results without special-casing. The
severity weights exist so ScanReport.risk_score() is computed in one place
rather than re-derived by each caller.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

    @property
    def weight(self) -> int:
        return _WEIGHTS[self]

    @property
    def rank(self) -> int:
        """0 for critical .. 4 for info, so sorted() puts the worst first."""
        return _ORDER.index(self)


_ORDER: List["Severity"] = [
    Severity.CRITICAL,
    Severity.HIGH,
    Severity.MEDIUM,
    Severity.LOW,
    Severity.INFO,
]

_WEIGHTS: Dict["Severity", int] = {
    Severity.CRITICAL: 40,
    Severity.HIGH: 20,
    Severity.MEDIUM: 8,
    Severity.LOW: 3,
    Severity.INFO: 0,
}


class Confidence(str, Enum):
    """How sure the tool is that a finding is real.

    Active probes are noisy: a single reflected payload is not proof of XSS.
    Keeping this separate from Severity stops a low-confidence hit from being
    reported with the same authority as a confirmed one.
    """

    CONFIRMED = "confirmed"
    FIRM = "firm"
    TENTATIVE = "tentative"


@dataclass
class Finding:
    """One issue, from any tool, with its evidence and its fix."""

    title: str
    severity: Severity
    category: str
    target: str = ""
    description: str = ""
    evidence: str = ""
    remediation: str = ""
    confidence: Confidence = Confidence.FIRM
    references: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "severity": self.severity.value,
            "category": self.category,
            "target": self.target,
            "description": self.description,
            "evidence": self.evidence,
            "remediation": self.remediation,
            "confidence": self.confidence.value,
            "references": list(self.references),
            "metadata": dict(self.metadata),
        }


@dataclass
class ScanReport:
    """Everything one tool run produced, plus the counts a UI needs."""

    tool: str
    target: str = ""
    findings: List[Finding] = field(default_factory=list)
    scanned: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def add(self, finding: Finding) -> None:
        self.findings.append(finding)

    def extend(self, findings: List[Finding]) -> None:
        self.findings.extend(findings)

    def sorted_findings(self) -> List[Finding]:
        return sorted(self.findings, key=lambda f: (f.severity.rank, f.title))

    def counts(self) -> Dict[str, int]:
        out = {s.value: 0 for s in Severity}
        for f in self.findings:
            out[f.severity.value] += 1
        return out

    def risk_score(self) -> int:
        """0-100. Capped so a pile of low findings cannot outrank one critical."""
        raw = sum(f.severity.weight for f in self.findings)
        return min(100, raw)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool": self.tool,
            "target": self.target,
            "risk_score": self.risk_score(),
            "counts": self.counts(),
            "findings": [f.to_dict() for f in self.sorted_findings()],
            "scanned": list(self.scanned),
            "notes": list(self.notes),
            "errors": list(self.errors),
        }

    def summary_line(self) -> str:
        c = self.counts()
        bits = [f"{c[s.value]} {s.value}" for s in Severity if c[s.value]]
        return ", ".join(bits) if bits else "no findings"


def truncate(text: str, limit: int = 400) -> str:
    """Clamp untrusted evidence before it reaches a report or the model."""
    if text is None:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + f"... [+{len(text) - limit} chars]"
