"""
Rule-Level Disparate Impact Audit (§A7).

Model-level fairness metrics can't say *which mechanism* discriminates.
This can: for each rule and each pair of structural strata (merchant size
tier, tenure, MCC, cardmember segment, geography, channel), compare the
rule's firing rate *within the same evidence-strength bucket* -- comparing
like with like, via propensity stratification on evidence strength rather
than a raw marginal comparison, which would conflate "this stratum's cases
tend to have weaker evidence" with "this rule discriminates". A firing-rate
delta that survives that conditioning is the doc's own example, verified
here as an actual computation: "a rule that fires 3x more often against
small merchants at equal evidence strength is a discovered defect with a
line number."

This is a batch job over historical decision events (§9.6: `fairness-service`,
Python batch, CronJob + stream), not a per-case computation -- it runs over
many `CaseRecord`s, not one case's evidence graph. That is what distinguishes
it from services/counterfactual's per-case "symmetric fairness probe"
(§5.1 idea 14): A7 is population-level, that is per-case.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Tuple


@dataclass(frozen=True)
class CaseRecord:
    """One adjudicated case, reduced to exactly what this audit needs:
    which rule(s) fired, which structural stratum the case belongs to, and
    a coarse evidence-strength bucket to condition on."""

    case_id: str
    reason_code: str
    stratum_dimension: str  # e.g. "merchant_tier"
    stratum_value: str  # e.g. "SMALL" | "LARGE"
    evidence_strength_bucket: int
    fired_rule_ids: Tuple[str, ...]


@dataclass(frozen=True)
class DisparateImpactFinding:
    rule_id: str
    stratum_dimension: str
    stratum_a: str
    stratum_b: str
    evidence_strength_bucket: int
    firing_rate_a: float
    firing_rate_b: float
    delta: float
    n_a: int
    n_b: int
    flagged: bool

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "stratum_dimension": self.stratum_dimension,
            "stratum_a": self.stratum_a,
            "stratum_b": self.stratum_b,
            "evidence_strength_bucket": self.evidence_strength_bucket,
            "firing_rate_a": round(self.firing_rate_a, 4),
            "firing_rate_b": round(self.firing_rate_b, 4),
            "delta": round(self.delta, 4),
            "n_a": self.n_a,
            "n_b": self.n_b,
            "flagged": self.flagged,
        }


def compute_rule_level_disparate_impact(
    records: List[CaseRecord],
    all_rule_ids: List[str],
    delta_threshold: float = 0.15,
    min_n_per_cell: int = 5,
) -> List[DisparateImpactFinding]:
    """
    Propensity-stratified firing-rate comparison. Cells with fewer than
    `min_n_per_cell` cases on either side are skipped rather than flagged --
    a large delta on 2 cases is noise, not a discovered defect, and a
    real audit must say so rather than report a spurious finding.
    """
    # (stratum_dimension, evidence_bucket) -> stratum_value -> [records]
    cells: Dict[Tuple[str, int], Dict[str, List[CaseRecord]]] = defaultdict(lambda: defaultdict(list))
    for r in records:
        cells[(r.stratum_dimension, r.evidence_strength_bucket)][r.stratum_value].append(r)

    findings: List[DisparateImpactFinding] = []
    for (dimension, bucket), by_value in cells.items():
        values = sorted(by_value.keys())
        for i in range(len(values)):
            for j in range(i + 1, len(values)):
                stratum_a, stratum_b = values[i], values[j]
                group_a, group_b = by_value[stratum_a], by_value[stratum_b]
                n_a, n_b = len(group_a), len(group_b)
                if n_a < min_n_per_cell or n_b < min_n_per_cell:
                    continue

                for rule_id in all_rule_ids:
                    rate_a = sum(1 for r in group_a if rule_id in r.fired_rule_ids) / n_a
                    rate_b = sum(1 for r in group_b if rule_id in r.fired_rule_ids) / n_b
                    delta = rate_a - rate_b
                    if abs(delta) < 1e-12 and delta_threshold > 0:
                        continue  # no difference at all -- not a finding, just noise floor
                    findings.append(
                        DisparateImpactFinding(
                            rule_id=rule_id, stratum_dimension=dimension,
                            stratum_a=stratum_a, stratum_b=stratum_b,
                            evidence_strength_bucket=bucket,
                            firing_rate_a=rate_a, firing_rate_b=rate_b, delta=delta,
                            n_a=n_a, n_b=n_b, flagged=abs(delta) >= delta_threshold,
                        )
                    )
    return findings


def flagged_only(findings: List[DisparateImpactFinding]) -> List[DisparateImpactFinding]:
    return [f for f in findings if f.flagged]
