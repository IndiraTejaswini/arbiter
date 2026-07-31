"""
Audit sampling — the fix for calibration selection bias.

The defect this exists to correct, stated precisely:

    Analyst reviews only ever came from ESCALATED cases. Those are, by
    construction, the high-nonconformity tail. Feeding only that tail back
    into the split-conformal calibration pool inflates the (1-alpha)
    quantile monotonically, so **the more human review you do, the more
    permissive the gate becomes** — the exact opposite of what a reviewer
    doing more work should cause.

Split-conformal validity requires the calibration set be *exchangeable*
with what the gate will see in deployment. A review-only pool is not
exchangeable with the deployment distribution; it is a biased subsample of
it, and no amount of data fixes a biased sample.

Two mechanisms, both needed:

1. **Sample auto-resolved cases for audit.** A fraction of cases the gate
   resolved automatically are routed to a human anyway. Those reviews carry
   information about the region of the distribution the escalation path
   never visits.

2. **Weight every calibration sample by its inverse selection
   probability.** Even with (1), the two strata are reviewed at very
   different rates — escalated cases at 100%, auto-resolved at the audit
   rate — so the raw pool is still skewed. Recording the probability a
   sample had of being reviewed, and weighting by its inverse, recovers an
   unbiased estimate of the deployment quantile. This is the standard
   Horvitz-Thompson correction, and `arbiter.decision.conformal` consumes
   the weights directly in its quantile computation.

Selection is **deterministic per case**, derived from a keyed hash rather
than a random draw. Three reasons, and the third is the one that matters:
a re-run of the same case selects identically (so a retry does not change
whether it is audited), the decision is reproducible from the case id
alone during an audit, and an operator cannot influence which of their
cases get audited by resubmitting — which they could if selection were a
coin flip evaluated per request.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Optional

# Escalated cases are reviewed by definition — that IS the escalation path.
ESCALATED_SELECTION_PROBABILITY = 1.0


@dataclass(frozen=True)
class ReviewSelection:
    """Whether this case is routed to a human, and with what probability it
    was going to be."""

    selected_for_review: bool
    selection_probability: float
    reason: str

    @property
    def inverse_probability_weight(self) -> float:
        """Horvitz-Thompson weight. A case reviewed at a 5% audit rate
        stands in for ~20 cases that were not reviewed; an escalated case
        reviewed with certainty stands in for exactly itself."""
        if self.selection_probability <= 0.0:
            return 0.0
        return 1.0 / self.selection_probability

    def to_dict(self) -> dict:
        return {
            "selected_for_review": self.selected_for_review,
            "selection_probability": round(self.selection_probability, 6),
            "inverse_probability_weight": round(self.inverse_probability_weight, 4),
            "reason": self.reason,
        }


def _deterministic_unit_interval(case_id: str, salt: str) -> float:
    """Map a case id uniformly into [0, 1), keyed so it cannot be gamed.

    Keyed rather than a bare hash: with an unkeyed hash a party who can
    influence a case identifier could grind for one that lands outside the
    audit window. The key is derived from the deployment's audit-sampling
    secret, so the mapping is stable within a deployment and unpredictable
    outside it.
    """
    digest = hmac.new(salt.encode("utf-8"), case_id.encode("utf-8"), hashlib.sha256).digest()
    # First 8 bytes as a big-endian integer, scaled to [0, 1).
    return int.from_bytes(digest[:8], "big") / float(1 << 64)


def select_for_review(
    case_id: str,
    auto_resolved: bool,
    audit_rate: Optional[float] = None,
    salt: Optional[str] = None,
) -> ReviewSelection:
    """Decide whether this case goes to a human, and record the probability.

    `auto_resolved=False` (the gate abstained) always routes to a human at
    probability 1.0 — that is what abstention means. `auto_resolved=True`
    routes with probability `audit_rate`, and those are the samples that
    repair the calibration pool.
    """
    if not auto_resolved:
        return ReviewSelection(
            selected_for_review=True,
            selection_probability=ESCALATED_SELECTION_PROBABILITY,
            reason=(
                "the abstention gate did not auto-resolve this case, so a human decides "
                "it by definition"
            ),
        )

    if audit_rate is None or salt is None:
        from arbiter.config import get_settings

        settings = get_settings()
        audit_rate = settings.review_audit_rate if audit_rate is None else audit_rate
        salt = settings.review_sampling_salt if salt is None else salt

    audit_rate = max(0.0, min(1.0, audit_rate))
    if audit_rate == 0.0:
        return ReviewSelection(
            selected_for_review=False,
            selection_probability=0.0,
            reason=(
                "audit sampling is disabled (ARBITER_REVIEW_AUDIT_RATE=0). The calibration "
                "pool will only ever see escalated cases, which is a biased subsample -- "
                "the gate's coverage guarantee degrades over time as a result."
            ),
        )

    draw = _deterministic_unit_interval(case_id, salt)
    selected = draw < audit_rate
    return ReviewSelection(
        selected_for_review=selected,
        selection_probability=audit_rate,
        reason=(
            f"auto-resolved case selected for audit review at rate {audit_rate:.1%} "
            f"(deterministic draw {draw:.4f}) -- these samples are what keep the "
            f"calibration pool exchangeable with the deployment distribution"
            if selected
            else
            f"auto-resolved case not selected for audit (draw {draw:.4f} >= rate {audit_rate:.1%})"
        ),
    )


def calibration_weight(selection_probability: Optional[float]) -> float:
    """Inverse-probability weight for a stored calibration sample.

    Legacy rows written before audit sampling existed have no recorded
    probability. They are weighted 1.0 rather than discarded: they are
    genuine analyst adjudications and throwing them away would cost more
    coverage than the residual bias costs. The bias they carry shrinks as
    correctly-weighted samples accumulate, and `ConformalAbstentionGate`
    reports the effective sample size so the dilution is visible.
    """
    if selection_probability is None or selection_probability <= 0.0:
        return 1.0
    return 1.0 / selection_probability
