"""
Ground-truth verdicts, derived from World FACTS -- never from the rulepack.

CLAUDE.md invariant #5: this module must not import arbiter.horn,
arbiter.rulepack, or arbiter.evidence.derive (import-linter enforced). It
answers a different question than the rulepack does: "given what actually
happened, who SHOULD win" -- not "given the evidence a system could gather,
what does the rulepack decide". evals/accuracy.py compares the two, and
that comparison is the whole point of this module's existence; if it ever
imported the thing it's supposed to grade, the comparison would be
tautological.
"""

from __future__ import annotations

from enum import Enum

from .world import World


class Outcome(Enum):
    CARD_MEMBER_PREVAILS = "CARD_MEMBER_PREVAILS"
    MERCHANT_PREVAILS = "MERCHANT_PREVAILS"
    SPLIT = "SPLIT"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


def _true_outcome_f29(w: World) -> Outcome:
    if w.account_was_taken_over:
        return Outcome.CARD_MEMBER_PREVAILS
    if w.cm_actually_authorised:
        return Outcome.MERCHANT_PREVAILS
    return Outcome.CARD_MEMBER_PREVAILS


def _true_outcome_c08(w: World) -> Outcome:
    if w.is_digital_goods:
        return Outcome.MERCHANT_PREVAILS if w.digital_access_occurred else Outcome.CARD_MEMBER_PREVAILS
    if w.item_delivered and w.delivered_to_correct_address:
        return Outcome.MERCHANT_PREVAILS
    if w.item_delivered and not w.delivered_to_correct_address:
        # delivered, but to the wrong place -- genuinely contested; the
        # merchant shipped in good faith but the goods didn't reach the
        # card member, so neither side is simply "right".
        return Outcome.SPLIT
    if w.cm_cancelled_before_shipment:
        return Outcome.CARD_MEMBER_PREVAILS
    if not w.merchant_shipped_before_dispute:
        return Outcome.CARD_MEMBER_PREVAILS
    if w.carrier_exception:
        return Outcome.CARD_MEMBER_PREVAILS
    # shipped, not yet delivered, no exception logged, dispute filed early --
    # genuinely still in flight.
    return Outcome.INSUFFICIENT_EVIDENCE


def _true_outcome_c02(w: World) -> Outcome:
    if w.merchant_issued_refund and w.refund_amount_minor >= w.expected_refund_minor:
        return Outcome.MERCHANT_PREVAILS
    if w.merchant_issued_refund and w.refund_amount_minor < w.expected_refund_minor:
        return Outcome.SPLIT
    if w.return_delivered_to_merchant and not w.merchant_issued_refund:
        return Outcome.CARD_MEMBER_PREVAILS
    if w.merchant_promised_refund_comm and not w.merchant_issued_refund:
        return Outcome.CARD_MEMBER_PREVAILS
    if w.merchant_confirmed_cancellation_comm and not w.merchant_issued_refund:
        return Outcome.CARD_MEMBER_PREVAILS
    if w.service_never_rendered and not w.merchant_issued_refund:
        return Outcome.CARD_MEMBER_PREVAILS
    if w.cm_returned_item and not w.return_delivered_to_merchant:
        # card member says they returned it; nothing confirms arrival yet.
        return Outcome.INSUFFICIENT_EVIDENCE
    if w.return_requested_days_after_purchase > w.return_window_days and w.refund_policy_disclosed:
        return Outcome.MERCHANT_PREVAILS
    return Outcome.INSUFFICIENT_EVIDENCE


def true_outcome(world: World) -> Outcome:
    """Derived from world FACTS, never from the rulepack (CLAUDE.md #5)."""
    if world.reason_code == "F29":
        return _true_outcome_f29(world)
    if world.reason_code == "C08":
        return _true_outcome_c08(world)
    if world.reason_code == "C02":
        return _true_outcome_c02(world)
    raise ValueError(f"unknown reason_code {world.reason_code!r}")
