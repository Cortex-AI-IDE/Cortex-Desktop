#!/usr/bin/env python3
"""
policy_compliance_checker.py — Audit deals against commercial policy.

Reads a CSV of deals + a policy YAML; emits per-deal compliance status
(compliant / non-compliant with reason) + aggregate compliance metrics.

Stdlib only. Markdown or JSON output.

Usage:
    python3 policy_compliance_checker.py --deals deals.csv --policy policy.yaml
    python3 policy_compliance_checker.py --deals deals.csv --policy policy.yaml --format json
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, asdict, field
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


# Default policy (overridden by --policy file)
DEFAULT_POLICY = {
    "max_discount_pct_no_approval": {
        "lt_25k": 10,
        "lt_100k": 15,
        "lt_500k": 20,
        "lt_2m": 25,
        "ge_2m": 30,
    },
    "absolute_max_discount_pct": 50,
    "standard_payment_terms_max_days": 30,
    "mfn_requires_approval": True,
    "custom_legal_requires_approval": True,
    "custom_sla_requires_approval": True,
    "max_liability_cap_multiplier": 1.0,
    "max_contract_length_months_no_approval": 12,
}


@dataclass
class Violation:
    deal_id: str
    rule: str
    actual: Any
    threshold: Any
    severity: str
    approval_recorded: bool


@dataclass
class DealCompliance:
    deal_id: str
    compliant: bool
    violations: list[Violation]


def standard_max_discount_for_acv(acv: float, policy: dict[str, Any]) -> float:
    rules = policy.get("max_discount_pct_no_approval", DEFAULT_POLICY["max_discount_pct_no_approval"])
    if acv < 25_000:
        return float(rules.get("lt_25k", 10))
    if acv < 100_000:
        return float(rules.get("lt_100k", 15))
    if acv < 500_000:
        return float(rules.get("lt_500k", 20))
    if acv < 2_000_000:
        return float(rules.get("lt_2m", 25))
    return float(rules.get("ge_2m", 30))


def check_deal(row: dict[str, str], policy: dict[str, Any]) -> DealCompliance:
    violations: list[Violation] = []
    deal_id = row.get("deal_id", "")
    acv = float(row.get("acv", 0) or 0)
    discount = float(row.get("discount_pct", 0) or 0)
    payment_terms_days = int(row.get("payment_terms_days", 30) or 30)
    contract_months = int(row.get("contract_length_months", 12) or 12)
    mfn = row.get("mfn_requested", "").lower() in ("yes", "true", "1")
    custom_legal = row.get("custom_legal_terms", "").lower() in ("yes", "true", "1")
    custom_sla = row.get("custom_sla", "").lower() in ("yes", "true", "1")
    liability_mult = float(row.get("liability_cap_multiplier", 1) or 1)
    approval_recorded = row.get("approval_recorded", "").lower() in ("yes", "true", "1")

    # Discount checks
    max_std = standard_max_discount_for_acv(acv, policy)
    if discount > max_std and not approval_recorded:
        violations.append(Violation(deal_id, "discount_exceeds_standard_without_approval",
                                    discount, max_std, "critical", approval_recorded))
    abs_max = float(policy.get("absolute_max_discount_pct", DEFAULT_POLICY["absolute_max_discount_pct"]))
    if discount > abs_max:
        violations.append(Violation(deal_id, "discount_exceeds_absolute_max",
                                    discount, abs_max, "critical", approval_recorded))

    # Payment terms
    std_payment = int(policy.get("standard_payment_terms_max_days", DEFAULT_POLICY["standard_payment_terms_max_days"]))
    if payment_terms_days > std_payment and not approval_recorded:
        violations.append(Violation(deal_id, "payment_terms_beyond_standard_without_approval",
                                    payment_terms_days, std_payment, "warning", approval_recorded))

    # MFN
    if mfn and policy.get("mfn_requires_approval", True) and not approval_recorded:
        violations.append(Violation(deal_id, "mfn_requested_without_approval",
                                    True, "approval_required", "critical", approval_recorded))

    # Custom legal
    if custom_legal and policy.get("custom_legal_requires_approval", True) and not approval_recorded:
        violations.append(Violation(deal_id, "custom_legal_without_approval",
                                    True, "approval_required", "critical", approval_recorded))

    # Custom SLA
    if custom_sla and policy.get("custom_sla_requires_approval", True) and not approval_recorded:
        violations.append(Violation(deal_id, "custom_sla_without_approval",
                                    True, "approval_required", "warning", approval_recorded))

    # Liability cap
    max_liab = float(policy.get("max_liability_cap_multiplier", DEFAULT_POLICY["max_liability_cap_multiplier"]))
    if liability_mult > max_liab and not approval_recorded:
        violations.append(Violation(deal_id, "liability_cap_exceeds_standard_without_approval",
                                    liability_mult, max_liab, "critical", approval_recorded))

    # Contract length
    max_months = int(policy.get("max_contract_length_months_no_approval", DEFAULT_POLICY["max_contract_length_months_no_approval"]))
    if contract_months > max_months and not approval_recorded:
        violations.append(Violation(deal_id, "contract_length_beyond_standard_without_approval",
                                    contract_months, max_months, "warning", approval_recorded))

    return DealCompliance(
        deal_id=deal_id,
        compliant=not violations,
        violations=violations,
    )


def aggregate(compliances: list[DealCompliance]) -> dict[str, Any]:
    total = len(compliances)
    compliant_count = sum(1 for c in compliances if c.compliant)
    non_compliant = total - compliant_count
    all_violations = [v for c in compliances for v in c.violations]
    by_rule: dict[str, int] = defaultdict(int)
    by_severity: dict[str, int] = defaultdict(int)
    for v in all_violations:
        by_rule[v.rule] += 1
        by_severity[v.severity] += 1
    return {
        "total_deals": total,
        "compliant_count": compliant_count,
        "non_compliant_count": non_compliant,
        "compliance_rate_pct": round(100 * compliant_count / total, 1) if total else 0,
        "violations_by_rule": dict(by_rule),
        "violations_by_severity": dict(by_severity),
    }


def render_markdown(compliances: list[DealCompliance], agg: dict[str, Any]) -> str:
    out = ["# Commercial Policy Compliance Report", ""]
    out.append("## Summary")
    out.append("")
    out.append(f"- **Total deals**: {agg['total_deals']}")
    out.append(f"- **Compliant**: {agg['compliant_count']}")
    out.append(f"- **Non-compliant**: {agg['non_compliant_count']}")
    out.append(f"- **Compliance rate**: {agg['compliance_rate_pct']}%")
    out.append("")
    if agg['violations_by_rule']:
        out.append("## Violations by Rule")
        out.append("")
        out.append("| Rule | Count |")
        out.append("|------|-------|")
        for rule, count in sorted(agg['violations_by_rule'].items(), key=lambda x: -x[1]):
            out.append(f"| {rule} | {count} |")
        out.append("")
    if agg['violations_by_severity']:
        out.append("## Violations by Severity")
        out.append("")
        out.append("| Severity | Count |")
        out.append("|----------|-------|")
        for sev, count in sorted(agg['violations_by_severity'].items()):
            out.append(f"| {sev} | {count} |")
        out.append("")
    non_compliant = [c for c in compliances if not c.compliant]
    if non_compliant:
        out.append("## Non-Compliant Deals (top 50)")
        out.append("")
        out.append("| Deal ID | Violations | Severity |")
        out.append("|---------|------------|----------|")
        for c in non_compliant[:50]:
            sev = "critical" if any(v.severity == "critical" for v in c.violations) else "warning"
            rules = "; ".join(v.rule for v in c.violations)
            out.append(f"| {c.deal_id} | {rules[:80]} | {sev} |")
    return "\n".join(out)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Audit deals against commercial policy",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--deals", required=True, help="CSV of deals")
    p.add_argument("--policy", help="Policy YAML (uses default if omitted)")
    p.add_argument("--format", choices=["markdown", "json"], default="markdown")
    p.add_argument("--output", help="Output file path")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    try:
        with open(args.deals, newline="") as f:
            rows = list(csv.DictReader(f))
    except OSError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    policy = DEFAULT_POLICY
    if args.policy:
        try:
            policy = parse_yaml(Path(args.policy).read_text())
        except OSError as e:
            print(f"error loading policy: {e}", file=sys.stderr)
            return 2
    compliances = [check_deal(r, policy) for r in rows]
    agg = aggregate(compliances)
    if args.format == "json":
        out = json.dumps(
            {"aggregate": agg, "deals": [asdict(c) for c in compliances]},
            indent=2,
            default=str,
        )
    else:
        out = render_markdown(compliances, agg)
    if args.output:
        Path(args.output).write_text(out)
        print(f"wrote {args.output}", file=sys.stderr)
    else:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
