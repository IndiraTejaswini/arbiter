"""
The ground-truth world model (Phase 1 foundation).

CLAUDE.md invariant #5, the most consequential rule in this repository:
datagen.world MUST NOT import arbiter.horn, arbiter.rulepack, or
arbiter.evidence.derive (enforced by import-linter, pyproject.toml). If it
did, every accuracy number measured against this world would be circular --
the rulepack would be graded against a ground truth built from its own
vocabulary, and it would look excellent, and you would not notice.

`World` is what *actually happened* in a synthetic dispute: whether the card
member really authorised the transaction, whether the merchant really
shipped and it really arrived, whether a refund was really owed. observe.py
reads a World and produces the (lossy, provenance-tiered, sometimes noisy)
evidence a real system would actually have -- never the World fields
directly. outcome.py reads a World and produces the correct verdict,
independently of any rulepack. The gap between what outcome.py says and what
the rulepack decides, measured over many worlds, IS the system's accuracy
number (evals/accuracy.py).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

UTC = timezone.utc

ReasonCode = Literal["F29", "C08", "C02"]
MerchantTier = Literal["MICRO", "SMALL", "MID", "ENTERPRISE"]


@dataclass(frozen=True)
class World:
    reason_code: ReasonCode
    case_id: str

    # -- transaction context, all reason codes --------------------------
    amount_minor: int
    currency: str
    transaction_at: datetime
    dispute_filed_at: datetime

    # -- merchant structural context (bias sources, A7) -------------------
    merchant_id: str
    merchant_size_tier: MerchantTier
    merchant_tenure_days: int
    merchant_keeps_records: bool          # correlates with size
    merchant_uses_adec: bool              # correlates with size
    merchant_responds_to_inquiry: bool    # the R13 mechanism

    # -- F29: card-not-present fraud world truth --------------------------
    cm_actually_authorised: bool = True
    account_was_taken_over: bool = False
    three_ds_performed: bool = False
    avs_result: str = "N"                 # Y | N | X | A | Z | U
    cvv_result: str = "N"                 # M | N | U
    device_matches_prior: bool = False
    ip_matches_prior: bool = False
    shipping_matches_prior: bool = False
    user_id_matches_prior: bool = False
    prior_undisputed_count: int = 0
    prior_txn_age_days: int = 0
    cardholder_reported_lost_stolen: bool = False
    velocity_anomaly: bool = False

    # -- C08: goods/services not received world truth ----------------------
    merchant_shipped: bool = False
    merchant_shipped_before_dispute: bool = False
    item_delivered: bool = False
    delivered_to_correct_address: bool = False
    signature_required: bool = False
    signature_captured: bool = False
    is_digital_goods: bool = False
    digital_access_occurred: bool = False
    cardholder_confirmed_receipt_comm: bool = False
    cm_cancelled_before_shipment: bool = False
    carrier_exception: bool = False
    delivery_address_mismatch: bool = False

    # -- C02: credit not processed world truth -----------------------------
    cm_returned_item: bool = False
    return_delivered_to_merchant: bool = False
    merchant_issued_refund: bool = False
    refund_amount_minor: int = 0
    expected_refund_minor: int = 0
    merchant_promised_refund_comm: bool = False
    merchant_confirmed_cancellation_comm: bool = False
    service_never_rendered: bool = False
    return_window_days: int = 30
    return_requested_days_after_purchase: int = 0
    refund_policy_disclosed: bool = False

    def age_days(self, at: datetime) -> int:
        return (at - self.transaction_at).days


def _sample_merchant(rng: random.Random) -> tuple[MerchantTier, int, bool, bool, bool]:
    tier = rng.choices(
        ["MICRO", "SMALL", "MID", "ENTERPRISE"],
        weights=[0.35, 0.30, 0.20, 0.15],
    )[0]
    tenure = {
        "MICRO": rng.randint(10, 400),
        "SMALL": rng.randint(60, 1200),
        "MID": rng.randint(200, 2500),
        "ENTERPRISE": rng.randint(500, 6000),
    }[tier]
    # The planted, known-magnitude bias arbiter.fairness's audit is meant to
    # discover: record-keeping and ADEC adoption both correlate with size.
    # This is a STRUCTURAL fact about the world (smaller merchants really do
    # have worse systems), not a fairness defect in the rulepack itself --
    # the audit's job is to show the rulepack does not compound it.
    keeps_records_p = {"MICRO": 0.35, "SMALL": 0.55, "MID": 0.80, "ENTERPRISE": 0.95}[tier]
    uses_adec_p = {"MICRO": 0.05, "SMALL": 0.15, "MID": 0.45, "ENTERPRISE": 0.80}[tier]
    responds_p = {"MICRO": 0.55, "SMALL": 0.70, "MID": 0.88, "ENTERPRISE": 0.96}[tier]
    return (
        tier, tenure,
        rng.random() < keeps_records_p,
        rng.random() < uses_adec_p,
        rng.random() < responds_p,
    )


def _sample_f29(rng: random.Random, w: dict) -> dict:
    authorised = rng.random() < 0.55
    ato = (not authorised) and rng.random() < 0.6
    w["cm_actually_authorised"] = authorised
    w["account_was_taken_over"] = ato

    # Deliberately imperfect proxies, not deterministic tells: AVS/CVV/3DS
    # and identifier matches correlate with true authorisation but do not
    # determine it -- an address change or a new device makes a genuine
    # cardholder fail AVS; a family member sharing a household network
    # makes a fraudulent actor pass a device/IP match. Network-tier
    # evidence carries no *extraction* noise (it's authoritative, unlike a
    # scanned document), but it is not a perfect oracle either -- this is
    # what keeps rulepack accuracy against true_outcome honestly below
    # ~100% instead of the ground truth leaking through the evidence by
    # construction.
    three_ds = rng.random() < (0.35 if authorised else 0.08)
    w["three_ds_performed"] = three_ds
    w["avs_result"] = rng.choices(["Y", "N", "X", "A", "Z", "U"], weights=[0.35, 0.28, 0.1, 0.1, 0.12, 0.05])[0] \
        if authorised else rng.choices(["Y", "N", "X", "A", "Z", "U"], weights=[0.22, 0.38, 0.05, 0.1, 0.1, 0.15])[0]
    w["cvv_result"] = rng.choices(["M", "N", "U"], weights=[0.62, 0.28, 0.1])[0] \
        if authorised else rng.choices(["M", "N", "U"], weights=[0.28, 0.52, 0.2])[0]

    has_prior = rng.random() < (0.5 if authorised else 0.15)
    w["prior_undisputed_count"] = rng.randint(2, 12) if has_prior else rng.randint(0, 1)
    w["prior_txn_age_days"] = rng.randint(10, 500) if has_prior else 0
    match_p = 0.66 if (has_prior and authorised) else 0.20
    w["device_matches_prior"] = has_prior and rng.random() < match_p
    w["ip_matches_prior"] = has_prior and rng.random() < match_p
    w["shipping_matches_prior"] = has_prior and rng.random() < (match_p * 0.85)
    w["user_id_matches_prior"] = has_prior and rng.random() < (match_p * 0.9)

    # Mostly tracks true authorisation, but not deterministically: a small
    # fraction of authorised transactions still get a (mistaken or
    # friendly-fraud) lost/stolen claim, and not every genuinely
    # unauthorised transaction gets reported this way.
    w["cardholder_reported_lost_stolen"] = rng.random() < (0.04 if authorised else 0.35)
    w["velocity_anomaly"] = rng.random() < (0.05 if authorised else 0.4)
    return w


def _sample_c08(rng: random.Random, w: dict) -> dict:
    is_digital = rng.random() < 0.15
    w["is_digital_goods"] = is_digital

    shipped = rng.random() < 0.85
    w["merchant_shipped"] = shipped
    w["merchant_shipped_before_dispute"] = shipped and rng.random() < 0.9

    if is_digital:
        w["digital_access_occurred"] = rng.random() < 0.75
        w["item_delivered"] = w["digital_access_occurred"]
        w["delivered_to_correct_address"] = True
    else:
        delivered = shipped and rng.random() < 0.82
        w["item_delivered"] = delivered
        w["delivered_to_correct_address"] = delivered and rng.random() < 0.9
        w["delivery_address_mismatch"] = delivered and not w["delivered_to_correct_address"]
        w["signature_required"] = rng.random() < 0.3
        w["signature_captured"] = w["signature_required"] and delivered and rng.random() < 0.85
        w["carrier_exception"] = (not delivered) and shipped and rng.random() < 0.5

    w["cardholder_confirmed_receipt_comm"] = w["item_delivered"] and rng.random() < 0.15
    w["cm_cancelled_before_shipment"] = (not shipped) and rng.random() < 0.3
    return w


def _sample_c02(rng: random.Random, w: dict) -> dict:
    returned = rng.random() < 0.6
    w["cm_returned_item"] = returned
    w["return_delivered_to_merchant"] = returned and rng.random() < 0.85

    owed_refund = rng.random() < 0.55
    w["service_never_rendered"] = (not returned) and rng.random() < 0.25
    refund_issued = rng.random() < (0.7 if (owed_refund or w["service_never_rendered"]) else 0.3)
    w["merchant_issued_refund"] = refund_issued
    if refund_issued:
        w["expected_refund_minor"] = w["amount_minor"] if "amount_minor" in w else 5000
        correct = rng.random() < 0.85
        w["refund_amount_minor"] = w["expected_refund_minor"] if correct else int(w["expected_refund_minor"] * rng.uniform(0.3, 0.85))
    w["merchant_promised_refund_comm"] = (not refund_issued) and rng.random() < 0.25
    w["merchant_confirmed_cancellation_comm"] = (not returned) and rng.random() < 0.2
    w["return_window_days"] = rng.choice([14, 30, 45, 60, 90])
    w["return_requested_days_after_purchase"] = rng.randint(0, 120)
    w["refund_policy_disclosed"] = rng.random() < 0.7
    return w


def generate_world(rng: random.Random, reason_code: ReasonCode, case_id: str) -> World:
    """Sample one internally-consistent synthetic World. Every correlation
    here is a modeling choice, stated in-line, not hidden -- see A7's
    planted merchant-tier bias in _sample_merchant."""
    tier, tenure, keeps_records, uses_adec, responds = _sample_merchant(rng)

    transaction_at = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=rng.randint(0, 540), seconds=rng.randint(0, 86400))
    dispute_gap_days = rng.choices([rng.randint(1, 10), rng.randint(11, 45), rng.randint(46, 120)], weights=[0.6, 0.3, 0.1])[0]
    dispute_filed_at = transaction_at + timedelta(days=dispute_gap_days)

    fields = dict(
        reason_code=reason_code,
        case_id=case_id,
        amount_minor=rng.choice([1999, 4599, 8999, 12500, 25000, 45000, 89900]),
        currency="USD",
        transaction_at=transaction_at,
        dispute_filed_at=dispute_filed_at,
        merchant_id=f"merchant-{rng.randint(1000, 9999)}",
        merchant_size_tier=tier,
        merchant_tenure_days=tenure,
        merchant_keeps_records=keeps_records,
        merchant_uses_adec=uses_adec,
        merchant_responds_to_inquiry=responds,
    )

    if reason_code == "F29":
        fields = _sample_f29(rng, fields)
    elif reason_code == "C08":
        fields = _sample_c08(rng, fields)
    elif reason_code == "C02":
        fields = _sample_c02(rng, fields)
    else:
        raise ValueError(f"unknown reason_code {reason_code!r}")

    return World(**fields)
