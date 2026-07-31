"""Chargeback-right gate: is this dispute one the network may charge back at all?

Runs before `arbiter.horn`, never inside it. See `models.py` for why the
condition language is deliberately not a language, and why unknown inputs
fail *open* here while every other gate in this system fails closed.
"""

from .evaluate import (
    BranchFinding,
    EligibilityResult,
    ExclusionFinding,
    FilingWindowFinding,
    evaluate_chargeback_right,
)
from .models import (
    ATTRIBUTE_VOCABULARY,
    AttributeSpec,
    AttrType,
    ChargebackRight,
    Condition,
    Exclusion,
    FilingWindowBranch,
    coerce_attributes,
)

__all__ = [
    "ATTRIBUTE_VOCABULARY",
    "AttrType",
    "AttributeSpec",
    "BranchFinding",
    "ChargebackRight",
    "Condition",
    "EligibilityResult",
    "Exclusion",
    "ExclusionFinding",
    "FilingWindowBranch",
    "FilingWindowFinding",
    "coerce_attributes",
    "evaluate_chargeback_right",
]
