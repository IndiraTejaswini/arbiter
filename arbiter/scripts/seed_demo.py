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

import random
import sys
import uuid
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arbiter.db.models import SeedTransaction
from arbiter.db.session import session_scope
from arbiter.network.loader import NetworkFacts
from datagen.world import generate_world
from datagen.outcome import true_outcome

N_PER_REASON_CODE = 40


def _world_to_network_facts(w) -> NetworkFacts:
    if w.reason_code == "F29":
        return NetworkFacts(
            avs_result=w.avs_result, cvv_result=w.cvv_result, three_ds_performed=w.three_ds_performed,
            device_matches_prior=w.device_matches_prior, ip_matches_prior=w.ip_matches_prior,
            shipping_matches_prior=w.shipping_matches_prior, user_id_matches_prior=w.user_id_matches_prior,
            prior_undisputed_count=w.prior_undisputed_count, prior_txn_age_days=w.prior_txn_age_days,
            account_takeover_signal=w.account_was_taken_over, velocity_anomaly=w.velocity_anomaly,
        )
    if w.reason_code == "C08":
        return NetworkFacts(
            item_delivered=w.item_delivered, delivered_to_correct_address=w.delivered_to_correct_address,
            signature_required=w.signature_required, signature_captured=w.signature_captured,
            merchant_shipped_before_dispute=w.merchant_shipped_before_dispute,
            delivery_address_mismatch=w.delivery_address_mismatch, carrier_exception=w.carrier_exception,
        )
    return NetworkFacts(
        refund_issued=w.merchant_issued_refund, refund_amount_minor=w.refund_amount_minor,
        expected_refund_minor=w.expected_refund_minor, return_delivered_to_merchant=w.return_delivered_to_merchant,
        return_window_days=w.return_window_days,
        return_requested_days_after_purchase=w.return_requested_days_after_purchase,
        dispute_filed_before_return_received=(w.cm_returned_item and not w.return_delivered_to_merchant),
    )


def main() -> None:
    rng = random.Random(7)
    with session_scope() as session:
        for reason_code in ("F29", "C08", "C02"):
            for i in range(N_PER_REASON_CODE):
                w = generate_world(rng, reason_code, f"seed-{reason_code}-{i}")
                facts = _world_to_network_facts(w)
                row = SeedTransaction(
                    transaction_id=uuid.uuid4(),
                    reason_code=reason_code,
                    card_member_id=uuid.uuid4(),
                    merchant_id=uuid.UUID(int=abs(hash(w.merchant_id)) % (2**128)),
                    amount_minor=w.amount_minor,
                    currency=w.currency,
                    transaction_at=w.transaction_at,
                    network_facts={k: v for k, v in asdict(facts).items() if v is not None},
                    world_truth={
                        "merchant_size_tier": w.merchant_size_tier,
                        "merchant_keeps_records": w.merchant_keeps_records,
                        "merchant_uses_adec": w.merchant_uses_adec,
                        "merchant_responds_to_inquiry": w.merchant_responds_to_inquiry,
                        "true_outcome": true_outcome(w).value,
                    },
                )
                session.add(row)
        session.commit()
        print(f"Seeded {N_PER_REASON_CODE * 3} transactions.")


if __name__ == "__main__":
    main()
