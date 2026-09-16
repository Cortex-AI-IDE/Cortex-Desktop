"""
report.py -- turns scan findings into a document you can hand to a client.

The rest of this package answers "is there a problem?". This module answers
"what do I send the person who hired me?". A scan dump is not a deliverable,
so the renderer adds the four things a client actually reads for:

  Who      the engagement block: client, assessment reference, assessor and
           the authorization the work was performed under. A report that does
           not name its authorization is a liability, not a deliverable.
  How bad  severity counts and a single risk score, so the reader knows what
           to fix first without reading forty findings.
  Proof    each finding carries where it was found, why it matters and the
           raw evidence, so the reader can reproduce every claim. Findings
           that could not be confirmed are labelled as such rather than
           quietly presented as fact.
  The fix  a concrete remediation per finding, because "you have an SQL
           injection" without "use parameterized queries" is a complaint.

Two output formats, same content: Markdown for a repo or a ticket, and a
self-contained HTML file (inline CSS, no network requests) for emailing.

Nothing here talks to the network and nothing here decides anything about
scope. It renders findings that some other module already produced, which
keeps reporting usable on a code scan that never touched a network.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.core.security_toolkit.finding import Finding, ScanReport, Severity, truncate

# A report is a document, not a database dump. These caps keep a pathological
# scan (a lockfile with 400 CVEs) from producing a file nobody can open.
MAX_FINDINGS_IN_REPORT = 200
EVIDENCE_CHARS = 1200
REPORT_DIR_NAME = "reports"

# Ordered worst-first, which is also the order the reader wants them.
_SEVERITY_ORDER: Tuple[Severity, ...] = (
    Severity.CRITICAL,
    Severity.HIGH,
    Severity.MEDIUM,
    Severity.LOW,
    Severity.INFO,
)

_SEVERITY_LABEL: Dict[str, str] = {
    "critical": "Critical",
    "high": "High",
    "medium": "Medium",
    "low": "Low",
    "info": "Informational",
}

_SEVERITY_COLOR: Dict[str, str] = {
    "critical": "#991b1b",
    "high": "#c2410c",
    "medium": "#a16207",
    "low": "#1d4ed8",
    "info": "#475569",
}


@dataclass
class Engagement:
    """The administrative half of a report.

    Every field is free text and none of them are required, because a report
    about your own project is a valid use and has no client. They are printed
    when present and omitted when empty, so nothing renders as an empty
    heading.
    """

    client: str = ""
    name: str = ""
    assessor: str = ""
    authorization: str = ""
    window: str = ""
    notes: str = ""

    def is_empty(self) -> bool:
        return not any(
            (self.client, self.name, self.assessor, self.authorization,
             self.window, self.notes)
        )


@dataclass
class ReportBundle:
    """Everything merged from one or more ScanReports, ready to render."""

    engagement: Engagement = field(default_factory=Engagement)
    findings: List[Finding] = field(default_factory=list)
    scanned: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    tools: List[str] = field(default_factory=list)
    targets: List[str] = field(default_factory=list)
    generated: str = ""
    truncated: bool = False

    def counts(self) -> Dict[str, int]:
        out = {s.value: 0 for s in Severity}
        for f in self.findings:
            out[f.severity.value] += 1
        return out

    def risk_score(self) -> int:
        raw = sum(f.severity.weight for f in self.findings)
        return min(100, raw)

    def by_severity(self) -> List[Tuple[Severity, List[Finding]]]:
        groups: List[Tuple[Severity, List[Finding]]] = []
        for sev in _SEVERITY_ORDER:
            bucket = sorted(
                (f for f in self.findings if f.severity is sev),
                key=lambda f: f.title,
            )
            if bucket:
                groups.append((sev, bucket))
        return groups

    def actionable(self) -> List[Finding]:
        return [f for f in self.findings if f.severity is not Severity.INFO]


def build_bundle(
    reports: Sequence[ScanReport],
    engagement: Optional[Engagement] = None,
    generated: Optional[str] = None,
) -> ReportBundle:
    """Merge several tool runs into one report.

    A single engagement normally spans recon, port scanning, probes and a code
    sweep. Rendering them as four separate documents would make the client
    reconcile them by hand, so they are merged and sorted by severity instead.
    """
    bundle = ReportBundle(engagement=engagement or Engagement())
    bundle.generated = generated or _now()
    seen: set = set()

    for report in reports:
        for finding in report.findings:
            # Two probes can report the same issue on the same target (a
            # header found by both the TLS and the header check). Deduplicate
            # on the identity of the finding so the client does not read the
            # same paragraph twice and question the count.
            key = (
                finding.title.strip().lower(),
                finding.target.strip().lower(),
                finding.category.strip().lower(),
            )
            if key in seen:
                continue
            seen.add(key)
            bundle.findings.append(finding)

        for value, sink in (
            (report.scanned, bundle.scanned),
            (report.notes, bundle.notes),
            (report.errors, bundle.errors),
            ([report.tool], bundle.tools),
            ([report.target], bundle.targets),
        ):
            for item in value:
                if item and item not in sink:
                    sink.append(item)

    bundle.findings.sort(key=lambda f: (f.severity.rank, f.title))
    if len(bundle.findings) > MAX_FINDINGS_IN_REPORT:
        bundle.findings = bundle.findings[:MAX_FINDINGS_IN_REPORT]
        bundle.truncated = True
    return bundle


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def suggest_filename(engagement: Engagement, generated: str = "") -> str:
    """`2026-09-16-acme-corp-security-report.md`-ish, without the extension."""
    stamp = (generated or _now())[:10]
    label = engagement.client or engagement.name or "security"
    slug = "".join(
        ch if ch.isalnum() else "-" for ch in label.strip().lower()
    )
    while "--" in slug:
        slug = slug.replace("--", "-")
    slug = slug.strip("-") or "security"
    return f"{stamp}-{slug}-report"


def write_report(
    reports: Sequence[ScanReport],
    output_dir: str,
    engagement: Optional[Engagement] = None,
    fmt: str = "markdown",
    generated: Optional[str] = None,
) -> Path:
    """Render and write one report. Returns the path written.

    `output_dir` is created if missing. The caller decides where it goes, so
    this function has no opinion about project layout and never writes outside
    the directory it was handed.
    """
    b = build_bundle(reports, engagement, generated)
    fmt = (fmt or "markdown").strip().lower()
    if fmt in ("md", "markdown"):
        body, ext = render_markdown(b, engagement), ".md"
    elif fmt in ("html", "htm"):
        body, ext = render_html(b, engagement), ".html"
    else:
        raise ValueError(f"unsupported report format '{fmt}' (use markdown or html)")

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / (suggest_filename(engagement or Engagement(), b.generated) + ext)
    path.write_text(body, encoding="utf-8", newline="\n")
    return path


# --------------------------------------------------------------------- markdown


def _fence(text: str) -> str:
    """A code fence guaranteed not to be closed early by the evidence itself.

    Evidence is raw target output. If it happens to contain a ``` run (a
    markdown file we scanned, a response body echoing our own payload) a
    fixed three-backtick fence would end the block mid-evidence and the rest
    of the report would render as prose.
    """
    longest = 0
    run = 0
    for ch in text:
        run = run + 1 if ch == "`" else 0
        longest = max(longest, run)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}\n{text}\n{ticks}"


def _md_table(rows: Sequence[Sequence[str]], headers: Sequence[str]) -> List[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(" --- " for _ in headers) + "|",
    ]
    for row in rows:
        cells = [str(c).replace("|", "\\|").replace("\n", " ") for c in row]
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def render_markdown(bundle: ReportBundle, engagement: Optional[Engagement] = None) -> str:
    """The report as Markdown: readable in a repo, a ticket or an email."""
    eng = engagement or bundle.engagement
    counts = bundle.counts()
    lines: List[str] = []
    title = eng.name or "Security Assessment Report"
    lines += [f"# {title}", ""]

    meta: List[Sequence[str]] = []
    if eng.client:
        meta.append(("Client", eng.client))
    meta.append(("Assessment", eng.name or "Security assessment"))
    if eng.assessor:
        meta.append(("Assessor", eng.assessor))
    if eng.authorization:
        meta.append(("Authorization", eng.authorization))
    if eng.window:
        meta.append(("Testing window", eng.window))
    meta.append(("Report generated", bundle.generated))
    meta.append(("Produced by", "Cortex security toolkit"))
    lines += _md_table(meta, ("Field", "Value"))
    lines.append("")

    # ---------------------------------------------------------- exec summary
    lines += ["## Executive summary", ""]
    if bundle.actionable():
        lines += [
            f"{len(bundle.actionable())} issue(s) require attention, "
            f"{counts['critical']} of them critical. "
            f"Overall risk score: **{bundle.risk_score()}/100**.",
            "",
        ]
    else:
        lines += [
            "No actionable issues were found by the checks that ran. "
            "See *Method and limitations* below for what that does and does "
            "not cover.",
            "",
        ]
    lines += _md_table(
        [
            (_SEVERITY_LABEL[s.value], str(counts[s.value]))
            for s in _SEVERITY_ORDER
        ],
        ("Severity", "Count"),
    )
    lines.append("")

    top = bundle.actionable()[:3]
    if top:
        lines += ["### Fix these first", ""]
        for i, f in enumerate(top, 1):
            lines.append(
                f"{i}. **{f.title}** ({_SEVERITY_LABEL[f.severity.value]}) "
                f"on `{f.target or 'n/a'}`"
            )
        lines.append("")

    # ----------------------------------------------------- scope and method
    lines += ["## Scope and authorization", ""]
    if eng.authorization:
        lines += [
            f"Testing was performed under: {eng.authorization}.",
            "",
        ]
    if bundle.targets:
        lines += _md_table(
            [(t,) for t in bundle.targets], ("Target",)
        )
        lines.append("")
    if eng.notes:
        lines += [eng.notes, ""]
    if bundle.tools:
        lines += [
            "Checks performed: " + ", ".join(f"`{t}`" for t in bundle.tools) + ".",
            "",
        ]

    # ------------------------------------------------------------- findings
    lines += ["## Findings", ""]
    groups = bundle.by_severity()
    if not groups:
        lines += ["No findings. Nothing to remediate from this assessment.", ""]
    index = 0
    for severity, bucket in groups:
        lines += [f"### {_SEVERITY_LABEL[severity.value]}", ""]
        for finding in bucket:
            index += 1
            lines += _finding_markdown(finding, index)
    if bundle.truncated:
        lines += [
            f"> Report truncated at {MAX_FINDINGS_IN_REPORT} findings. "
            "Re-run the specific check for the full list.",
            "",
        ]

    # --------------------------------------------------- method and limits
    lines += ["## Method and limitations", ""]
    lines += [
        "- Checks are **non-destructive detection**. A probe proves that an "
        "issue is present and then stops; no data was extracted, altered or "
        "deleted, and no access was escalated.",
        "- Each finding states its confidence. `confirmed` means the tool "
        "observed the issue directly. `tentative` means the response was "
        "consistent with the issue but could be a false positive and should "
        "be verified by hand before it is treated as fact.",
        "- A finding with no reproduction is a starting point, not a "
        "conclusion. Verify anything you intend to act on or publish.",
        "- Absence of a finding is not proof of absence. This covers the "
        "checks listed above and no others.",
        "",
    ]

    # -------------------------------------------------------------- appendix
    if bundle.scanned:
        lines += ["## Appendix A: what was tested", ""]
        lines += [f"- `{item}`" for item in bundle.scanned[:400]]
        lines.append("")
    if bundle.notes:
        lines += ["## Appendix B: scan notes", ""]
        lines += [f"- {n}" for n in bundle.notes[:60]]
        lines.append("")
    if bundle.errors:
        lines += ["## Appendix C: checks that could not complete", ""]
        lines += [f"- {e}" for e in bundle.errors[:60]]
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _finding_markdown(finding: Finding, index: int) -> List[str]:
    fid = f"F-{index:02d}"
    out = [
        f"#### {fid}: {finding.title}",
        "",
    ]
    rows: List[Sequence[str]] = [
        ("Severity", _SEVERITY_LABEL[finding.severity.value]),
        ("Confidence", finding.confidence.value),
        ("Category", finding.category or "uncategorised"),
        ("Location", finding.target or "n/a"),
    ]
    out += _md_table(rows, ("Field", "Detail"))
    out.append("")
    if finding.description:
        out += [finding.description.strip(), ""]
    if finding.evidence:
        out += ["**Evidence**", "", _fence(truncate(finding.evidence, EVIDENCE_CHARS)), ""]
    if finding.remediation:
        out += [f"**Remediation.** {finding.remediation.strip()}", ""]
    if finding.references:
        out += ["**References**", ""]
        out += [f"- {r}" for r in finding.references[:10]]
        out.append("")
    return out


# ------------------------------------------------------------------------- html

def _e(text: Any) -> str:
    return html.escape(str(text if text is not None else ""), quote=True)


_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0; background: #f1f5f9; color: #0f172a;
  font: 15px/1.6 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
main { max-width: 940px; margin: 0 auto; padding: 40px 24px 80px; }
header { border-bottom: 3px solid #0f172a; padding-bottom: 16px; margin-bottom: 28px; }
h1 { font-size: 27px; margin: 0 0 6px; letter-spacing: -0.01em; }
h2 { font-size: 19px; margin: 38px 0 12px; padding-bottom: 6px;
  border-bottom: 1px solid #cbd5e1; }
h3 { font-size: 15px; text-transform: uppercase; letter-spacing: 0.08em;
  margin: 26px 0 10px; color: #475569; }
h4 { font-size: 16px; margin: 22px 0 8px; }
p, li { margin: 8px 0; }
code, pre { font-family: ui-monospace, Consolas, "Courier New", monospace; }
code { background: #e2e8f0; padding: 1px 5px; border-radius: 3px; font-size: 13px; }
pre { background: #0f172a; color: #e2e8f0; padding: 12px 14px; border-radius: 6px;
  overflow-x: auto; font-size: 12.5px; white-space: pre-wrap; word-break: break-word; }
table { border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 14px; }
th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid #e2e8f0;
  vertical-align: top; }
th { background: #f8fafc; font-weight: 600; }
.card { background: #fff; border: 1px solid #e2e8f0; border-radius: 8px;
  padding: 18px 20px; margin: 14px 0; }
.pill { display: inline-block; padding: 2px 9px; border-radius: 999px;
  font-size: 11.5px; font-weight: 700; color: #fff; letter-spacing: 0.04em; }
.fid { color: #64748b; font-weight: 600; }
.score { font-size: 40px; font-weight: 700; letter-spacing: -0.02em; }
.muted { color: #64748b; font-size: 13px; }
.warn { background: #fffbeb; border: 1px solid #fde68a; border-radius: 8px;
  padding: 12px 16px; }
"""


def render_html(bundle: ReportBundle, engagement: Optional[Engagement] = None) -> str:
    """The report as one self-contained HTML file.

    Inline CSS and no external requests, so it opens and renders identically
    on a machine with no network and does not silently phone home when a
    client opens it.
    """
    eng = engagement or bundle.engagement
    counts = bundle.counts()
    title = eng.name or "Security Assessment Report"

    meta_rows = ""
    for label, value in (
        ("Client", eng.client),
        ("Assessor", eng.assessor),
        ("Authorization", eng.authorization),
        ("Testing window", eng.window),
        ("Report generated", bundle.generated),
        ("Produced by", "Cortex security toolkit"),
    ):
        if value:
            meta_rows += f"<tr><th>{_e(label)}</th><td>{_e(value)}</td></tr>"

    count_rows = "".join(
        f'<tr><td><span class="pill" style="background:'
        f'{_SEVERITY_COLOR[s.value]}">{_e(_SEVERITY_LABEL[s.value])}</span></td>'
        f"<td>{counts[s.value]}</td></tr>"
        for s in _SEVERITY_ORDER
    )

    parts: List[str] = []
    parts.append(
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>{_e(title)}</title><style>{_CSS}</style></head><body><main>"
    )
    parts.append(
        f'<header><h1>{_e(title)}</h1>'
        f'<p class="muted">Generated {_e(bundle.generated)} by the Cortex '
        f"security toolkit</p></header>"
    )

    if meta_rows:
        parts.append(f"<h2>Engagement</h2><table>{meta_rows}</table>")

    # executive summary
    score = bundle.risk_score()
    n_actionable = len(bundle.actionable())
    lead = (
        f"{n_actionable} issue(s) require attention, {counts['critical']} of "
        f"them critical."
        if n_actionable
        else "No actionable issues were found by the checks that ran."
    )
    parts.append(
        f'<h2>Executive summary</h2><div class="card">'
        f'<p class="score">{score}<span class="muted">/100 risk</span></p>'
        f"<p>{_e(lead)}</p></div>"
        f"<table><tr><th>Severity</th><th>Count</th></tr>{count_rows}</table>"
    )

    top = bundle.actionable()[:3]
    if top:
        items = "".join(
            f"<li><strong>{_e(f.title)}</strong> "
            f"({_e(_SEVERITY_LABEL[f.severity.value])}) on "
            f"<code>{_e(f.target or 'n/a')}</code></li>"
            for f in top
        )
        parts.append(f"<h3>Fix these first</h3><ol>{items}</ol>")

    if bundle.truncated:
        parts.append(
            f'<p class="warn">Report truncated at {MAX_FINDINGS_IN_REPORT} '
            f"findings.</p>"
        )

    # scope
    scope_bits = []
    if eng.authorization:
        scope_bits.append(f"<p>Testing was performed under: {_e(eng.authorization)}.</p>")
    if bundle.targets:
        rows = "".join(f"<tr><td><code>{_e(t)}</code></td></tr>" for t in bundle.targets)
        scope_bits.append(f"<table><tr><th>Target</th></tr>{rows}</table>")
    if eng.notes:
        scope_bits.append(f"<p>{_e(eng.notes)}</p>")
    if bundle.tools:
        scope_bits.append(
            "<p>Checks performed: "
            + ", ".join(f"<code>{_e(t)}</code>" for t in bundle.tools)
            + ".</p>"
        )
    if scope_bits:
        parts.append("<h2>Scope and authorization</h2>" + "".join(scope_bits))

    # findings
    parts.append("<h2>Findings</h2>")
    groups = bundle.by_severity()
    if not groups:
        parts.append("<p>No findings.</p>")
    index = 0
    for severity, bucket in groups:
        parts.append(f"<h3>{_e(_SEVERITY_LABEL[severity.value])}</h3>")
        for finding in bucket:
            index += 1
            parts.append(_finding_html(finding, index))

    # method
    parts.append(
        "<h2>Method and limitations</h2><ul>"
        "<li>Checks are <strong>non-destructive detection</strong>. No data "
        "was extracted, altered or deleted, and no access was escalated.</li>"
        "<li>Every finding states its confidence. <code>tentative</code> "
        "means the response was consistent with the issue but may be a false "
        "positive, and should be verified before being treated as fact.</li>"
        "<li>Absence of a finding is not proof of absence.</li>"
        "</ul>"
    )

    if bundle.scanned:
        rows = "".join(f"<tr><td><code>{_e(i)}</code></td></tr>" for i in bundle.scanned[:400])
        parts.append(f"<h2>Appendix A: what was tested</h2><table>{rows}</table>")
    if bundle.notes:
        items = "".join(f"<li>{_e(n)}</li>" for n in bundle.notes[:60])
        parts.append(f"<h2>Appendix B: scan notes</h2><ul>{items}</ul>")
    if bundle.errors:
        items = "".join(f"<li>{_e(e)}</li>" for e in bundle.errors[:60])
        parts.append(
            f'<h2>Appendix C: checks that could not complete</h2>'
            f'<ul class="warn">{items}</ul>'
        )

    parts.append("</main></body></html>")
    return "\n".join(parts)


def _finding_html(finding: Finding, index: int) -> str:
    color = _SEVERITY_COLOR[finding.severity.value]
    bits = [
        f'<div class="card"><h4><span class="fid">F-{index:02d}</span> '
        f'{_e(finding.title)}</h4>',
        f'<p><span class="pill" style="background:{color}">'
        f"{_e(_SEVERITY_LABEL[finding.severity.value])}</span> "
        f'<span class="muted">confidence: {_e(finding.confidence.value)} | '
        f"category: {_e(finding.category or 'uncategorised')} | "
        f"location: {_e(finding.target or 'n/a')}</span></p>",
    ]
    if finding.description:
        bits.append(f"<p>{_e(finding.description.strip())}</p>")
    if finding.evidence:
        bits.append(
            f"<p><strong>Evidence</strong></p>"
            f"<pre>{_e(truncate(finding.evidence, EVIDENCE_CHARS))}</pre>"
        )
    if finding.remediation:
        bits.append(
            f"<p><strong>Remediation.</strong> {_e(finding.remediation.strip())}</p>"
        )
    if finding.references:
        items = "".join(f"<li>{_e(r)}</li>" for r in finding.references[:10])
        bits.append(f"<p><strong>References</strong></p><ul>{items}</ul>")
    bits.append("</div>")
    return "".join(bits)
