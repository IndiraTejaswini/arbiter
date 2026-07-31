"""
observe(): World -> the lossy, provenance-tiered evidence a real system
would actually have.

This is the one place in datagen/ allowed to know about arbiter.evidence
node types and provenance tiers (import-linter only forbids world.py and
outcome.py from reaching into the rulepack layer) -- it is NOT allowed to
know the rulepack's predicate semantics, only which predicate *names* the
loaded rulepacks expect, which it treats as opaque strings.

Three structural properties this function is responsible for, all stated
in the build spec:

  1. NETWORK-tier evidence (auth, settlement, AVS, CVV, 3DS, device,
     carrier/refund data flowing through Amex's own network) is ALWAYS
     present, independent of merchant participation -- this is what makes
     "silent merchant, adjudicated on Amex data alone" possible.
  2. Merchant-submitted evidence is gated by `merchant_keeps_records`: if a
     merchant doesn't keep records, that evidence doesn't exist even when
     the underlying fact is true -- this is the R13 structural inequity,
     reproduced honestly rather than assumed away.
  3. `merchant_uses_adec` merchants get a REAL commit/reveal cycle through
     arbiter.provenance (not a synthetic COMMITTED tag) -- the ADEC
     verification path is genuinely exercised, not stubbed.

8% per-node extraction noise (corrupted field + degraded confidence) is
injected on party-submitted (SUBMITTED/ASSERTED) evidence only -- NETWORK
data is treated as clean, since it never passes through a lossy
OCR/VLM/manual-entry step.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from arbiter.evidence.graph import EvidenceGraph
from arbiter.evidence.models import EvidenceNode, EvidenceNodeType, ProvenanceTier
from arbiter.provenance.commitment import ProvenanceService
from arbiter.provenance.merkle import TransparencyLog

from .world import World

NOISE_RATE = 0.08


@dataclass
class ObservedCase:
    world: World
    graph: EvidenceGraph
    provenance: Optional[ProvenanceService]
    merchant_silent: bool


def _assert(
    graph: EvidenceGraph,
    node_type: EvidenceNodeType,
    predicate: str,
    value: bool,
    tier: ProvenanceTier,
    rng: random.Random,
    confidence: float = 1.0,
    commitment_id: Optional[str] = None,
    label: str = "",
    **extra,
) -> EvidenceNode:
    noisy_value = value
    noisy_conf = confidence
    if tier in (ProvenanceTier.SUBMITTED, ProvenanceTier.ASSERTED) and rng.random() < NOISE_RATE:
        noisy_value = not value
        noisy_conf = min(noisy_conf, 0.6) * rng.uniform(0.5, 0.9)

    attrs = {"asserts_predicate": predicate, "predicate_value": noisy_value, "label": label or predicate}
    attrs.update(extra)
    node = EvidenceNode(
        case_id=graph.case_id, node_type=node_type, attrs=attrs, provenance=tier,
        extract_conf=noisy_conf, commitment_id=commitment_id,
    )
    graph.add_node(node)
    return node


def _adec_commit_and_verify(
    world: World, rng: random.Random, artifact_type: str, backdated: bool = False
):
    """A real commit/reveal cycle through arbiter.provenance (the same
    ProvenanceService a live POST /v1/commitments route uses), not a
    synthetic tag -- exercises the actual Merkle log. datagen deliberately
    talks to arbiter.provenance directly rather than through sdk/, which is
    the external merchant-facing integration point, not an internal
    dependency."""
    import os

    from arbiter.provenance.commitment import compute_commitment_hash

    provenance = ProvenanceService(TransparencyLog())

    if backdated:
        commit_time = world.dispute_filed_at + timedelta(hours=rng.randint(1, 48))
    else:
        commit_time = world.transaction_at - timedelta(days=rng.randint(0, 5))

    artifact = f'{{"case_id":"{world.case_id}"}}'.encode()
    salt = os.urandom(32)
    commitment_hash = compute_commitment_hash(artifact, salt)
    commitment = provenance.commit(
        merchant_id=world.merchant_id, artifact_type=artifact_type,
        commitment_hash=commitment_hash, event_time=commit_time,
    )
    # ProvenanceService.commit() stamps committed_at with the real wall
    # clock (CLAUDE.md #12: server-observed, never the merchant's claimed
    # event_time) -- correct for the live API, but datagen is constructing
    # a synthetic historical timeline, so backdate the display field to
    # match. The actual non-backdating PROOF is unaffected: it comes from
    # the TSA-stamped seal below (`at=commit_time.timestamp()`), not from
    # this field.
    commitment.committed_at = commit_time
    provenance.seal(at=commit_time.timestamp())
    verification = provenance.reveal_and_verify(
        commitment_id=commitment.commitment_id, artifact=artifact, salt=salt,
        dispute_filed_at=world.dispute_filed_at,
    )
    return provenance, verification, commitment.commitment_id


def _observe_f29(graph: EvidenceGraph, world: World, rng: random.Random) -> Optional[ProvenanceService]:
    _assert(graph, EvidenceNodeType.PRIOR_TRANSACTION, "prior_undisputed_txn_count_ge_2",
            world.prior_undisputed_count >= 2, ProvenanceTier.NETWORK, rng)
    _assert(graph, EvidenceNodeType.PRIOR_TRANSACTION, "prior_txn_120_to_365_days_old",
            120 <= world.prior_txn_age_days <= 365, ProvenanceTier.NETWORK, rng)
    _assert(graph, EvidenceNodeType.DEVICE_SESSION, "device_id_match", world.device_matches_prior,
            ProvenanceTier.NETWORK, rng)
    _assert(graph, EvidenceNodeType.DEVICE_SESSION, "ip_address_match", world.ip_matches_prior,
            ProvenanceTier.NETWORK, rng)
    _assert(graph, EvidenceNodeType.ADDRESS, "shipping_address_match", world.shipping_matches_prior,
            ProvenanceTier.NETWORK, rng)
    _assert(graph, EvidenceNodeType.IDENTITY, "user_id_match", world.user_id_matches_prior,
            ProvenanceTier.NETWORK, rng)
    _assert(graph, EvidenceNodeType.AVS_RESULT, "avs_match", world.avs_result in ("Y", "X"),
            ProvenanceTier.NETWORK, rng)
    _assert(graph, EvidenceNodeType.CVV_RESULT, "cvv_match", world.cvv_result == "M",
            ProvenanceTier.NETWORK, rng)
    _assert(graph, EvidenceNodeType.THREE_DS_RESULT, "three_ds_authenticated", world.three_ds_performed,
            ProvenanceTier.NETWORK, rng)
    _assert(graph, EvidenceNodeType.DEVICE_SESSION, "velocity_anomaly_flagged", world.velocity_anomaly,
            ProvenanceTier.NETWORK, rng)

    if world.cardholder_reported_lost_stolen:
        # NETWORK, not ASSERTED: Reg Z 12 CFR 1026.12(b) turns on notice TO
        # THE ISSUER, and Amex's record of receiving that notice is the
        # legally operative fact. Modelling it as an unverifiable narrative
        # claim was both factually wrong and a gaming vector, because
        # F29_R_LOST_STOLEN decides the case on this predicate alone.
        # ATTESTATION rather than CLAIM: this is the issuer attesting that
        # notice was received, not the card member's own narrative.
        _assert(graph, EvidenceNodeType.ATTESTATION, "cardholder_reported_card_lost_stolen", True,
                ProvenanceTier.NETWORK, rng, confidence=0.9)
    # account_takeover_signal is Amex's own device/session-discontinuity
    # DETECTOR, not a readout of ground truth -- it must not simply copy
    # `account_was_taken_over` (that would make the F29_R_ATO rule
    # tautologically correct by construction). Modeled as an imperfect
    # detector: high recall, small false-positive rate.
    ato_detected = rng.random() < (0.78 if world.account_was_taken_over else 0.06)
    if ato_detected or world.velocity_anomaly:
        _assert(graph, EvidenceNodeType.DEVICE_SESSION, "account_takeover_signal",
                ato_detected, ProvenanceTier.NETWORK, rng)

    provenance = None
    if world.merchant_uses_adec and world.prior_undisputed_count >= 1:
        provenance, verification, commitment_id = _adec_commit_and_verify(world, rng, "prior_transaction_commitment")
        _assert(graph, EvidenceNodeType.ATTESTATION, "adec_prior_txn_commitments_verified", verification.ok,
                ProvenanceService.provenance_tier_for(verification), rng, commitment_id=commitment_id)
    return provenance


def _observe_c08(graph: EvidenceGraph, world: World, rng: random.Random) -> Optional[ProvenanceService]:
    # NETWORK: Amex's own carrier-integration + AVS data, always present.
    _assert(graph, EvidenceNodeType.DELIVERY_SCAN, "delivery_confirmed", world.item_delivered,
            ProvenanceTier.NETWORK, rng, temporal_fact_key="delivery",
            temporal_value=world.transaction_at + timedelta(days=rng.randint(1, 10)) if world.item_delivered else None)
    _assert(graph, EvidenceNodeType.DELIVERY_SCAN, "tracking_shows_delivered", world.item_delivered,
            ProvenanceTier.NETWORK, rng)
    _assert(graph, EvidenceNodeType.ADDRESS, "address_matches_avs", world.delivered_to_correct_address,
            ProvenanceTier.NETWORK, rng)
    _assert(graph, EvidenceNodeType.DELIVERY_SCAN, "signature_missing",
            world.signature_required and not world.signature_captured, ProvenanceTier.NETWORK, rng)
    _assert(graph, EvidenceNodeType.SHIPMENT, "merchant_shipped_before_dispute",
            world.merchant_shipped_before_dispute, ProvenanceTier.NETWORK, rng,
            temporal_fact_key="shipment", temporal_value=world.transaction_at + timedelta(days=1))
    _assert(graph, EvidenceNodeType.ADDRESS, "delivery_address_mismatch", world.delivery_address_mismatch,
            ProvenanceTier.NETWORK, rng)
    _assert(graph, EvidenceNodeType.DELIVERY_SCAN, "carrier_exception_reported", world.carrier_exception,
            ProvenanceTier.NETWORK, rng)

    # merchant-side, gated by record-keeping (the structural inequity):
    provenance: Optional[ProvenanceService] = None
    if world.merchant_keeps_records and world.is_digital_goods:
        tier = ProvenanceTier.SUBMITTED
        commitment_id = None
        if world.merchant_uses_adec:
            provenance, verification, commitment_id = _adec_commit_and_verify(world, rng, "access_log_commitment")
            tier = ProvenanceService.provenance_tier_for(verification)
        _assert(graph, EvidenceNodeType.SERVICE_ACCESS_LOG, "digital_goods_access_logged",
                world.digital_access_occurred, tier, rng, commitment_id=commitment_id)

    if world.cardholder_confirmed_receipt_comm:
        _assert(graph, EvidenceNodeType.COMMUNICATION, "cardholder_confirmed_receipt", True,
                ProvenanceTier.SUBMITTED, rng)
    if world.cm_cancelled_before_shipment:
        _assert(graph, EvidenceNodeType.COMMUNICATION, "cancellation_requested_before_shipment", True,
                ProvenanceTier.ASSERTED, rng, confidence=0.85)

    if world.merchant_keeps_records and world.merchant_uses_adec and not world.is_digital_goods:
        provenance, verification, commitment_id = _adec_commit_and_verify(world, rng, "shipment_commitment")
        _assert(graph, EvidenceNodeType.ATTESTATION, "adec_shipment_commitment_verified", verification.ok,
                ProvenanceService.provenance_tier_for(verification), rng, commitment_id=commitment_id)
    return provenance


def _observe_c02(graph: EvidenceGraph, world: World, rng: random.Random) -> Optional[ProvenanceService]:
    # NETWORK: refund/settlement data flows through the card network itself,
    # so Amex knows this even if the merchant never responds.
    _assert(graph, EvidenceNodeType.REFUND, "refund_issued", world.merchant_issued_refund,
            ProvenanceTier.NETWORK, rng)
    amount_ok = world.merchant_issued_refund and abs(world.refund_amount_minor - world.expected_refund_minor) <= max(50, world.expected_refund_minor // 100)
    _assert(graph, EvidenceNodeType.REFUND, "refund_amount_matches_expected", amount_ok,
            ProvenanceTier.NETWORK, rng)
    _assert(graph, EvidenceNodeType.REFUND, "partial_refund_issued",
            world.merchant_issued_refund and not amount_ok, ProvenanceTier.NETWORK, rng)
    _assert(graph, EvidenceNodeType.SHIPMENT, "return_delivered_to_merchant", world.return_delivered_to_merchant,
            ProvenanceTier.NETWORK, rng)
    window_expired = world.return_requested_days_after_purchase > world.return_window_days
    _assert(graph, EvidenceNodeType.ORDER, "return_window_expired", window_expired, ProvenanceTier.NETWORK, rng)
    dispute_before_return = world.cm_returned_item and not world.return_delivered_to_merchant
    _assert(graph, EvidenceNodeType.ORDER, "dispute_filed_before_return_received", dispute_before_return,
            ProvenanceTier.NETWORK, rng)

    if world.merchant_keeps_records:
        _assert(graph, EvidenceNodeType.REFUND_POLICY, "refund_policy_disclosed_at_sale",
                world.refund_policy_disclosed, ProvenanceTier.SUBMITTED, rng)
    if world.merchant_promised_refund_comm:
        _assert(graph, EvidenceNodeType.COMMUNICATION, "merchant_refund_promise_on_record", True,
                ProvenanceTier.ASSERTED, rng, confidence=0.85)
    if world.merchant_confirmed_cancellation_comm:
        _assert(graph, EvidenceNodeType.COMMUNICATION, "cancellation_confirmed_by_merchant", True,
                ProvenanceTier.ASSERTED, rng, confidence=0.85)
    if world.service_never_rendered:
        tier = ProvenanceTier.NETWORK if not world.merchant_keeps_records else ProvenanceTier.ASSERTED
        _assert(graph, EvidenceNodeType.SERVICE_ACCESS_LOG, "service_never_rendered", True, tier, rng)
    return None


def observe(world: World, rng: Optional[random.Random] = None) -> ObservedCase:
    rng = rng or random.Random()
    graph = EvidenceGraph(world.case_id)

    dispatch = {"F29": _observe_f29, "C08": _observe_c08, "C02": _observe_c02}
    provenance = dispatch[world.reason_code](graph, world, rng)

    graph.run_contradiction_analysis()

    return ObservedCase(
        world=world,
        graph=graph,
        provenance=provenance,
        merchant_silent=not world.merchant_responds_to_inquiry,
    )
