"""Stratification vocabulary for the rule-level disparate-impact audit (A7)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class CaseRecord:
    """One adjudicated case, reduced to exactly what the A7 audit needs:
    which rule(s) fired, which structural stratum the case belongs to, and
    a coarse evidence-strength bucket to condition on."""

    case_id: str
    reason_code: str
    stratum_dimension: str  # e.g. "merchant_tier"
    stratum_value: str  # e.g. "SMALL" | "LARGE"
    evidence_strength_bucket: int
    fired_rule_ids: Tuple[str, ...]
