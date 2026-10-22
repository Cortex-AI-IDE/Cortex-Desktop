#!/usr/bin/env python3
"""
terms_deviation_analyzer.py — Analyze patterns of terms deviation from standard.

Reads a deal CSV; emits which terms most often deviate from standard, by what
magnitude, by which segment, with recommendations for policy adjustment.

Stdlib only. Markdown or JSON output.

Usage:
    python3 terms_deviation_analyzer.py --deals deals.csv
    python3 terms_deviation_analyzer.py --deals deals.csv --format json
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


STANDARD_DISCOUNT_PCT = 0  # standard: no discount
STANDARD_PAYMENT_TERMS_DAYS = 30
STANDARD_CONTRACT_LENGTH_MONTHS = 12
STANDARD_LIABILITY_MULTIPLIER = 1.0


@dataclass
class DeviationPattern:
    term: str
    deviation_count: int
    deviation_rate_pct: float
    median_deviation: float
    p90_deviation: float
    max_deviation: float
    recommendation: str


def analyze(rows: list[dict[str, str]]) -> tuple[list[DeviationPattern], dict[str, Any]]:
    patterns: list[DeviationPattern] = []
    total = len(rows)
    if total == 0:
        return [], {}

    discounts = [float(r.get("discount_pct", 0) or 0) for r in rows]
    payment_days = [int(r.get("payment_terms_days", 30) or 30) for r in rows]
    contract_months = [int(r.get("contract_length_months", 12) or 12) for r in rows]
    liab_mults = [float(r.get("liability_cap_multiplier", 1) or 1) for r in rows]

    # Discount deviation (any > 0)
    discount_devs = [d for d in discounts if d > STANDARD_DISCOUNT_PCT]
    if discount_devs:
        patterns.append(DeviationPattern(
            term="discount_pct",
            deviation_count=len(discount_devs),
            deviation_rate_pct=round(100 * len(discount_devs) / total, 1),
            median_deviation=round(statistics.median(discount_devs), 1),
            p90_deviation=round(percentile(discount_devs, 90), 1),
            max_deviation=round(max(discount_devs), 1),
            recommendation=_discount_recommendation(discount_devs, total),
        ))

    # Payment-terms deviation (> 30)
    payment_devs = [p - STANDARD_PAYMENT_TERMS_DAYS for p in payment_days if p > STANDARD_PAYMENT_TERMS_DAYS]
    if payment_devs:
        patterns.append(DeviationPattern(
            term="payment_terms_days",
            deviation_count=len(payment_devs),
            deviation_rate_pct=round(100 * len(payment_devs) / total, 1),
            median_deviation=statistics.median(payment_devs),
            p90_deviation=percentile(payment_devs, 90),
            max_deviation=max(payment_devs),
            recommendation=_payment_recommendation(payment_devs, total),
        ))

    # Contract length deviation (> 12)
    contract_devs = [c - STANDARD_CONTRACT_LENGTH_MONTHS for c in contract_months if c > STANDARD_CONTRACT_LENGTH_MONTHS]
    if contract_devs:
        patterns.append(DeviationPattern(
            term="contract_length_months",
            deviation_count=len(contract_devs),
            deviation_rate_pct=round(100 * len(contract_devs) / total, 1),
            median_deviation=statistics.median(contract_devs),
            p90_deviation=percentile(contract_devs, 90),
            max_deviation=max(contract_devs),
            recommendation=_contract_recommendation(contract_devs, total),
        ))

    # Liability cap deviation (> 1x)
    liab_devs = [m for m in liab_mults if m > STANDARD_LIABILITY_MULTIPLIER]
    if liab_devs:
        patterns.append(DeviationPattern(
            term="liability_cap_multiplier",
            deviation_count=len(liab_devs),
            deviation_rate_pct=round(100 * len(liab_devs) / total, 1),
            median_deviation=round(statistics.median(liab_devs), 1),
            p90_deviation=round(percentile(liab_devs, 90), 1),
            max_deviation=round(max(liab_devs), 1),
            recommendation=_liab_recommendation(liab_devs, total),
        ))

    # MFN / custom-legal / custom-sla counts
    mfn_count = sum(1 for r in rows if r.get("mfn_requested", "").lower() in ("yes", "true", "1"))
    if mfn_count > 0:
        patterns.append(DeviationPattern(
            term="mfn_requested",
            deviation_count=mfn_count,
            deviation_rate_pct=round(100 * mfn_count / total, 1),
            median_deviation=0,
            p90_deviation=0,
            max_deviation=0,
            recommendation=f"{mfn_count} MFN requests ({round(100*mfn_count/total, 1)}% of deals). Review approval discipline."
            if mfn_count > 5 else "Few MFN requests; manageable.",
        ))

    custom_legal_count = sum(1 for r in rows if r.get("custom_legal_terms", "").lower() in ("yes", "true", "1"))
    if custom_legal_count > 0:
        patterns.append(DeviationPattern(
            term="custom_legal_terms",
            deviation_count=custom_legal_count,
            deviation_rate_pct=round(100 * custom_legal_count / total, 1),
            median_deviation=0,
            p90_deviation=0,
            max_deviation=0,
            recommendation=f"{custom_legal_count} custom-legal requests. Consider adding common ones to pre-approved modifications list."
            if custom_legal_count > 10 else "Manageable volume.",
        ))

    summary = {
        "total_deals": total,
        "deals_with_any_deviation": sum(1 for r in rows if _has_any_deviation(r)),
        "median_discount_pct": round(statistics.median(discounts), 1) if discounts else 0,
        "mean_discount_pct": round(statistics.mean(discounts), 1) if discounts else 0,
    }
    return patterns, summary


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0
    s = sorted(values)
    k = (len(s) - 1) * (p / 100)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def _has_any_deviation(r: dict[str, str]) -> bool:
    return (
        float(r.get("discount_pct", 0) or 0) > 0
        or int(r.get("payment_terms_days", 30) or 30) > STANDARD_PAYMENT_TERMS_DAYS
        or int(r.get("contract_length_months", 12) or 12) != STANDARD_CONTRACT_LENGTH_MONTHS
        or float(r.get("liability_cap_multiplier", 1) or 1) > STANDARD_LIABILITY_MULTIPLIER
        or r.get("mfn_requested", "").lower() in ("yes", "true", "1")
        or r.get("custom_legal_terms", "").lower() in ("yes", "true", "1")
        or r.get("custom_sla", "").lower() in ("yes", "true", "1")
    )


def _discount_recommendation(devs: list[float], total: int) -> str:
    rate = len(devs) / total
    median = statistics.median(devs)
    if rate > 0.7:
        return f"{round(rate*100,1)}% of deals discounted (median {median:.1f}%). List pricing may be off-market; consider price adjustment."
    if rate > 0.3 and median > 25:
        return f"Median discount {median:.1f}% on {round(rate*100,1)}% of deals. Consider tightening discount approval matrix."
    return f"Discount pattern: {round(rate*100,1)}% of deals, median {median:.1f}%. Healthy."


def _payment_recommendation(devs: list[int], total: int) -> str:
    rate = len(devs) / total
    if rate > 0.3:
        return f"{round(rate*100,1)}% of deals beyond Net 30. Consider whether enterprise norms require policy update."
    return f"Payment-term deviations: {round(rate*100,1)}%. Normal."


def _contract_recommendation(devs: list[int], total: int) -> str:
    rate = len(devs) / total
    if rate > 0.4:
        return f"{round(rate*100,1)}% of deals multi-year. Consider whether multi-year should be standard."
    return f"Multi-year rate: {round(rate*100,1)}%. Healthy mix."


def _liab_recommendation(devs: list[float], total: int) -> str:
    rate = len(devs) / total
    if rate > 0.2:
        return f"{round(rate*100,1)}% of deals require liability-cap increase. Consider adding 2x to pre-approved modifications for relevant segments."
    return f"Liability-cap requests: {round(rate*100,1)}%. Manageable."


def render_markdown(patterns: list[DeviationPattern], summary: dict[str, Any]) -> str:
    out = ["# Terms Deviation Analysis", ""]
    out.append(f"_Deals analyzed: {summary.get('total_deals', 0)}_  ")
    out.append(f"_Deals with any deviation: {summary.get('deals_with_any_deviation', 0)} ({round(100*summary.get('deals_with_any_deviation', 0)/summary.get('total_deals', 1), 1)}%)_  ")
    out.append(f"_Median discount: {summary.get('median_discount_pct', 0)}%_  ")
    out.append(f"_Mean discount: {summary.get('mean_discount_pct', 0)}%_")
    out.append("")
    if not patterns:
        out.append("_No significant deviation patterns identified._")
        return "\n".join(out)
    out.append("## Deviation Patterns")
    out.append("")
    out.append("| Term | Count | Rate | Median | p90 | Max | Recommendation |")
    out.append("|------|-------|------|--------|-----|-----|----------------|")
    for p in patterns:
        out.append(f"| {p.term} | {p.deviation_count} | {p.deviation_rate_pct}% | {p.median_deviation} | {p.p90_deviation} | {p.max_deviation} | {p.recommendation[:100]} |")
    return "\n".join(out)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Analyze terms-deviation patterns in deal data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--deals", required=True, help="CSV of deals")
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
    patterns, summary = analyze(rows)
    if args.format == "json":
        out = json.dumps(
            {"summary": summary, "patterns": [asdict(p) for p in patterns]},
            indent=2,
            default=str,
        )
    else:
        out = render_markdown(patterns, summary)
    if args.output:
        Path(args.output).write_text(out)
        print(f"wrote {args.output}", file=sys.stderr)
    else:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
