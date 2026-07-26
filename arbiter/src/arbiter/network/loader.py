"""
Network-side evidence loader -- Task 1 primary source.

Amex-held data: authorization, settlement, AVS, CVV, 3DS, device session,
descriptor, prior transactions, and (for C08/C02) carrier and refund/
settlement data that flows through Amex's own network regardless of whether
the merchant ever responds. This is what makes a silent merchant's case
still adjudicable on the merits (C3) instead of defaulting to the card
member (the R13 failure mode CLAUDE.md's scope section names explicitly).

Read-only interface to a production ledger: in this build it reads
`arbiter.db.models.SeedTransaction` (a synthetic stand-in populated by
datagen at seed time -- see that model's docstring); in a production
deployment this function's body would be replaced with real ledger/gateway
reads, and nothing downstream of it would need to change, because it
already speaks only in terms of `NetworkFacts` and `EvidenceNode`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from arbiter.evidence.models import EvidenceNode, EvidenceNodeType, ProvenanceTier


@dataclass(frozen=True)
class NetworkFacts:
    """Everything Amex's own systems would know about a transaction and its
    merchant relationship, independent of merchant participation in the
    dispute. Reason-code-specific groups are all-optional; a loader only
    populates the group relevant to the case's reason code."""

    # -- F29: card-not-present fraud signals -------------------------------
    avs_result: Optional[str] = None          # Y | N | X | A | Z | U
    cvv_result: Optional[str] = None          # M | N | U
    three_ds_performed: Optional[bool] = None
    device_matches_prior: Optional[bool] = None
    ip_matches_prior: Optional[bool] = None
    shipping_matches_prior: Optional[bool] = None
    user_id_matches_prior: Optional[bool] = None
    prior_undisputed_count: Optional[int] = None
    prior_txn_age_days: Optional[int] = None
    account_takeover_signal: Optional[bool] = None
    velocity_anomaly: Optional[bool] = None

    # -- C08: goods/services not received -----------------------------------
    item_delivered: Optional[bool] = None
    delivered_to_correct_address: Optional[bool] = None
    signature_required: Optional[bool] = None
    signature_captured: Optional[bool] = None
    merchant_shipped_before_dispute: Optional[bool] = None
    delivery_address_mismatch: Optional[bool] = None
    carrier_exception: Optional[bool] = None
    delivery_at: Optional[datetime] = None
    shipment_at: Optional[datetime] = None

    # -- C02: credit not processed -------------------------------------------
    refund_issued: Optional[bool] = None
    refund_amount_minor: Optional[int] = None
    expected_refund_minor: Optional[int] = None
    return_delivered_to_merchant: Optional[bool] = None
    return_window_days: Optional[int] = None
    return_requested_days_after_purchase: Optional[int] = None
    dispute_filed_before_return_received: Optional[bool] = None


def _node(node_type: EvidenceNodeType, case_id: str, predicate: str, value: bool, **extra) -> EvidenceNode:
    attrs = {"asserts_predicate": predicate, "predicate_value": value, "label": predicate}
    attrs.update(extra)
    return EvidenceNode(case_id=case_id, node_type=node_type, attrs=attrs, provenance=ProvenanceTier.NETWORK)


def load_network_evidence(case_id: str, reason_code: str, facts: NetworkFacts) -> List[EvidenceNode]:
    """NETWORK-tier evidence, always present. Emits: authorization,
    settlement, avs_result, cvv_result, three_ds_result, device_session,
    descriptor, prior_transaction[] (F29), or the carrier/refund equivalents
    for C08/C02."""
    nodes: List[EvidenceNode] = []

    if reason_code == "F29":
        if facts.prior_undisputed_count is not None:
            nodes.append(_node(EvidenceNodeType.PRIOR_TRANSACTION, case_id, "prior_undisputed_txn_count_ge_2",
                                facts.prior_undisputed_count >= 2))
        if facts.prior_txn_age_days is not None:
            nodes.append(_node(EvidenceNodeType.PRIOR_TRANSACTION, case_id, "prior_txn_120_to_365_days_old",
                                120 <= facts.prior_txn_age_days <= 365))
        if facts.device_matches_prior is not None:
            nodes.append(_node(EvidenceNodeType.DEVICE_SESSION, case_id, "device_id_match", facts.device_matches_prior))
        if facts.ip_matches_prior is not None:
            nodes.append(_node(EvidenceNodeType.DEVICE_SESSION, case_id, "ip_address_match", facts.ip_matches_prior))
        if facts.shipping_matches_prior is not None:
            nodes.append(_node(EvidenceNodeType.ADDRESS, case_id, "shipping_address_match", facts.shipping_matches_prior))
        if facts.user_id_matches_prior is not None:
            nodes.append(_node(EvidenceNodeType.IDENTITY, case_id, "user_id_match", facts.user_id_matches_prior))
        if facts.avs_result is not None:
            nodes.append(_node(EvidenceNodeType.AVS_RESULT, case_id, "avs_match", facts.avs_result in ("Y", "X")))
        if facts.cvv_result is not None:
            nodes.append(_node(EvidenceNodeType.CVV_RESULT, case_id, "cvv_match", facts.cvv_result == "M"))
        if facts.three_ds_performed is not None:
            nodes.append(_node(EvidenceNodeType.THREE_DS_RESULT, case_id, "three_ds_authenticated", facts.three_ds_performed))
        if facts.velocity_anomaly is not None:
            nodes.append(_node(EvidenceNodeType.DEVICE_SESSION, case_id, "velocity_anomaly_flagged", facts.velocity_anomaly))
        if facts.account_takeover_signal is not None:
            nodes.append(_node(EvidenceNodeType.DEVICE_SESSION, case_id, "account_takeover_signal", facts.account_takeover_signal))

    elif reason_code == "C08":
        if facts.item_delivered is not None:
            nodes.append(_node(EvidenceNodeType.DELIVERY_SCAN, case_id, "delivery_confirmed", facts.item_delivered,
                                temporal_fact_key="delivery", temporal_value=facts.delivery_at))
            nodes.append(_node(EvidenceNodeType.DELIVERY_SCAN, case_id, "tracking_shows_delivered", facts.item_delivered))
        if facts.delivered_to_correct_address is not None:
            nodes.append(_node(EvidenceNodeType.ADDRESS, case_id, "address_matches_avs", facts.delivered_to_correct_address))
        if facts.signature_required is not None:
            nodes.append(_node(EvidenceNodeType.DELIVERY_SCAN, case_id, "signature_missing",
                                bool(facts.signature_required and not facts.signature_captured)))
        if facts.merchant_shipped_before_dispute is not None:
            nodes.append(_node(EvidenceNodeType.SHIPMENT, case_id, "merchant_shipped_before_dispute",
                                facts.merchant_shipped_before_dispute,
                                temporal_fact_key="shipment", temporal_value=facts.shipment_at))
        if facts.delivery_address_mismatch is not None:
            nodes.append(_node(EvidenceNodeType.ADDRESS, case_id, "delivery_address_mismatch", facts.delivery_address_mismatch))
        if facts.carrier_exception is not None:
            nodes.append(_node(EvidenceNodeType.DELIVERY_SCAN, case_id, "carrier_exception_reported", facts.carrier_exception))

    elif reason_code == "C02":
        if facts.refund_issued is not None:
            nodes.append(_node(EvidenceNodeType.REFUND, case_id, "refund_issued", facts.refund_issued))
            amount_ok = bool(
                facts.refund_issued and facts.refund_amount_minor is not None and facts.expected_refund_minor is not None
                and abs(facts.refund_amount_minor - facts.expected_refund_minor) <= max(50, facts.expected_refund_minor // 100)
            )
            nodes.append(_node(EvidenceNodeType.REFUND, case_id, "refund_amount_matches_expected", amount_ok))
            nodes.append(_node(EvidenceNodeType.REFUND, case_id, "partial_refund_issued", bool(facts.refund_issued and not amount_ok)))
        if facts.return_delivered_to_merchant is not None:
            nodes.append(_node(EvidenceNodeType.SHIPMENT, case_id, "return_delivered_to_merchant", facts.return_delivered_to_merchant))
        if facts.return_window_days is not None and facts.return_requested_days_after_purchase is not None:
            nodes.append(_node(EvidenceNodeType.ORDER, case_id, "return_window_expired",
                                facts.return_requested_days_after_purchase > facts.return_window_days))
        if facts.dispute_filed_before_return_received is not None:
            nodes.append(_node(EvidenceNodeType.ORDER, case_id, "dispute_filed_before_return_received",
                                facts.dispute_filed_before_return_received))

    return nodes
