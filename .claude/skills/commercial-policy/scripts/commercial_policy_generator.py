#!/usr/bin/env python3
"""
commercial_policy_generator.py — Generate a tailored commercial policy document.

Reads an org spec YAML (stage, ICP, region, regulatory context); emits a
tailored policy charter as markdown.

Stdlib only. Markdown or JSON output.

Usage:
    python3 commercial_policy_generator.py --org-spec org.yaml
    python3 commercial_policy_generator.py --org-spec org.yaml --region EU --format json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def parse_yaml(text: str) -> dict[str, Any]:
    lines: list[tuple[int, str]] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        lines.append((indent, line[indent:]))
    result, _ = _parse_block(lines, 0, 0)
    return result if isinstance(result, dict) else {}


def _parse_block(lines, idx, indent):
    if idx >= len(lines):
        return None, idx
    first_indent = lines[idx][0]
    if first_indent < indent:
        return None, idx
    first_line = lines[idx][1]
    if first_line.startswith("- "):
        return _parse_seq(lines, idx, first_indent)
    return _parse_map(lines, idx, first_indent)


def _parse_map(lines, idx, indent):
    out: dict[str, Any] = {}
    while idx < len(lines):
        cur_indent, content = lines[idx]
        if cur_indent < indent:
            break
        if cur_indent > indent:
            idx += 1
            continue
        if ":" not in content:
            idx += 1
            continue
        key, _, rest = content.partition(":")
        key = key.strip().strip('"').strip("'")
        rest = rest.strip()
        if rest:
            out[key] = _scalar(rest)
            idx += 1
        else:
            idx += 1
            if idx < len(lines) and lines[idx][0] > indent:
                value, idx = _parse_block(lines, idx, lines[idx][0])
                out[key] = value if value is not None else {}
            else:
                out[key] = {}
    return out, idx


def _parse_seq(lines, idx, indent):
    out: list[Any] = []
    while idx < len(lines):
        cur_indent, content = lines[idx]
        if cur_indent < indent:
            break
        if not content.startswith("- "):
            break
        rest = content[2:].strip()
        if not rest:
            idx += 1
            if idx < len(lines) and lines[idx][0] > indent:
                value, idx = _parse_block(lines, idx, lines[idx][0])
                out.append(value if value is not None else {})
            else:
                out.append(None)
        elif ":" in rest:
            synth = [(indent + 2, rest)]
            j = idx + 1
            while j < len(lines) and lines[j][0] > indent:
                synth.append(lines[j])
                j += 1
            value, _ = _parse_map(synth, 0, indent + 2)
            out.append(value)
            idx = j
        else:
            out.append(_scalar(rest))
            idx += 1
    return out, idx


def _scalar(s: str):
    s = s.strip()
    if s.startswith('"') and s.endswith('"'):
        return s[1:-1]
    if s.startswith("'") and s.endswith("'"):
        return s[1:-1]
    if s.lower() in ("true", "yes"):
        return True
    if s.lower() in ("false", "no"):
        return False
    if s.lower() in ("null", "~", ""):
        return None
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


@dataclass
class PolicyDocument:
    company_name: str
    effective_date: str
    stage: str
    icp: str
    region: str
    discount_thresholds: dict[str, int]
    absolute_max_discount_pct: int
    payment_terms_std: str
    contract_length_std_months: int
    liability_cap_standard: str
    region_overlay: list[str] = field(default_factory=list)
    regulated_overlay: list[str] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)


def design_policy(org: dict[str, Any], region: str | None) -> PolicyDocument:
    name = org.get("company_name", "Unnamed Co")
    stage = org.get("stage", "growth")  # seed / growth / scale / mature
    icp = (org.get("icp_segment") or "mid-market").lower()
    company_region = (region or org.get("region") or "US").upper()
    regulated = bool(org.get("regulated_industry", False))

    # Discount thresholds by stage + ICP
    if stage == "seed" or icp == "smb":
        discount_thresholds = {"<25k": 10, "<100k": 15, "<500k": 20, "<2M": 25, ">=2M": 30}
        abs_max = 40
    elif icp == "enterprise":
        discount_thresholds = {"<25k": 10, "<100k": 15, "<500k": 20, "<2M": 25, ">=2M": 30}
        abs_max = 50
    else:  # mid-market default
        discount_thresholds = {"<25k": 10, "<100k": 15, "<500k": 20, "<2M": 25, ">=2M": 30}
        abs_max = 50

    # Payment terms
    if icp == "enterprise":
        payment_terms = "Net 30 standard; Net 60 commonly accepted"
    elif icp == "smb":
        payment_terms = "Net 30 standard; annual prepay preferred"
    else:
        payment_terms = "Net 30 standard"

    # Contract length
    contract_length = 12

    # Liability cap
    if regulated:
        liability_cap = "2x annual fees (regulated industry exposure justifies higher)"
    else:
        liability_cap = "1x annual fees standard"

    # Region overlay
    region_overlay: list[str] = []
    if company_region == "EU":
        region_overlay.extend([
            "GDPR Data Processing Addendum (DPA) standard for all customers",
            "Data residency in EU upon customer request (per supported regions)",
            "EU customer jurisdiction often required; standard governing law per contract",
            "VAT typically reverse-charged for B2B (verify per country)",
        ])
    elif company_region == "UK":
        region_overlay.extend([
            "UK GDPR / Data Protection Act compliance",
            "England & Wales law and jurisdiction standard",
            "VAT separate from EU post-Brexit",
        ])
    elif company_region == "APAC":
        region_overlay.extend([
            "Singapore or Hong Kong jurisdiction common for regional",
            "Country-specific data-residency laws (review per country)",
            "GST / VAT per country",
        ])
    elif company_region == "US":
        region_overlay.extend([
            "Delaware governing law common for B2B",
            "State sales tax (Wayfair Decision; review per state)",
        ])

    # Regulated overlay
    regulated_overlay: list[str] = []
    if regulated:
        regulated_overlay.extend([
            "Industry-specific regulatory requirements (HIPAA, PCI-DSS, SOC 2, etc.) per customer industry",
            "Customer audit rights commonly required",
            "Custom security questionnaire response (standardize top 100 questions)",
            "Source code escrow may be required for OEM / strategic deals",
        ])

    return PolicyDocument(
        company_name=name,
        effective_date=datetime.now(timezone.utc).date().isoformat(),
        stage=stage,
        icp=icp,
        region=company_region,
        discount_thresholds=discount_thresholds,
        absolute_max_discount_pct=abs_max,
        payment_terms_std=payment_terms,
        contract_length_std_months=contract_length,
        liability_cap_standard=liability_cap,
        region_overlay=region_overlay,
        regulated_overlay=regulated_overlay,
        metadata={"generated_at": datetime.now(timezone.utc).isoformat()},
    )


def render_markdown(p: PolicyDocument) -> str:
    out: list[str] = []
    out.append(f"# Commercial Policy: {p.company_name}")
    out.append("")
    out.append(f"_Effective: {p.effective_date} | Stage: {p.stage} | ICP: {p.icp} | Region: {p.region}_")
    out.append("")
    out.append("## Purpose")
    out.append("")
    out.append(f"This Commercial Policy defines the rules that govern commercial terms offered to customers by {p.company_name}. It is binding on all customer-facing functions and enforced by Deal Desk. Compliance is mandatory; exceptions require named-approver sign-off per the approval matrix.")
    out.append("")
    out.append("## Scope")
    out.append("")
    out.append("Applies to:")
    out.append("- All new customer agreements")
    out.append("- All renewals with material changes")
    out.append("- All partner-mediated deals")
    out.append("- All custom / non-standard terms")
    out.append("")
    out.append("Out of scope:")
    out.append("- Self-serve / PLG transactions per published terms")
    out.append("- Auto-renewals at standard terms")
    out.append("")
    out.append("## Owners")
    out.append("")
    out.append("- Policy owners: CRO + CFO + General Counsel (jointly)")
    out.append("- Operational enforcement: Deal Desk")
    out.append("- Review cadence: annual; material changes communicated to sales")
    out.append("")
    out.append("## Pricing Policy")
    out.append("")
    out.append("### Discount thresholds (max without approval)")
    out.append("")
    out.append("| ACV bracket | Max standard discount |")
    out.append("|-------------|----------------------|")
    for k, v in p.discount_thresholds.items():
        out.append(f"| {k} ACV | {v}% |")
    out.append("")
    out.append(f"Absolute maximum discount: **{p.absolute_max_discount_pct}%** (beyond requires CEO + Board awareness)")
    out.append("")
    out.append("### MFN")
    out.append("")
    out.append("Not granted by default. If granted: strategic-tier customer + CRO + CFO + GC approval; scoped narrowly (same product, same volume, same term, same geo); disclosure-only.")
    out.append("")
    out.append("## Contract Policy")
    out.append("")
    out.append(f"- **Standard term**: {p.contract_length_std_months} months")
    out.append(f"- **Standard payment terms**: {p.payment_terms_std}")
    out.append("- **Auto-renew with 90-day notice**")
    out.append("")
    out.append("## Legal Policy")
    out.append("")
    out.append(f"- **Liability cap**: {p.liability_cap_standard}")
    out.append("- **Indemnification**: standard mutual per template")
    out.append("- **MSA modifications**: per pre-approved-modifications list; custom requires GC approval")
    out.append("")
    if p.region_overlay:
        out.append(f"## Regional Overlay: {p.region}")
        out.append("")
        for item in p.region_overlay:
            out.append(f"- {item}")
        out.append("")
    if p.regulated_overlay:
        out.append("## Regulated-Industry Overlay")
        out.append("")
        for item in p.regulated_overlay:
            out.append(f"- {item}")
        out.append("")
    out.append("## Operational Policy")
    out.append("")
    out.append("- **SLA tiers**: Standard, Enhanced, Custom — Custom requires CS + Engineering approval")
    out.append("- **Security commitments**: standard SOC 2 / ISO 27001 per template; custom requires CISO + GC")
    out.append("- **Dedicated infrastructure**: not standard; CTO + GC approval; premium pricing")
    out.append("")
    out.append("## Approval Matrix")
    out.append("")
    out.append("See Deal Desk approval matrix (mirrors this policy). Deviations route per matrix.")
    out.append("")
    out.append("## Documentation")
    out.append("")
    out.append("Every non-standard deal documented per Deal Desk packet template. Auditable.")
    out.append("")
    out.append("---")
    out.append("")
    out.append(f"_Generated: {p.metadata['generated_at']}_")
    return "\n".join(out)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate a tailored commercial policy document",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--org-spec", required=True, help="Org spec YAML")
    p.add_argument("--region", help="Region overlay (US, EU, UK, APAC)")
    p.add_argument("--format", choices=["markdown", "json"], default="markdown")
    p.add_argument("--output", help="Output file path")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    try:
        org = parse_yaml(Path(args.org_spec).read_text())
    except OSError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    doc = design_policy(org, args.region)
    if args.format == "json":
        out = json.dumps(asdict(doc), indent=2, default=str)
    else:
        out = render_markdown(doc)
    if args.output:
        Path(args.output).write_text(out)
        print(f"wrote {args.output}", file=sys.stderr)
    else:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
