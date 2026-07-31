"""
Rule-Level Disparate Impact Audit (A7).

Model-level fairness metrics can't say *which mechanism* discriminates.
This can: for each rule and each pair of structural strata (merchant size
tier, tenure, MCC, cardmember segment, geography, channel), compare the
rule's firing rate *within the same evidence-strength bucket* -- comparing
like with like, via propensity stratification on evidence strength rather
than a raw marginal comparison, which would conflate "this stratum's cases
tend to have weaker evidence" with "this rule discriminates".

This is a batch job over historical decision events, not a per-case
computation -- it runs over many CaseRecords, not one case's evidence graph.
That is what distinguishes it from arbiter.horn's per-case "symmetric
fairness probe" (PerCaseSymmetryProbe): A7 is population-level, that is
per-case.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .stats import (
    PowerAssessment,
    ProportionCI,
    assess_power,
    benjamini_hochberg,
    two_proportion_p_value,
    wilson_interval,
)
from .strata import CaseRecord


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
    # -- statistical evidence, not just a point estimate ------------------
    ci_a: Optional[ProportionCI] = None
    ci_b: Optional[ProportionCI] = None
    p_value: float = 1.0
    q_value: float = 1.0  # Benjamini-Hochberg FDR-adjusted
    power: Optional[PowerAssessment] = None

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
            "ci_a": self.ci_a.to_dict() if self.ci_a else None,
            "ci_b": self.ci_b.to_dict() if self.ci_b else None,
            "p_value": round(self.p_value, 6),
            "q_value": round(self.q_value, 6),
            "power": self.power.to_dict() if self.power else None,
        }


def compute_rule_level_disparate_impact(
    records: List[CaseRecord],
    all_rule_ids: List[str],
    delta_threshold: float = 0.15,
    min_n_per_cell: int = 30,
    fdr_q: float = 0.05,
) -> List[DisparateImpactFinding]:
    """
    Propensity-stratified firing-rate comparison with real statistics.

    A finding is `flagged` when BOTH hold:

      1. the firing-rate delta is at least `delta_threshold` (practical
         significance -- a statistically detectable 2-point gap is not a
         defect worth a reviewer's time);
      2. the two-proportion test survives Benjamini-Hochberg FDR control at
         `fdr_q` across the whole comparison family (statistical
         significance, corrected for the ~430 simultaneous comparisons this
         audit runs -- uncorrected, dozens of false findings are
         arithmetically guaranteed).

    Previously the only criterion was (1), with `min_n_per_cell = 5` -- so
    a single case in a 5-case cell was a 20-point delta and got reported as
    a discovered defect.

    `power` is REPORTED on every comparison but is deliberately NOT a gate.
    Power analysis answers "should I trust this null result?", not "should I
    believe this significant one" -- an effect that reached significance was
    by definition detectable at the size it was observed, so suppressing it
    for failing an a-priori power calculation against a *smaller* target
    effect is a logical error. (It was one this function made: the planted
    C02_R7 disparity -- delta -0.42, q=0.002 -- was being discarded because
    a 44-vs-33 cell cannot resolve a 0.15 effect, even though it plainly
    resolved a 0.42 one.) Where power matters is the other direction:
    `adequately_powered: false` on an unflagged comparison means "we could
    not tell", which is a different claim from "we checked and found
    nothing", and conflating them is how an audit launders absence of
    evidence into evidence of absence.
    """
    cells: Dict[Tuple[str, int], Dict[str, List[CaseRecord]]] = defaultdict(lambda: defaultdict(list))
    for r in records:
        cells[(r.stratum_dimension, r.evidence_strength_bucket)][r.stratum_value].append(r)

    # Pass 1: compute every comparison, with p-values but no q-values yet --
    # FDR correction is a property of the whole family, so it cannot be
    # applied one comparison at a time.
    raw: List[Tuple[dict, float]] = []
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
                    fired_a = sum(1 for r in group_a if rule_id in r.fired_rule_ids)
                    fired_b = sum(1 for r in group_b if rule_id in r.fired_rule_ids)
                    rate_a, rate_b = fired_a / n_a, fired_b / n_b
                    delta = rate_a - rate_b
                    if abs(delta) < 1e-12:
                        continue  # identical rates -- nothing to test

                    raw.append((
                        {
                            "rule_id": rule_id, "stratum_dimension": dimension,
                            "stratum_a": stratum_a, "stratum_b": stratum_b,
                            "evidence_strength_bucket": bucket,
                            "firing_rate_a": rate_a, "firing_rate_b": rate_b, "delta": delta,
                            "n_a": n_a, "n_b": n_b,
                            "ci_a": wilson_interval(fired_a, n_a),
                            "ci_b": wilson_interval(fired_b, n_b),
                            "power": assess_power(n_a, n_b, target_effect=delta_threshold),
                        },
                        two_proportion_p_value(fired_a, n_a, fired_b, n_b),
                    ))

    # Pass 2: FDR-adjust across the entire family, then flag.
    q_values = benjamini_hochberg([p for _, p in raw])
    findings: List[DisparateImpactFinding] = []
    for (fields, p_value), q_value in zip(raw, q_values, strict=True):
        practically_significant = abs(fields["delta"]) >= delta_threshold
        statistically_significant = q_value <= fdr_q
        findings.append(
            DisparateImpactFinding(
                **fields, p_value=p_value, q_value=q_value,
                flagged=practically_significant and statistically_significant,
            )
        )
    return findings


def inconclusive_only(findings: List[DisparateImpactFinding]) -> List[DisparateImpactFinding]:
    """Comparisons that found nothing but could not have found anything.

    Reporting these separately is the honest counterpart to `flagged_only`:
    an unflagged comparison in an underpowered cell is not evidence that the
    rule is fair, and a fairness dashboard that shows only "0 flagged" would
    let a reviewer conclude it is.
    """
    return [
        f for f in findings
        if not f.flagged and f.power is not None and not f.power.adequately_powered
    ]


def flagged_only(findings: List[DisparateImpactFinding]) -> List[DisparateImpactFinding]:
    return [f for f in findings if f.flagged]
