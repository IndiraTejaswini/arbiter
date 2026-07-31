"""
Reg E (12 CFR 1005.11) provisional credit -- a second, independent decision
axis alongside the merchant/card-member verdict, not a third value on it.

The regulatory point this exists to serve: under Regulation E, an issuer
investigating an EFT/debit-card error claim that isn't resolved within the
statutory window must provisionally recredit the card member's account
while the investigation continues, then reverse that credit only if the
investigation concludes against them. That protection has nothing to do
with WHICH side ultimately wins -- it is specifically about not leaving a
card member's money tied up during an unresolved investigation. This build
already has exactly the signal needed to make that call instantly instead
of on a 10-business-day clock: the conformal abstention gate
(arbiter.decision.conformal) already knows, the moment adjudication runs,
whether the case resolved on the merits or not.

Scope, stated honestly: this models Reg E specifically (`reg_regime ==
"REG_E"`, i.e. EFT/debit-card disputes). Regulation Z governs credit-card
billing disputes and has a related but structurally different protection
(the cardholder is not obligated to pay the disputed amount while an
investigation is open -- a payment-withholding right, not a credit-issuance
one) that this function deliberately does not attempt to model as the same
mechanism; conflating the two would misstate what either regulation
actually requires.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ProvisionalCreditDecision:
    due: bool
    reason: str

    def to_dict(self) -> dict:
        return {"due": self.due, "reason": self.reason}


def compute_provisional_credit(
    reg_regime: str,
    decision: Optional[str],
    abstained: bool,
    conflicting: bool,
) -> ProvisionalCreditDecision:
    """
    `decision` is the referee's raw outcome name (e.g. "CARD_MEMBER_WINS",
    "SPLIT", "MERCHANT_WINS", or None) -- the same value
    `EvaluationResult.decision` carries. `abstained` is `not
    AbstentionDecision.auto_resolve`; `conflicting` is
    `bool(EvaluationResult.conflicting_outcomes)`.
    """
    if reg_regime != "REG_E":
        return ProvisionalCreditDecision(
            due=False,
            reason=(
                "Reg E provisional credit applies to reg_regime=REG_E (EFT/debit) "
                "disputes; this case is REG_Z, whose analogous protection is "
                "payment-withholding during investigation, not credit issuance, "
                "and is not modeled by this axis."
            ),
        )

    if abstained or conflicting or decision is None:
        return ProvisionalCreditDecision(
            due=True,
            reason=(
                "12 CFR 1005.11(c): the investigation did not resolve on the "
                "merits -- provisional credit is due to the card member while "
                "investigation continues, reversible only if a later "
                "resolution finds against them."
            ),
        )

    if decision in ("CARD_MEMBER_WINS", "SPLIT"):
        return ProvisionalCreditDecision(
            due=True,
            reason=f"resolved on the merits as {decision} -- credit (full or partial) is due outright, not merely provisionally.",
        )

    return ProvisionalCreditDecision(due=False, reason=f"resolved on the merits as {decision} -- no credit due.")
