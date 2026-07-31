"""
The chargeback-right gate: deterministic, stdlib-only, runs before the referee.

Output shape mirrors `arbiter.horn.proof.ProofNode` on purpose. A case that
ends here ends on a citation ("4540 Excluded Transactions: Card Present
Transactions") exactly as legibly as a case that ends on a fired rule, and
`GET /v1/audit/{case_id}` renders both from the same kind of record. A gate
whose output is a bare boolean would be a second, worse explanation path
bolted onto a system whose entire thesis is that there is one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, List, Mapping, Optional, Tuple

from .models import ATTRIBUTE_VOCABULARY, ChargebackRight, Exclusion, FilingWindowBranch


@dataclass(frozen=True)
class ExclusionFinding:
    exclusion_id: str
    fired: bool
    description: str
    legal_basis: str
    satisfied_conditions: Tuple[str, ...]
    undetermined_conditions: Tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "exclusion_id": self.exclusion_id,
            "fired": self.fired,
            "description": self.description,
            "legal_basis": self.legal_basis,
            "satisfied_conditions": list(self.satisfied_conditions),
            "undetermined_conditions": list(self.undetermined_conditions),
        }


@dataclass(frozen=True)
class BranchFinding:
    branch_id: str
    anchor_attribute: Optional[str]
    anchor_at: Optional[datetime]
    deadline: Optional[datetime]
    timely: Optional[bool]  # None == could not be evaluated
    capped_out: bool = False

    def to_dict(self) -> dict:
        return {
            "branch_id": self.branch_id,
            "anchor_attribute": self.anchor_attribute,
            "anchor_at": self.anchor_at.isoformat() if self.anchor_at else None,
            "deadline": self.deadline.isoformat() if self.deadline else None,
            "timely": self.timely,
            "capped_out": self.capped_out,
        }


@dataclass(frozen=True)
class FilingWindowFinding:
    timely: Optional[bool]  # None == no branch could be evaluated
    branches: Tuple[BranchFinding, ...]

    def to_dict(self) -> dict:
        return {"timely": self.timely, "branches": [b.to_dict() for b in self.branches]}


@dataclass(frozen=True)
class EligibilityResult:
    """`available=False` is the only value that stops a case.

    `undetermined` is deliberately NOT wired into the abstention gate. It is
    real information -- "we could not tell whether this was a Card Present
    transaction, and if it was, the whole dispute was excluded" -- but the
    conformal gate's coverage guarantee is calibrated over a fixed feature
    vector (`arbiter.decision.confidence.ConfidenceVector`), and quietly
    adding a term to it invalidates the calibration set that guarantee rests
    on. It is recorded in the decision payload and emitted as a case event
    instead, where a human reviewing the case sees it. Wiring it into
    confidence is a calibration change, not a code change, and should be
    made as one.
    """

    available: bool
    network_code: str
    reason: str
    exclusions: Tuple[ExclusionFinding, ...]
    filing_window: FilingWindowFinding
    undetermined: Tuple[str, ...]

    @property
    def fired_exclusions(self) -> Tuple[ExclusionFinding, ...]:
        return tuple(e for e in self.exclusions if e.fired)

    def to_dict(self) -> dict:
        return {
            "available": self.available,
            "network_code": self.network_code,
            "reason": self.reason,
            "filing_window": self.filing_window.to_dict(),
            "exclusions_fired": [e.to_dict() for e in self.fired_exclusions],
            "exclusions_evaluated": len(self.exclusions),
            "undetermined_attributes": list(self.undetermined),
        }


def _evaluate_exclusion(exclusion: Exclusion, attrs: Mapping[str, Any]) -> Tuple[ExclusionFinding, List[str]]:
    satisfied: List[str] = []
    undetermined: List[str] = []
    unknown_attrs: List[str] = []
    refuted = False

    for condition in exclusion.conditions:
        verdict = condition.evaluate(attrs)
        if verdict is None:
            undetermined.append(condition.describe())
            unknown_attrs.append(condition.attribute)
        elif verdict:
            satisfied.append(condition.describe())
        else:
            refuted = True

    fired = bool(exclusion.conditions) and not refuted and not undetermined
    finding = ExclusionFinding(
        exclusion_id=exclusion.exclusion_id,
        fired=fired,
        description=exclusion.description,
        legal_basis=exclusion.legal_basis,
        satisfied_conditions=tuple(satisfied),
        undetermined_conditions=tuple(undetermined),
    )
    # An unknown attribute is only worth reporting if knowing it could have
    # changed the answer -- i.e. no OTHER condition in the bullet was
    # already refuted. An exclusion that failed on a condition we DO know
    # about was not going to fire regardless of what we don't know.
    decisive_unknowns = [] if refuted else unknown_attrs
    return finding, decisive_unknowns


def _evaluate_branch(
    branch: FilingWindowBranch, attrs: Mapping[str, Any], filed_at: datetime
) -> BranchFinding:
    # "whichever occurred first": the earliest anchor that is actually known.
    candidates = [
        (name, attrs[name]) for name in branch.from_attributes
        if isinstance(attrs.get(name), datetime)
    ]
    if not candidates:
        return BranchFinding(branch.branch_id, None, None, None, timely=None)

    anchor_attr, anchor_at = min(candidates, key=lambda pair: pair[1])
    deadline = anchor_at + timedelta(days=branch.days)
    timely = filed_at <= deadline
    capped_out = False

    # The absolute cap (4554's 540 days) overrides a branch that would
    # otherwise still be open. Without it the awareness clock is unbounded:
    # a card member who becomes "aware" three years later would be filing
    # inside a 120-day window that started three years after the charge.
    if timely and branch.absolute_cap_days is not None and branch.cap_from_attribute:
        cap_anchor = attrs.get(branch.cap_from_attribute)
        if isinstance(cap_anchor, datetime):
            cap_deadline = cap_anchor + timedelta(days=branch.absolute_cap_days)
            if filed_at > cap_deadline:
                timely = False
                capped_out = True
                deadline = min(deadline, cap_deadline)

    return BranchFinding(branch.branch_id, anchor_attr, anchor_at, deadline, timely, capped_out)


def evaluate_chargeback_right(
    right: Optional[ChargebackRight],
    attributes: Mapping[str, Any],
    filed_at: datetime,
) -> EligibilityResult:
    """Evaluate the network chargeback right for one case.

    A rulepack with no `chargeback_right:` block returns `available=True`
    with an explicit reason saying so, rather than silently behaving as
    though the right were unconditional -- the difference matters when a
    reviewer asks why no exclusion was considered.
    """
    if right is None:
        return EligibilityResult(
            available=True, network_code="", reason="rulepack declares no chargeback_right block",
            exclusions=(), filing_window=FilingWindowFinding(timely=None, branches=()), undetermined=(),
        )

    undetermined: List[str] = []

    findings: List[ExclusionFinding] = []
    for exclusion in right.exclusions:
        finding, unknowns = _evaluate_exclusion(exclusion, attributes)
        findings.append(finding)
        undetermined.extend(unknowns)

    branches = tuple(_evaluate_branch(b, attributes, filed_at) for b in right.filing_window)
    evaluated = [b for b in branches if b.timely is not None]
    if not evaluated:
        window_timely: Optional[bool] = None
        undetermined.extend(
            name for b in right.filing_window for name in b.from_attributes
            if name in ATTRIBUTE_VOCABULARY
        )
    else:
        # Any open branch keeps the dispute in time: the guide lists these
        # as alternatives ("120 days from X, or 120 days from Y").
        window_timely = any(b.timely for b in evaluated)

    fired = [f for f in findings if f.fired]
    if fired:
        available = False
        reason = (
            f"excluded transaction under {right.network_code}: "
            + "; ".join(f.description for f in fired)
        )
    elif window_timely is False:
        available = False
        late = ", ".join(
            f"{b.branch_id} closed {b.deadline.isoformat()}"
            + (" (absolute cap)" if b.capped_out else "")
            for b in evaluated if b.deadline
        )
        reason = (
            f"filed outside the {right.network_code} chargeback window "
            f"({filed_at.isoformat()}): {late}"
        )
    else:
        available = True
        reason = (
            f"chargeback right available under {right.network_code}"
            + ("" if window_timely else " (filing window could not be evaluated)")
        )

    return EligibilityResult(
        available=available,
        network_code=right.network_code,
        reason=reason,
        exclusions=tuple(findings),
        filing_window=FilingWindowFinding(timely=window_timely, branches=branches),
        undetermined=tuple(sorted(set(undetermined))),
    )
