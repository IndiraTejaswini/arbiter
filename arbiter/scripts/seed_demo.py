"""
Seeds `seed_transaction` (the synthetic stand-in for a real Amex ledger,
see arbiter.db.models.SeedTransaction) and creates a handful of dispute
cases against it, so the API has something to adjudicate immediately after
`docker compose up`.

    python scripts/seed_demo.py

Requires a running Postgres (docker compose up -d db) and the schema
migrated (alembic upgrade head).
"""

from __future__ import annotations

import hashlib
import random
import sys
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arbiter.db.models import SeedTransaction
from arbiter.db.session import session_scope
from arbiter.network.loader import NetworkFacts
from datagen.outcome import true_outcome
from datagen.world import generate_world

N_PER_REASON_CODE = 40

# `datagen.world` samples transaction dates from a fixed calendar window
# (2026-01-01 + 0..540 days) because a world model must not know what today
# is -- its job is internally consistent facts, not a plausible ledger. But
# a case is filed at `now`, and the Amex guide's chargeback window is
# measured from the processing date, so seeding those fixed dates verbatim
# made a seeded transaction's age an accident of when someone ran the
# script: run the demo in 2027 and every case would terminate at the
# chargeback-right gate for reasons that have nothing to do with the case.
#
# So the seeder -- not the world model -- anchors each transaction relative
# to seed time, which is what querying a real ledger actually returns. The
# world's internal timings (shipment a day after the transaction, delivery
# three days after that) are preserved by shifting every derived timestamp
# by the same offset.
#
# A deliberate minority land OUTSIDE the 120-day window. Seeding only
# in-window transactions would mean the gate never fires on demo data and
# nobody would see the behaviour until production -- the same
# "fully implemented, never exercised" failure mode that left tier gating a
# silent no-op in an earlier build.
_OUT_OF_WINDOW_SHARE = 0.10


def _transaction_age_days(rng: random.Random) -> int:
    if rng.random() < _OUT_OF_WINDOW_SHARE:
        # Past 120 days from processing. For C08 the alternate expected-
        # delivery clock is also closed at these ages, so these genuinely
        # exercise the gate rather than only its first branch.
        return rng.randint(130, 400)
    return rng.randint(0, 100)


def _world_to_network_facts(w, shift: timedelta, rng: random.Random) -> NetworkFacts:
    # Amounts and identity/timeline observations are populated for every
    # reason code so A6's numeric, identity, and temporal contradiction
    # layers have real input. They carry no predicate and cannot satisfy a
    # rule (arbiter.network.loader._observation) -- they only feed
    # contradiction detection, which in turn gates auto-resolution.
    transaction_at = w.transaction_at + shift
    common = dict(
        order_total_minor=w.amount_minor,
        authorization_minor=w.amount_minor,
        settlement_minor=w.amount_minor,
        currency=w.currency,
        # The date the Amex Network processed the Transaction -- the anchor
        # every filing window in the guide is measured from. Populated
        # explicitly rather than leaning on `load_eligibility_attributes`'
        # transaction-date fallback, so the demo exercises the real path a
        # production ledger integration would take.
        processed_at=transaction_at + timedelta(days=1),
        # Channel attributes the RC 4540 exclusions read. Seeded as FALSE
        # rather than left unknown for the same reason the world model states
        # its correlations in-line: an unknown here would silently mean "the
        # gate could not evaluate this exclusion", and a demo whose every
        # case reports undetermined attributes teaches the wrong thing about
        # what the gate does.
        card_present=False,
        contactless=False,
        digital_wallet_contactless_initiated=False,
        digital_wallet_mst=False,
    )

    if w.reason_code == "F29":
        return NetworkFacts(
            avs_result=w.avs_result, cvv_result=w.cvv_result, three_ds_performed=w.three_ds_performed,
            device_matches_prior=w.device_matches_prior, ip_matches_prior=w.ip_matches_prior,
            shipping_matches_prior=w.shipping_matches_prior, user_id_matches_prior=w.user_id_matches_prior,
            prior_undisputed_count=w.prior_undisputed_count, prior_txn_age_days=w.prior_txn_age_days,
            account_takeover_signal=w.account_was_taken_over, velocity_anomaly=w.velocity_anomaly,
            card_reported_lost_stolen=w.cardholder_reported_lost_stolen,
            # RC 4540 excludes transactions qualifying for SafeKey Fraud
            # Liability Shift outright. `three_ds_performed` is the observed
            # authentication result, which is a different fact -- a 3DS
            # transaction outside the program is still judged on the merits
            # by F29_R_3DS. Modelled here as: most authenticated transactions
            # qualify, some do not.
            safekey_authenticated=bool(w.three_ds_performed) and rng.random() < 0.8,
            pcsc_provided=True,
            pcsc_validation_returned=True,
            avs_address_verified_match=w.avs_result in ("Y", "X"),
            physical_goods_shipped_to_verified_address=False,
            # Identity coherence: when the authorising device/IP does not
            # match the session's, that is a genuine contradiction the
            # identity layer should surface to a reviewer -- it is NOT a
            # predicate and never decides the case.
            auth_device_id=f"dev-{w.case_id}",
            session_device_id=f"dev-{w.case_id}" if w.device_matches_prior else f"dev-{w.case_id}-alt",
            auth_ip_address="203.0.113.10",
            session_ip_address="203.0.113.10" if w.ip_matches_prior else "198.51.100.77",
            **common,
        )
    if w.reason_code == "C08":
        shipment_at = transaction_at + timedelta(days=1)
        return NetworkFacts(
            item_delivered=w.item_delivered, delivered_to_correct_address=w.delivered_to_correct_address,
            signature_required=w.signature_required, signature_captured=w.signature_captured,
            merchant_shipped_before_dispute=w.merchant_shipped_before_dispute,
            delivery_address_mismatch=w.delivery_address_mismatch, carrier_exception=w.carrier_exception,
            shipment_at=shipment_at,
            delivery_at=(shipment_at + timedelta(days=3)) if w.item_delivered else None,
            # RC 4554's alternate clock: "120 days from the date the Card
            # Member expected to receive goods and/or services". Populated so
            # the second filing-window branch is actually evaluated on demo
            # data instead of reporting as undetermined on every C08 case.
            expected_delivery_at=transaction_at + timedelta(days=7),
            # `qualifies_under_reason_codes` is deliberately NOT seeded here,
            # and the reason is worth stating rather than leaving as an
            # omission.
            #
            # RC 4554 excludes transactions chargeable under RC 4513 (Credit
            # Not Presented), and a cancelled-before-shipment order with no
            # credit posted is squarely a 4513 case -- it is also exactly
            # what C08_R6 adjudicates today. Deriving the attribute from
            # `w.cm_cancelled_before_shipment` would therefore route every
            # C08_R6 case to the gate instead and make a shipped rule
            # unreachable, on the strength of an inference this script is not
            # entitled to make: whether a specific dispute is chargeable
            # under another code is an issuer triage determination (see the
            # attribute's `source` in arbiter.eligibility.models), not
            # something derivable from world truth. Left unknown, which means
            # the exclusion does not fire and the case is adjudicated on the
            # merits exactly as before.
            order_shipping_address="1 Cardmember Way, New York, NY 10001",
            carrier_delivery_address=(
                "1 Cardmember Way, New York, NY 10001" if w.delivered_to_correct_address
                else "42 Elsewhere Ave, Newark, NJ 07102"
            ),
            **common,
        )
    return NetworkFacts(
        refund_issued=w.merchant_issued_refund, refund_amount_minor=w.refund_amount_minor,
        expected_refund_minor=w.expected_refund_minor, return_delivered_to_merchant=w.return_delivered_to_merchant,
        return_window_days=w.return_window_days,
        return_requested_days_after_purchase=w.return_requested_days_after_purchase,
        dispute_filed_before_return_received=(w.cm_returned_item and not w.return_delivered_to_merchant),
        return_delivered_at=(transaction_at + timedelta(days=10)) if w.return_delivered_to_merchant else None,
        refund_at=(transaction_at + timedelta(days=12)) if w.merchant_issued_refund else None,
        # RC 4513's second clock: "120 days from the date the goods and/or
        # services were cancelled, refused or returned".
        cancellation_at=(transaction_at + timedelta(days=8)) if w.cm_returned_item else None,
        **common,
    )


def main() -> None:
    rng = random.Random(7)
    now = datetime.now(timezone.utc)
    out_of_window = 0
    with session_scope() as session:
        for reason_code in ("F29", "C08", "C02"):
            for i in range(N_PER_REASON_CODE):
                w = generate_world(rng, reason_code, f"seed-{reason_code}-{i}")
                age_days = _transaction_age_days(rng)
                out_of_window += age_days > 120
                shift = (now - timedelta(days=age_days)) - w.transaction_at
                facts = _world_to_network_facts(w, shift, rng)
                row = SeedTransaction(
                    transaction_id=uuid.uuid4(),
                    reason_code=reason_code,
                    card_member_id=uuid.uuid4(),
                    # hashlib, not hash(): Python string hashing is
                    # randomised per process (PYTHONHASHSEED), so the
                    # previous version produced different merchant ids on
                    # every run of a script whose whole point is a
                    # reproducible seed.
                    merchant_id=uuid.UUID(bytes=hashlib.sha256(w.merchant_id.encode()).digest()[:16]),
                    amount_minor=w.amount_minor,
                    currency=w.currency,
                    transaction_at=w.transaction_at + shift,
                    # The ledger decides the regulatory regime, not the
                    # dispute-creation request. Roughly one in six seeded
                    # transactions is a prepaid/debit product so the Reg E
                    # path (provisional credit, business-day clocks) is
                    # actually exercised by the demo data.
                    reg_regime="REG_E" if rng.random() < 0.15 else "REG_Z",
                    # default=str so the datetimes the contradiction layers
                    # need survive the JSONB round-trip; the loader parses
                    # them back (arbiter.network.loader._as_datetime).
                    network_facts={
                        k: (v.isoformat() if hasattr(v, "isoformat") else v)
                        for k, v in asdict(facts).items() if v is not None
                    },
                    world_truth={
                        "merchant_size_tier": w.merchant_size_tier,
                        "merchant_keeps_records": w.merchant_keeps_records,
                        "merchant_uses_adec": w.merchant_uses_adec,
                        "merchant_responds_to_inquiry": w.merchant_responds_to_inquiry,
                        "true_outcome": true_outcome(w).value,
                        # Eval-only, like every other key here: which side of
                        # the 120-day chargeback window this transaction was
                        # seeded on. Never read by adjudication -- the gate
                        # recomputes it from the processing date, and a
                        # world_truth field feeding the decision would be the
                        # same circularity CLAUDE.md invariant #7 forbids.
                        "transaction_age_days": age_days,
                        "seeded_out_of_filing_window": age_days > 120,
                    },
                )
                session.add(row)
        session.commit()
        total = N_PER_REASON_CODE * 3
        print(
            f"Seeded {total} transactions "
            f"({out_of_window} outside the 120-day chargeback window -- those cases "
            f"terminate at the chargeback-right gate with CHARGEBACK_INELIGIBLE)."
        )


if __name__ == "__main__":
    main()
