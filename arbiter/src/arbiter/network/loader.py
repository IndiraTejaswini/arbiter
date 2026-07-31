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

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from arbiter.eligibility.models import coerce_attributes
from arbiter.evidence.models import EvidenceNode, EvidenceNodeType, ProvenanceTier


def _as_datetime(value) -> Optional[datetime]:
    """Coerce a JSONB-round-tripped timestamp back to a real datetime.

    `NetworkFacts(**seed.network_facts)` deserialises JSONB, so any
    datetime written at seed time comes back as an ISO-8601 *string*. The
    temporal contradiction layer does interval arithmetic
    (`fi.interval.start - fj.interval.end`) and calls `.isoformat()`, both
    of which fail on a str -- so the layer was protected from crashing only
    by the fact that nothing ever populated these fields. Parsing at the
    boundary is what makes it safe to populate them.
    """
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


@dataclass(frozen=True)
class NetworkFacts:
    """Everything Amex's own systems would know about a transaction and its
    merchant relationship, independent of merchant participation in the
    dispute. Reason-code-specific groups are all-optional; a loader only
    populates the group relevant to the case's reason code."""

    def __post_init__(self) -> None:
        # frozen dataclass: object.__setattr__ is the sanctioned way to
        # normalise in __post_init__.
        for field_name in (
            "delivery_at", "shipment_at", "refund_at", "cancellation_at", "return_delivered_at",
            "processed_at", "expected_delivery_at", "became_aware_at",
        ):
            object.__setattr__(self, field_name, _as_datetime(getattr(self, field_name)))

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
    # Amex's own record of receiving a lost/stolen report. Reg Z
    # 12 CFR 1026.12(b) ends cardholder liability upon NOTICE TO THE
    # ISSUER, so the issuer's record of that notice -- not the card
    # member's assertion of it -- is the operative fact.
    card_reported_lost_stolen: Optional[bool] = None

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
    cancellation_at: Optional[datetime] = None

    # -- C02: credit not processed -------------------------------------------
    refund_issued: Optional[bool] = None
    refund_amount_minor: Optional[int] = None
    expected_refund_minor: Optional[int] = None
    return_delivered_to_merchant: Optional[bool] = None
    return_window_days: Optional[int] = None
    return_requested_days_after_purchase: Optional[int] = None
    dispute_filed_before_return_received: Optional[bool] = None
    refund_at: Optional[datetime] = None
    return_delivered_at: Optional[datetime] = None

    # -- Cross-code: amounts and identity, for A6's numeric and identity ----
    # contradiction layers. These are NOT predicates -- no rule reads them.
    # They exist so `arbiter.evidence.{numeric,identity}` have something to
    # reconcile: both layers were fully implemented, fully tested, and
    # received no input from any production code path, which made
    # `contradiction_clarity` a constant 1.0 on every real case.
    order_total_minor: Optional[int] = None
    authorization_minor: Optional[int] = None
    settlement_minor: Optional[int] = None
    currency: Optional[str] = None
    order_shipping_address: Optional[str] = None
    carrier_delivery_address: Optional[str] = None
    auth_device_id: Optional[str] = None
    session_device_id: Optional[str] = None
    auth_ip_address: Optional[str] = None
    session_ip_address: Optional[str] = None

    # -- Chargeback-right attributes (arbiter.eligibility) ------------------
    # These are NOT predicates either, and for a sharper reason than the
    # contradiction-layer inputs above: they decide whether the reason code's
    # chargeback right exists at all, which is a question asked and answered
    # BEFORE the referee runs. A rule may never read one -- if `card_present`
    # were a Horn predicate, "the card was present" would become an argument
    # a merchant wins with, when the Amex guide's actual position (RC 4540
    # Excluded Transactions) is that the dispute was never chargeable.
    #
    # Every one is Optional and defaults to None, which the gate reads as
    # "unknown" -- and unknown never fires an exclusion. See
    # arbiter.eligibility.models on why that fail direction is the opposite
    # of this codebase's usual one, on purpose.
    processed_at: Optional[datetime] = None            # date the Amex Network processed the Transaction
    card_present: Optional[bool] = None
    transaction_channel: Optional[str] = None          # IN_PERSON|CAT|MAIL|TELEPHONE|INTERNET|RECURRING
    contactless: Optional[bool] = None
    digital_wallet_contactless_initiated: Optional[bool] = None
    digital_wallet_application_initiated: Optional[bool] = None
    digital_wallet_mst: Optional[bool] = None
    chip_transaction: Optional[bool] = None
    transaction_certificate_provided: Optional[bool] = None
    fallback_transaction: Optional[bool] = None
    magnetic_stripe_full_track_sent: Optional[bool] = None
    offline_authorised_by_chip: Optional[bool] = None
    safekey_authenticated: Optional[bool] = None
    pcsc_provided: Optional[bool] = None
    pcsc_validation_returned: Optional[bool] = None
    avs_address_verified_match: Optional[bool] = None
    physical_goods_shipped_to_verified_address: Optional[bool] = None
    no_signature_no_pin_program: Optional[bool] = None
    retrieval_request_code: Optional[str] = None       # 6003|6006|6008|6013|6014|6016
    qualifies_under_reason_codes: Optional[List[str]] = None
    expected_delivery_at: Optional[datetime] = None    # C08/4554 alternate clock
    became_aware_at: Optional[datetime] = None         # C08/4554 alternate clock


# Fixed namespace for deterministic network-node identifiers. Network
# evidence is DERIVED from the ledger, not submitted, so re-deriving it must
# be idempotent: the same case and the same underlying fact must always
# yield the same node_id.
#
# Why this matters beyond tidiness: `adjudicate_case` re-runs the loader on
# every adjudication, and with random UUIDs each run minted a fresh row for
# every network fact while the previous run's rows were still loaded from
# the database -- so a second adjudication saw two nodes per predicate, a
# third saw three. Deleting the old rows instead would have been worse: a
# signed decision's proof tree cites evidence_node_ids, and removing a cited
# node orphans the audit trail of a decision that has already been made.
# Stable ids fix the duplication while leaving every prior citation valid.
_NETWORK_NODE_NAMESPACE = uuid.UUID("6f1d5a52-0000-4000-8000-a12b17e33000")


def _stable_node_id(case_id: str, signature: str) -> str:
    return str(uuid.uuid5(_NETWORK_NODE_NAMESPACE, f"{case_id}|{signature}"))


def _node(node_type: EvidenceNodeType, case_id: str, predicate: str, value: bool, **extra) -> EvidenceNode:
    attrs = {"asserts_predicate": predicate, "predicate_value": value, "label": predicate}
    attrs.update(extra)
    return EvidenceNode(
        case_id=case_id, node_type=node_type, attrs=attrs, provenance=ProvenanceTier.NETWORK,
        node_id=_stable_node_id(case_id, f"predicate:{predicate}"),
    )


def _observation(node_type: EvidenceNodeType, case_id: str, signature: str, **attrs) -> EvidenceNode:
    """A NETWORK-tier node that carries NO predicate -- input for the
    contradiction layers only (A6). Deliberately predicate-free: these feed
    `arbiter.evidence.{numeric,identity,temporal,semantic}`, never
    `arbiter.evidence.derive`, so adding them can shift confidence and
    trigger escalation but can never satisfy a rule.

    `signature` identifies WHAT this observation is about, so re-deriving it
    produces the same node_id -- see _stable_node_id.
    """
    return EvidenceNode(
        case_id=case_id, node_type=node_type, attrs=dict(attrs), provenance=ProvenanceTier.NETWORK,
        node_id=_stable_node_id(case_id, signature),
    )


def _money_nodes(case_id: str, facts: NetworkFacts) -> List[EvidenceNode]:
    """Numeric reconciliation inputs (A6 layer 2): order -> authorization ->
    settlement -> refund. `arbiter.evidence.numeric.reconcile_chain` reads
    exactly these `money_role` keys and previously received them from
    nothing but the hand-built demo scenarios."""
    currency = facts.currency or "USD"
    out: List[EvidenceNode] = []
    for role, amount, node_type in (
        ("order_total", facts.order_total_minor, EvidenceNodeType.ORDER),
        ("authorization", facts.authorization_minor, EvidenceNodeType.AUTHORIZATION),
        ("settlement", facts.settlement_minor, EvidenceNodeType.SETTLEMENT),
        ("refund", facts.refund_amount_minor, EvidenceNodeType.REFUND),
    ):
        if amount is not None:
            out.append(_observation(
                node_type, case_id, signature=f"money:{role}",
                money_role=role, minor_units=int(amount),
                currency=currency, label=f"{role}_amount",
            ))
    return out


def _identity_nodes(case_id: str, facts: NetworkFacts) -> List[EvidenceNode]:
    """Identity coherence inputs (A6 layer 3). `detect_identity_incoherence`
    flags a dimension whose sources disagree -- e.g. the order record's
    shipping address versus the address the carrier actually scanned."""
    out: List[EvidenceNode] = []
    for key, value, source, node_type in (
        ("shipping_address", facts.order_shipping_address, "order_record", EvidenceNodeType.ADDRESS),
        ("shipping_address", facts.carrier_delivery_address, "carrier_scan", EvidenceNodeType.ADDRESS),
        ("device_id", facts.auth_device_id, "authorization", EvidenceNodeType.DEVICE_SESSION),
        ("device_id", facts.session_device_id, "device_session", EvidenceNodeType.DEVICE_SESSION),
        ("ip_address", facts.auth_ip_address, "authorization", EvidenceNodeType.DEVICE_SESSION),
        ("ip_address", facts.session_ip_address, "device_session", EvidenceNodeType.DEVICE_SESSION),
    ):
        if value:
            out.append(_observation(
                node_type, case_id, signature=f"identity:{key}:{source}",
                identity_key=key, identity_value=value,
                identity_source=source, label=f"{key}@{source}",
            ))
    return out


def _timeline_nodes(case_id: str, facts: NetworkFacts) -> List[EvidenceNode]:
    """Temporal ordering inputs (A6 layer 1). `DOMAIN_ORDERING_CONSTRAINTS`
    checks shipment-before-delivery, cancellation-before-shipment, and
    return_shipped-before-return_delivered; without these nodes it had no
    pairs to check on any production case."""
    out: List[EvidenceNode] = []
    for key, ts, node_type in (
        ("shipment", facts.shipment_at, EvidenceNodeType.SHIPMENT),
        ("delivery", facts.delivery_at, EvidenceNodeType.DELIVERY_SCAN),
        ("cancellation", facts.cancellation_at, EvidenceNodeType.COMMUNICATION),
        ("refund_issued", facts.refund_at, EvidenceNodeType.REFUND),
        ("return_delivered", facts.return_delivered_at, EvidenceNodeType.SHIPMENT),
    ):
        if ts is not None:
            out.append(_observation(
                node_type, case_id, signature=f"temporal:{key}",
                temporal_fact_key=key, temporal_value=ts, label=f"{key}_at",
            ))
    return out


def load_eligibility_attributes(
    facts: NetworkFacts, *, transaction_at: Optional[datetime] = None
) -> Dict[str, Any]:
    """Assemble the typed attribute mapping the chargeback-right gate reads.

    Deliberately NOT emitted as evidence nodes. Everything
    `load_network_evidence` returns is something a rule can consume and an
    advocate can cite; these are inputs to a question asked before either
    exists. Keeping them on a separate channel is what makes it structurally
    impossible for `card_present` to end up as a merchant's argument.

    `transaction_at` is a fallback anchor for the processing date, passed by
    the caller rather than read here so the substitution stays visible at the
    call site. It matters which one is used: the guide measures every filing
    window from the date the *Network processed* the Transaction, which in a
    real ledger is at or after the transaction date -- so substituting the
    transaction date yields an anchor that is never later, and therefore a
    deadline that is never later, and therefore can only ever bar a dispute
    the real anchor would have allowed. That is the wrong direction for a
    gate that removes a card member's rights, so the fallback is used only
    because this build's synthetic ledger (`SeedTransaction`) settles
    same-day, making the two identical rather than merely close. A real
    ledger integration populates `processed_at` and drops the fallback.
    """
    raw: Dict[str, Any] = {
        "processed": facts.processed_at or transaction_at,
        "card_present": facts.card_present,
        "transaction_channel": facts.transaction_channel,
        "contactless": facts.contactless,
        "digital_wallet_contactless_initiated": facts.digital_wallet_contactless_initiated,
        "digital_wallet_application_initiated": facts.digital_wallet_application_initiated,
        "digital_wallet_mst": facts.digital_wallet_mst,
        "chip_transaction": facts.chip_transaction,
        "transaction_certificate_provided": facts.transaction_certificate_provided,
        "fallback_transaction": facts.fallback_transaction,
        "magnetic_stripe_full_track_sent": facts.magnetic_stripe_full_track_sent,
        "offline_authorised_by_chip": facts.offline_authorised_by_chip,
        "safekey_authenticated": facts.safekey_authenticated,
        "pcsc_provided": facts.pcsc_provided,
        "pcsc_validation_returned": facts.pcsc_validation_returned,
        "avs_address_verified_match": facts.avs_address_verified_match,
        "physical_goods_shipped_to_verified_address": facts.physical_goods_shipped_to_verified_address,
        "no_signature_no_pin_program": facts.no_signature_no_pin_program,
        "retrieval_request_code": facts.retrieval_request_code,
        "qualifies_under_reason_codes": facts.qualifies_under_reason_codes,
        "amount_minor": facts.settlement_minor or facts.order_total_minor,
        "currency": facts.currency,
        "expected_delivery_at": facts.expected_delivery_at,
        "became_aware_at": facts.became_aware_at,
        # 4513's "the date the goods and/or services were cancelled, refused
        # or returned" -- the cancellation timestamp when there is one, else
        # the date the return actually landed with the merchant.
        "goods_returned_or_cancelled_at": facts.cancellation_at or facts.return_delivered_at,
    }
    attributes = coerce_attributes({k: v for k, v in raw.items() if k != "processed"})
    processed = _as_datetime(raw["processed"])
    if processed is not None:
        attributes["transaction_processed_at"] = processed
    return attributes


def load_network_evidence(case_id: str, reason_code: str, facts: NetworkFacts) -> List[EvidenceNode]:
    """NETWORK-tier evidence, always present. Emits: authorization,
    settlement, avs_result, cvv_result, three_ds_result, device_session,
    descriptor, prior_transaction[] (F29), or the carrier/refund equivalents
    for C08/C02."""
    nodes: List[EvidenceNode] = []

    # Contradiction-layer inputs, emitted for every reason code. These carry
    # no predicate and can never satisfy a rule (see _observation); they
    # exist so A6's numeric/identity/temporal layers have something to
    # reconcile on a real case instead of being structurally inert.
    nodes.extend(_money_nodes(case_id, facts))
    nodes.extend(_identity_nodes(case_id, facts))
    nodes.extend(_timeline_nodes(case_id, facts))

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
        if facts.card_reported_lost_stolen is not None:
            # ATTESTATION, not CLAIM: this is the ISSUER attesting that
            # notice was received, which is what the regulation turns on --
            # not the card member's own narrative about it.
            nodes.append(_node(EvidenceNodeType.ATTESTATION, case_id,
                                "cardholder_reported_card_lost_stolen", facts.card_reported_lost_stolen))

    elif reason_code == "C08":
        if facts.item_delivered is not None:
            # Timestamps now live on dedicated predicate-free observation
            # nodes (_timeline_nodes) so a predicate node carries exactly
            # one job -- and so the temporal layer sees one fact per event
            # rather than one per predicate that happens to mention it.
            nodes.append(_node(EvidenceNodeType.DELIVERY_SCAN, case_id, "delivery_confirmed", facts.item_delivered))
            nodes.append(_node(EvidenceNodeType.DELIVERY_SCAN, case_id, "tracking_shows_delivered", facts.item_delivered))
        if facts.delivered_to_correct_address is not None:
            nodes.append(_node(EvidenceNodeType.ADDRESS, case_id, "address_matches_avs", facts.delivered_to_correct_address))
        if facts.signature_required is not None:
            nodes.append(_node(EvidenceNodeType.DELIVERY_SCAN, case_id, "signature_missing",
                                bool(facts.signature_required and not facts.signature_captured)))
        if facts.merchant_shipped_before_dispute is not None:
            nodes.append(_node(EvidenceNodeType.SHIPMENT, case_id, "merchant_shipped_before_dispute",
                                facts.merchant_shipped_before_dispute))
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
