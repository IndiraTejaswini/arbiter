"""
Synthetic case generator (§12.1 Phase 1, Phase 7): named scenarios for the
three seed reason codes -- C08 goods-not-received, C02 credit-not-processed,
F29 card-not-present fraud -- built directly as evidence graphs, since no
real dispute dataset exists publicly (§11: "state the generative assumptions
explicitly; do not imply real data").

Each scenario is a fully-formed EvidenceGraph plus (where relevant) a
ProvenanceService with real ADEC commitments already sealed into a real
Merkle log, so the ADEC verification path is genuinely exercised end to end
rather than stubbed. Two scenarios are adversarial by design (§12.1 Phase 7's
"adversarial document suite" -- reproduced here at the evidence-graph level
rather than as literal PDF files, since this build has no OCR/VLM in the
loop to feed synthetic PDFs through anyway): a real ADEC backdating attempt
that gets caught, and a temporal contradiction matching §A6's own worked
example verbatim.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from arbiter.evidence.graph import EvidenceGraph
from arbiter.evidence.models import EvidenceNode, EvidenceNodeType, ProvenanceTier
from arbiter.provenance.commitment import ProvenanceService
from sdk.arbiter_commit import MerchantSDK

UTC = timezone.utc


@dataclass
class Scenario:
    case_id: str
    reason_code: str
    title: str
    description: str
    graph: EvidenceGraph
    dispute_filed_at: datetime
    provenance: Optional[ProvenanceService] = None


def _assert_predicate(
    graph: EvidenceGraph,
    node_type: EvidenceNodeType,
    predicate: str,
    value: bool,
    provenance: ProvenanceTier,
    node_id_hint: str = "",
    **extra_attrs,
) -> EvidenceNode:
    attrs = {"asserts_predicate": predicate, "predicate_value": value, "label": node_id_hint or predicate}
    attrs.update(extra_attrs)
    node = EvidenceNode(case_id=graph.case_id, node_type=node_type, attrs=attrs, provenance=provenance)
    graph.add_node(node)
    return node


# ============================================================== C08

def build_c08_merchant_wins_clean() -> Scenario:
    graph = EvidenceGraph("case-C08-001")
    _assert_predicate(graph, EvidenceNodeType.DELIVERY_SCAN, "delivery_confirmed", True, ProvenanceTier.NETWORK,
                       temporal_fact_key="delivery", temporal_value=datetime(2026, 6, 3, tzinfo=UTC))
    _assert_predicate(graph, EvidenceNodeType.ADDRESS, "address_matches_avs", True, ProvenanceTier.NETWORK)
    _assert_predicate(graph, EvidenceNodeType.DELIVERY_SCAN, "signature_missing", False, ProvenanceTier.NETWORK)
    _assert_predicate(graph, EvidenceNodeType.SHIPMENT, "merchant_shipped_before_dispute", True, ProvenanceTier.NETWORK,
                       temporal_fact_key="shipment", temporal_value=datetime(2026, 6, 1, tzinfo=UTC))
    graph.add_node(EvidenceNode(case_id=graph.case_id, node_type=EvidenceNodeType.ORDER,
                                 attrs={"money_role": "order_total", "minor_units": 8999, "currency": "USD"},
                                 provenance=ProvenanceTier.NETWORK))
    graph.add_node(EvidenceNode(case_id=graph.case_id, node_type=EvidenceNodeType.AUTHORIZATION,
                                 attrs={"money_role": "authorization", "minor_units": 8999, "currency": "USD"},
                                 provenance=ProvenanceTier.NETWORK))
    return Scenario(
        case_id=graph.case_id, reason_code="C08", title="Clean delivery, merchant wins",
        description="Carrier-confirmed delivery to the AVS-matched address, no missing signature. "
                     "Textbook proof-of-delivery defense (C08_R1).",
        graph=graph, dispute_filed_at=datetime(2026, 6, 10, tzinfo=UTC),
    )


def build_c08_card_member_wins_clean() -> Scenario:
    graph = EvidenceGraph("case-C08-002")
    _assert_predicate(graph, EvidenceNodeType.SHIPMENT, "merchant_shipped_before_dispute", False, ProvenanceTier.NETWORK)
    _assert_predicate(graph, EvidenceNodeType.DELIVERY_SCAN, "delivery_confirmed", False, ProvenanceTier.NETWORK)
    _assert_predicate(graph, EvidenceNodeType.DELIVERY_SCAN, "carrier_exception_reported", True, ProvenanceTier.NETWORK,
                       temporal_fact_key="carrier_exception", temporal_value=datetime(2026, 6, 5, tzinfo=UTC))
    return Scenario(
        case_id=graph.case_id, reason_code="C08", title="Never shipped, card member wins",
        description="Merchant never shipped before the dispute was filed, and the carrier logged an explicit "
                     "delivery exception (C08_R5).",
        graph=graph, dispute_filed_at=datetime(2026, 6, 12, tzinfo=UTC),
    )


def build_c08_temporal_contradiction() -> Scenario:
    """§A6's own worked example, reproduced verbatim: merchant asserts
    delivery on one date; the carrier's own record disagrees by three days;
    a cancellation email sits between the two claims."""
    graph = EvidenceGraph("case-C08-003")
    _assert_predicate(graph, EvidenceNodeType.COMMUNICATION, "delivery_confirmed", True, ProvenanceTier.SUBMITTED,
                       node_id_hint="merchant_claim", temporal_fact_key="delivery",
                       temporal_value=datetime(2026, 3, 5, tzinfo=UTC))
    _assert_predicate(graph, EvidenceNodeType.DELIVERY_SCAN, "delivery_confirmed", True, ProvenanceTier.NETWORK,
                       node_id_hint="carrier_record", temporal_fact_key="delivery",
                       temporal_value=datetime(2026, 3, 8, tzinfo=UTC))
    _assert_predicate(graph, EvidenceNodeType.COMMUNICATION, "cancellation_requested_before_shipment", False,
                       ProvenanceTier.SUBMITTED, node_id_hint="cancellation_email",
                       temporal_fact_key="cancellation", temporal_value=datetime(2026, 3, 6, tzinfo=UTC))
    _assert_predicate(graph, EvidenceNodeType.SHIPMENT, "merchant_shipped_before_dispute", True, ProvenanceTier.NETWORK,
                       temporal_fact_key="shipment", temporal_value=datetime(2026, 3, 1, tzinfo=UTC))
    return Scenario(
        case_id=graph.case_id, reason_code="C08", title="Merchant vs. carrier delivery date disagreement",
        description="Merchant claims delivery 2026-03-05; the carrier's own record shows 2026-03-08 -- a "
                     "same-fact temporal disagreement the graph layer must catch mechanically before any "
                     "decision should auto-resolve.",
        graph=graph, dispute_filed_at=datetime(2026, 3, 15, tzinfo=UTC),
    )


def build_c08_silent_merchant_amex_side_defense() -> Scenario:
    """The R03/R13 fix, made concrete: the merchant never responds at all,
    but Amex's own systems already hold AVS and carrier data sufficient for
    Advocate-M to construct a defense from data Amex already has (§A2)."""
    graph = EvidenceGraph("case-C08-004")
    _assert_predicate(graph, EvidenceNodeType.ADDRESS, "address_matches_avs", True, ProvenanceTier.NETWORK,
                       node_id_hint="amex_avs_record")
    _assert_predicate(graph, EvidenceNodeType.DELIVERY_SCAN, "delivery_confirmed", True, ProvenanceTier.NETWORK,
                       node_id_hint="amex_carrier_integration", temporal_fact_key="delivery",
                       temporal_value=datetime(2026, 5, 20, tzinfo=UTC))
    _assert_predicate(graph, EvidenceNodeType.DELIVERY_SCAN, "signature_missing", False, ProvenanceTier.NETWORK)
    # deliberately: NOTHING is asserted as SUBMITTED -- the merchant filed no response at all.
    return Scenario(
        case_id=graph.case_id, reason_code="C08", title="Silent merchant, Amex-side data alone",
        description="Merchant never responded to the inquiry -- the historical R03/R13 auto-concede trigger. "
                     "All evidence here is NETWORK-tier, from Amex's own systems, with zero SUBMITTED-tier "
                     "nodes: this is Advocate-M constructing a defense with no merchant input at all (see "
                     "the regulatory clock design: 'DO NOT auto-concede').",
        graph=graph, dispute_filed_at=datetime(2026, 5, 28, tzinfo=UTC),
    )


# ============================================================== C02

def build_c02_merchant_wins_clean() -> Scenario:
    graph = EvidenceGraph("case-C02-001")
    _assert_predicate(graph, EvidenceNodeType.REFUND, "refund_issued", True, ProvenanceTier.NETWORK)
    _assert_predicate(graph, EvidenceNodeType.REFUND, "refund_amount_matches_expected", True, ProvenanceTier.NETWORK)
    graph.add_node(EvidenceNode(case_id=graph.case_id, node_type=EvidenceNodeType.ORDER,
                                 attrs={"money_role": "order_total", "minor_units": 4500, "currency": "USD"},
                                 provenance=ProvenanceTier.NETWORK))
    graph.add_node(EvidenceNode(case_id=graph.case_id, node_type=EvidenceNodeType.REFUND,
                                 attrs={"money_role": "refund", "minor_units": 4500, "currency": "USD"},
                                 provenance=ProvenanceTier.NETWORK))
    return Scenario(
        case_id=graph.case_id, reason_code="C02", title="Refund already issued in full",
        description="Settlement data shows the refund posted for the correct amount (C02_R6).",
        graph=graph, dispute_filed_at=datetime(2026, 4, 2, tzinfo=UTC),
    )


def build_c02_card_member_wins_clean() -> Scenario:
    graph = EvidenceGraph("case-C02-002")
    _assert_predicate(graph, EvidenceNodeType.SHIPMENT, "return_delivered_to_merchant", True, ProvenanceTier.NETWORK)
    _assert_predicate(graph, EvidenceNodeType.REFUND, "refund_issued", False, ProvenanceTier.NETWORK)
    return Scenario(
        case_id=graph.case_id, reason_code="C02", title="Return delivered, never refunded",
        description="Carrier confirms the returned item reached the merchant; settlement data shows no "
                     "refund followed (C02_R1).",
        graph=graph, dispute_filed_at=datetime(2026, 4, 20, tzinfo=UTC),
    )


# ============================================================== F29

def build_f29_adec_generalized_merchant_wins() -> Scenario:
    """CE3.0 itself would fail here -- the prior transaction is only 45 days
    old, short of the 120-365 day window (§1.5). ADEC's generalisation
    removes that floor because it proves non-backdating directly."""
    graph = EvidenceGraph("case-F29-001")
    provenance = ProvenanceService()
    sdk = MerchantSDK(merchant_id="merchant-42", provenance=provenance)

    prior_txn_time = datetime(2026, 5, 1, tzinfo=UTC)  # only 45 days before the disputed transaction
    receipt = sdk.emit(
        "prior_transaction_commitment",
        b'{"order_id": "prior-889", "device_id": "dev-abc", "ip_address": "203.0.113.7"}',
        event_time=prior_txn_time,
    )
    provenance.seal(at=prior_txn_time.timestamp())

    disputed_txn_time = prior_txn_time + timedelta(days=45)
    dispute_filed_at = disputed_txn_time + timedelta(days=2)
    verification = sdk.reveal(receipt, dispute_filed_at=dispute_filed_at)
    assert verification.ok, "scenario setup error: ADEC commitment should verify cleanly here"

    _assert_predicate(graph, EvidenceNodeType.ATTESTATION, "adec_prior_txn_commitments_verified", True,
                       ProvenanceTier.COMMITTED, node_id_hint="adec_prior_txn", commitment_id=receipt.commitment_id)
    _assert_predicate(graph, EvidenceNodeType.DEVICE_SESSION, "device_id_match", True, ProvenanceTier.NETWORK)
    _assert_predicate(graph, EvidenceNodeType.DEVICE_SESSION, "ip_address_match", True, ProvenanceTier.NETWORK)
    # explicitly NOT set: prior_txn_120_to_365_days_old -- this is the point.

    return Scenario(
        case_id=graph.case_id, reason_code="F29", title="ADEC generalises CE3.0 beyond the 120-day floor",
        description="Device and IP match a prior transaction only 45 days old -- CE3.0 itself cannot use this "
                     "(needs 120-365 days). ADEC's Merkle-log-verified commitment proves non-backdating "
                     "directly, so F29_R_ADEC_GENERALIZED can fire where CE3.0 cannot.",
        graph=graph, dispute_filed_at=dispute_filed_at, provenance=provenance,
    )


def build_f29_adec_backdating_caught() -> Scenario:
    """A fraudulent merchant tries to submit a 'prior transaction commitment'
    fabricated after the dispute already existed. ADEC's verification
    catches it mechanically -- the commitment's TSA-stamped root postdates
    the dispute filing -- and the claim enters at SUBMITTED tier instead,
    where it is not enough on its own to win."""
    graph = EvidenceGraph("case-F29-002")
    provenance = ProvenanceService()
    sdk = MerchantSDK(merchant_id="merchant-99-fraud", provenance=provenance)

    dispute_filed_at = datetime(2026, 7, 1, tzinfo=UTC)
    backdating_attempt_time = dispute_filed_at + timedelta(days=1)  # committed AFTER the dispute -- too late
    receipt = sdk.emit(
        "prior_transaction_commitment",
        b'{"order_id": "fabricated-001", "device_id": "dev-xyz", "ip_address": "198.51.100.9"}',
        event_time=backdating_attempt_time,
    )
    provenance.seal(at=backdating_attempt_time.timestamp())
    verification = sdk.reveal(receipt, dispute_filed_at=dispute_filed_at)
    assert not verification.ok, "scenario setup error: this commitment should FAIL to predate the dispute"

    # The failed-verification claim enters at the lowest trust tier, exactly
    # as §A1 specifies ("uncommitted evidence enters at a lower provenance
    # tier rather than being rejected") -- it is not enough alone to satisfy
    # any F29 rule, which is the point of this scenario.
    _assert_predicate(graph, EvidenceNodeType.ATTESTATION, "adec_prior_txn_commitments_verified", False,
                       ProvenanceTier.SUBMITTED, node_id_hint="failed_adec_claim", commitment_id=receipt.commitment_id)
    _assert_predicate(graph, EvidenceNodeType.CLAIM, "device_id_match", True, ProvenanceTier.ASSERTED,
                       node_id_hint="merchant_narrative_claim")

    return Scenario(
        case_id=graph.case_id, reason_code="F29", title="ADEC catches a backdating attempt",
        description="Merchant tries to commit a 'prior transaction' one day AFTER the dispute was filed. "
                     "The Merkle log's TSA-stamped root cannot predate the dispute, so verification fails "
                     "mechanically and the claim is demoted to the lowest trust tier -- not rejected outright, "
                     "but not sufficient to win either.",
        graph=graph, dispute_filed_at=dispute_filed_at, provenance=provenance,
    )


def all_scenarios():
    return [
        build_c08_merchant_wins_clean(),
        build_c08_card_member_wins_clean(),
        build_c08_temporal_contradiction(),
        build_c08_silent_merchant_amex_side_defense(),
        build_c02_merchant_wins_clean(),
        build_c02_card_member_wins_clean(),
        build_f29_adec_generalized_merchant_wins(),
        build_f29_adec_backdating_caught(),
    ]
