"""Chargeback-right gate (arbiter.eligibility).

Every case here is transcribed from a specific line of American Express's
own merchant chargeback guide ("Chargeback Codes -- What they mean",
Australian merchant reason codes), so a failure names the sentence it
violates rather than an abstract invariant.

The three properties that carry the most weight, and why:

  * an UNKNOWN attribute never fires an exclusion. This is the one place in
    the codebase that fails open, and it does so because an exclusion
    removes a card member's dispute right outright -- there is no downstream
    for them after it fires;
  * "whichever occurred first" starts the clock at the EARLIER anchor. An
    implementation that ORs two independent branches is more permissive than
    the guide, and would silently be so on every C08 case;
  * the 540-day cap overrides an otherwise-open awareness clock. Without it
    the awareness branch is unbounded.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from arbiter.core.errors import RulepackError
from arbiter.eligibility import (
    ATTRIBUTE_VOCABULARY,
    ChargebackRight,
    Condition,
    Exclusion,
    FilingWindowBranch,
    coerce_attributes,
    evaluate_chargeback_right,
)
from arbiter.rulepack import load_rulepack_dir
from arbiter.rulepack.loader import parse_rulepack
from arbiter.rulepack.validate import validate_rulepack

RULEPACK_DIR = Path(__file__).resolve().parent.parent.parent / "rulepacks" / "amex"


@pytest.fixture(scope="module")
def packs():
    return load_rulepack_dir(RULEPACK_DIR)


def _at(days_ago: int, now: datetime) -> datetime:
    return now - timedelta(days=days_ago)


@pytest.fixture()
def now() -> datetime:
    return datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


# ------------------------------------------------------- exclusions

def test_card_present_transaction_is_excluded_from_4540(packs, now):
    """RC 4540 Excluded Transactions, bullet 1: "Card Present Transactions"."""
    right = packs["F29"].chargeback_right
    result = evaluate_chargeback_right(
        right,
        {"card_present": True, "transaction_processed_at": _at(10, now)},
        filed_at=now,
    )
    assert result.available is False
    assert [f.exclusion_id for f in result.fired_exclusions] == ["F29_EX_CARD_PRESENT"]
    assert "4540" in result.reason


def test_safekey_qualifying_transaction_is_excluded_not_merchant_win(packs, now):
    """RC 4540 bullet 4. The distinction this asserts is the whole point of a
    separate gate: SafeKey is an EXCLUSION, so the outcome must be "no
    chargeback right", never "the merchant proved their case"."""
    result = evaluate_chargeback_right(
        packs["F29"].chargeback_right,
        {"safekey_authenticated": True, "transaction_processed_at": _at(10, now)},
        filed_at=now,
    )
    assert result.available is False
    assert result.fired_exclusions[0].exclusion_id == "F29_EX_SAFEKEY"


def test_pcsc_exclusion_needs_both_clauses(packs, now):
    """RC 4540 bullet 2 fires only when the code was PROVIDED and the issuer
    FAILED to return a validation. A returned "No" is a validation, so a
    merchant does not get the exclusion just because the check failed."""
    right = packs["F29"].chargeback_right
    base = {"transaction_processed_at": _at(10, now)}

    both = evaluate_chargeback_right(
        right, {**base, "pcsc_provided": True, "pcsc_validation_returned": False}, filed_at=now
    )
    assert both.available is False

    issuer_did_validate = evaluate_chargeback_right(
        right, {**base, "pcsc_provided": True, "pcsc_validation_returned": True}, filed_at=now
    )
    assert issuer_did_validate.available is True

    never_provided = evaluate_chargeback_right(
        right, {**base, "pcsc_provided": False, "pcsc_validation_returned": False}, filed_at=now
    )
    assert never_provided.available is True


def test_unknown_attribute_never_excludes_and_is_reported(packs, now):
    """THE fail-direction test. `card_present` unknown must not exclude --
    but must not vanish either, or the gate silently stops running on a
    growing share of traffic as ledger coverage regresses."""
    result = evaluate_chargeback_right(
        packs["F29"].chargeback_right,
        {"transaction_processed_at": _at(10, now)},
        filed_at=now,
    )
    assert result.available is True
    assert result.fired_exclusions == ()
    assert "card_present" in result.undetermined
    assert "safekey_authenticated" in result.undetermined


def test_refuted_condition_suppresses_its_unknown_siblings(packs, now):
    """An exclusion already refuted by a KNOWN condition contributes no
    undetermined attributes: knowing the rest could not have changed the
    answer, and reporting it as a coverage gap would send someone to fix a
    non-problem."""
    result = evaluate_chargeback_right(
        packs["F29"].chargeback_right,
        {"transaction_processed_at": _at(10, now), "pcsc_provided": False},
        filed_at=now,
    )
    assert "pcsc_validation_returned" not in result.undetermined


def test_c02_declares_no_exclusions_and_says_so(packs, now):
    """RC 4513 Excluded Transactions: "None." Distinct from a rulepack that
    simply has not been transcribed yet."""
    right = packs["C02"].chargeback_right
    assert right.exclusions == ()
    result = evaluate_chargeback_right(
        right, {"transaction_processed_at": _at(10, now)}, filed_at=now
    )
    assert result.available is True
    assert result.to_dict()["exclusions_evaluated"] == 0


def test_c08_cross_code_precedence_to_4513(packs, now):
    """RC 4554 Excluded Transactions: "Transactions that could be charged
    back under Reason Code 4513 -- Credit Not Presented"."""
    result = evaluate_chargeback_right(
        packs["C08"].chargeback_right,
        {"transaction_processed_at": _at(10, now), "qualifies_under_reason_codes": ["4513"]},
        filed_at=now,
    )
    assert result.available is False
    assert result.fired_exclusions[0].exclusion_id == "C08_EX_CHARGEABLE_UNDER_4513"


# ------------------------------------------------------- filing window

@pytest.mark.parametrize("code", ["F29", "C08", "C02"])
def test_every_rulepack_uses_the_guides_120_day_window(packs, code):
    """"Maximum time a dispute can be raised: One hundred and twenty (120)
    days" is the same figure on every page of the guide. A rulepack that
    quietly used a different number would be a policy change wearing a
    transcription's clothes."""
    right = packs[code].chargeback_right
    assert right is not None, f"{code} declares no chargeback_right"
    assert right.filing_window, f"{code} declares no filing window"
    assert all(b.days == 120 for b in right.filing_window)
    # "Maximum time to challenge a dispute: Twenty (20) days from the date of
    # Chargeback" -- also identical across every code in the guide, and the
    # same number arbiter.decision.deadlines enforces.
    assert right.merchant_challenge_days == 20


def test_dispute_filed_after_120_days_is_out_of_time(packs, now):
    result = evaluate_chargeback_right(
        packs["F29"].chargeback_right,
        {"transaction_processed_at": _at(121, now), "card_present": False,
         "safekey_authenticated": False, "contactless": False,
         "digital_wallet_contactless_initiated": False, "digital_wallet_mst": False,
         "pcsc_provided": False, "avs_address_verified_match": False},
        filed_at=now,
    )
    assert result.available is False
    assert result.filing_window.timely is False
    assert "outside the 4540 chargeback window" in result.reason


def test_dispute_filed_on_the_last_day_is_in_time(packs, now):
    result = evaluate_chargeback_right(
        packs["F29"].chargeback_right,
        {"transaction_processed_at": _at(120, now)},
        filed_at=now,
    )
    assert result.filing_window.timely is True
    assert result.available is True


def test_c08_alternate_clock_saves_a_late_delivery_dispute(packs, now):
    """The reason C08's window is a list of branches. Goods due 30 days ago
    on a charge processed 200 days ago: the processing branch is closed, the
    expected-delivery branch is open, and the guide's "or" means the dispute
    stands."""
    result = evaluate_chargeback_right(
        packs["C08"].chargeback_right,
        {"transaction_processed_at": _at(200, now), "expected_delivery_at": _at(30, now)},
        filed_at=now,
    )
    assert result.available is True
    assert result.filing_window.timely is True
    branches = {b.branch_id: b for b in result.filing_window.branches}
    assert branches["C08_WINDOW_PROCESSED"].timely is False
    assert branches["C08_WINDOW_EXPECTED_OR_AWARE"].timely is True


def test_c08_whichever_occurred_first_uses_the_earlier_anchor(packs, now):
    """"120 days from (whichever occurred first)". Expected delivery 200 days
    ago, awareness 10 days ago: the EARLIER anchor governs, so the clock
    closed 80 days ago. Anchoring on the later date would keep this dispute
    alive and be more permissive than the guide."""
    result = evaluate_chargeback_right(
        packs["C08"].chargeback_right,
        {"transaction_processed_at": _at(300, now),
         "expected_delivery_at": _at(200, now),
         "became_aware_at": _at(10, now)},
        filed_at=now,
    )
    branch = {b.branch_id: b for b in result.filing_window.branches}["C08_WINDOW_EXPECTED_OR_AWARE"]
    assert branch.anchor_attribute == "expected_delivery_at"
    assert branch.timely is False
    assert result.available is False


def test_c08_540_day_cap_overrides_an_open_awareness_clock(packs, now):
    """"not exceeding five hundred and forty (540) days from the date
    American Express Network processed the Transaction". Awareness 10 days
    ago would leave the branch open on its own terms; the cap closes it."""
    result = evaluate_chargeback_right(
        packs["C08"].chargeback_right,
        {"transaction_processed_at": _at(600, now), "became_aware_at": _at(10, now)},
        filed_at=now,
    )
    assert result.available is False
    branch = {b.branch_id: b for b in result.filing_window.branches}["C08_WINDOW_EXPECTED_OR_AWARE"]
    assert branch.capped_out is True
    assert branch.timely is False


def test_c08_cap_does_not_close_a_branch_inside_540_days(packs, now):
    result = evaluate_chargeback_right(
        packs["C08"].chargeback_right,
        {"transaction_processed_at": _at(400, now), "became_aware_at": _at(10, now)},
        filed_at=now,
    )
    assert result.available is True
    branch = {b.branch_id: b for b in result.filing_window.branches}["C08_WINDOW_EXPECTED_OR_AWARE"]
    assert branch.capped_out is False


def test_no_anchor_date_leaves_the_window_unevaluated_not_breached(packs, now):
    """Fail-open again, on the other half of the gate: with no processing
    date the window cannot be computed, and an uncomputable window must not
    bar a dispute."""
    result = evaluate_chargeback_right(packs["C02"].chargeback_right, {}, filed_at=now)
    assert result.available is True
    assert result.filing_window.timely is None
    assert "could not be evaluated" in result.reason
    assert "transaction_processed_at" in result.undetermined


def test_exclusion_beats_an_open_filing_window(packs, now):
    """Both gates fail at once: the reason must name the exclusion, because
    "you filed in time but this transaction was never chargeable" is the
    accurate thing to tell the card member."""
    result = evaluate_chargeback_right(
        packs["F29"].chargeback_right,
        {"transaction_processed_at": _at(1, now), "card_present": True},
        filed_at=now,
    )
    assert result.available is False
    assert "excluded transaction" in result.reason


def test_no_chargeback_right_block_is_reported_rather_than_assumed():
    result = evaluate_chargeback_right(None, {"card_present": True}, filed_at=datetime.now(timezone.utc))
    assert result.available is True
    assert "declares no chargeback_right" in result.reason


# ------------------------------------------------------- load-time validation

def _minimal_doc(**chargeback_right):
    return {
        "rulepack_id": "test-v1", "reason_code": "TST", "version": "1.0.0",
        "predicate_schema": ["a"],
        "decision_predicates": {"MERCHANT_WINS": "merchant_wins"},
        "rules": [{"rule_id": "R1", "head": "merchant_wins", "body": ["a"]}],
        "chargeback_right": {
            "network_code": "9999",
            "filing_window": [{"branch_id": "W", "days": 120, "from": "transaction_processed_at"}],
            **chargeback_right,
        },
    }


def test_unknown_attribute_in_an_exclusion_fails_the_load():
    """The reason the vocabulary is closed. An exclusion on a misspelled
    attribute never fires, and an exclusion that never fires is
    indistinguishable in production from one nobody wrote."""
    doc = _minimal_doc(exclusions=[
        {"id": "EX", "when": [{"attribute": "card_was_present", "is": True}]}
    ])
    with pytest.raises(RulepackError, match="not in the eligibility attribute vocabulary"):
        validate_rulepack(parse_rulepack(doc))


def test_operator_type_mismatch_fails_the_load():
    doc = _minimal_doc(exclusions=[
        {"id": "EX", "when": [{"attribute": "amount_minor", "is": True}]}
    ])
    with pytest.raises(RulepackError, match="cannot apply to"):
        validate_rulepack(parse_rulepack(doc))


def test_non_date_filing_window_anchor_fails_the_load():
    doc = {
        **_minimal_doc(),
        "chargeback_right": {
            "network_code": "9999",
            "filing_window": [{"branch_id": "W", "days": 120, "from": "amount_minor"}],
        },
    }
    with pytest.raises(RulepackError, match="not a date"):
        validate_rulepack(parse_rulepack(doc))


def test_absolute_cap_without_an_anchor_fails_the_load():
    doc = {
        **_minimal_doc(),
        "chargeback_right": {
            "network_code": "9999",
            "filing_window": [
                {"branch_id": "W", "days": 120, "from": "became_aware_at", "absolute_cap_days": 540}
            ],
        },
    }
    with pytest.raises(RulepackError, match="cap could never be applied"):
        validate_rulepack(parse_rulepack(doc))


def test_exclusion_with_no_conditions_fails_the_load():
    """An empty `when` would exclude every transaction under the reason
    code -- the single most destructive typo available in this file format."""
    doc = _minimal_doc(exclusions=[{"id": "EX", "when": []}])
    with pytest.raises(RulepackError, match="would exclude every transaction"):
        parse_rulepack(doc)


# ------------------------------------------------------- attribute coercion

def test_coercion_drops_unknown_names_but_keeps_vocabulary_ones():
    """Rulepack-side references are validated strictly; ledger-side input is
    not, because a future ledger integration widening its JSONB blob must
    not be able to take adjudication down."""
    out = coerce_attributes({
        "card_present": 1,
        "amount_minor": "2500",
        "transaction_processed_at": "2026-01-01T00:00:00Z",
        "something_a_future_ledger_added": "whatever",
    })
    assert out["card_present"] is True
    assert out["amount_minor"] == 2500
    assert out["transaction_processed_at"] == datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert "something_a_future_ledger_added" not in out


def test_naive_datetimes_are_normalised_to_utc():
    """JSONB round-trips lose tzinfo; comparing a naive datetime against an
    aware `filed_at` raises TypeError, which at this gate would mean an
    exception where a decision belongs."""
    out = coerce_attributes({"transaction_processed_at": "2026-01-01T00:00:00"})
    assert out["transaction_processed_at"].tzinfo is not None


def test_every_vocabulary_attribute_documents_its_source():
    """An attribute with no stated source is one nobody has committed to
    populating -- see AttributeSpec."""
    for name, spec in ATTRIBUTE_VOCABULARY.items():
        assert spec.description.strip(), f"{name} has no description"
        assert spec.source.strip(), f"{name} has no source"


# ------------------------------------------------------- hashing

def test_changing_an_exclusion_changes_the_rulepack_hash():
    """The gate can end a case, so it must be pinned like the rules are --
    otherwise a decision could be replayed against exclusions that are not
    the ones that produced it."""
    base = parse_rulepack(_minimal_doc(exclusions=[
        {"id": "EX", "when": [{"attribute": "card_present", "is": True}]}
    ]))
    altered = parse_rulepack(_minimal_doc(exclusions=[
        {"id": "EX", "when": [{"attribute": "contactless", "is": True}]}
    ]))
    assert base.content_hash() != altered.content_hash()


def test_prose_only_edits_do_not_change_the_rulepack_hash():
    """Same reason `description` is excluded from a Rule's hash: prose about
    a check is not the check, and re-wording a citation must not invalidate
    every decision pinned against it."""
    a = parse_rulepack(_minimal_doc(exclusions=[
        {"id": "EX", "description": "first wording", "legal_basis": "x",
         "when": [{"attribute": "card_present", "is": True}]}
    ]))
    b = parse_rulepack(_minimal_doc(exclusions=[
        {"id": "EX", "description": "second wording", "legal_basis": "y",
         "when": [{"attribute": "card_present", "is": True}]}
    ]))
    assert a.content_hash() == b.content_hash()


def test_rulepack_without_a_gate_keeps_its_pre_gate_hash():
    """Backward compatibility, asserted rather than assumed: adding the
    field must not have moved the hash of any rulepack that does not use
    it, or every decision pinned before this feature became unreplayable."""
    doc = {
        "rulepack_id": "test-v1", "reason_code": "TST", "version": "1.0.0",
        "predicate_schema": ["a"],
        "decision_predicates": {"MERCHANT_WINS": "merchant_wins"},
        "rules": [{"rule_id": "R1", "head": "merchant_wins", "body": ["a"]}],
    }
    pack = parse_rulepack(doc)
    assert pack.chargeback_right is None

    import hashlib
    import json
    legacy_payload = {
        "rulepack_id": "test-v1", "reason_code": "TST", "version": "1.0.0",
        "rules": [{"rule_id": "R1", "head": "merchant_wins", "body": [["a", False]]}],
        "decision_predicates": {"MERCHANT_WINS": "merchant_wins"},
    }
    legacy = hashlib.sha256(json.dumps(legacy_payload, sort_keys=True).encode("utf-8")).hexdigest()
    assert pack.content_hash() == legacy


# ------------------------------------------------------- direct model use

# ------------------------------------------------------- ledger boundary

def test_attributes_come_off_network_facts_not_off_predicates(now):
    """`arbiter.network.load_eligibility_attributes` is a separate channel
    from `load_network_evidence` on purpose: nothing it returns is an
    evidence node, so no rule can read it and no advocate can cite it. If
    `card_present` were ever emitted as a node it would become a merchant's
    argument, when the guide's position is that the dispute was never
    chargeable."""
    from arbiter.network import NetworkFacts, load_eligibility_attributes, load_network_evidence

    facts = NetworkFacts(
        card_present=True, safekey_authenticated=False, contactless=False,
        processed_at=_at(30, now), settlement_minor=8999, currency="USD",
        item_delivered=True,
    )
    attrs = load_eligibility_attributes(facts, transaction_at=_at(31, now))
    assert attrs["card_present"] is True
    assert attrs["transaction_processed_at"] == _at(30, now)
    assert attrs["amount_minor"] == 8999

    nodes = load_network_evidence("case-1", "C08", facts)
    asserted = {n.attrs.get("asserts_predicate") for n in nodes}
    assert "card_present" not in asserted
    assert "safekey_authenticated" not in asserted


def test_transaction_date_is_only_a_fallback_anchor(now):
    """`processed_at` wins when the ledger supplies it. The fallback exists
    because this build's synthetic ledger settles same-day; a real one
    populates the processing date, and substituting the transaction date
    there would yield an anchor that is never later and therefore a deadline
    that can only ever bar a dispute the real anchor allowed."""
    from arbiter.network import NetworkFacts, load_eligibility_attributes

    supplied = load_eligibility_attributes(
        NetworkFacts(processed_at=_at(5, now)), transaction_at=_at(9, now)
    )
    assert supplied["transaction_processed_at"] == _at(5, now)

    fell_back = load_eligibility_attributes(NetworkFacts(), transaction_at=_at(9, now))
    assert fell_back["transaction_processed_at"] == _at(9, now)

    neither = load_eligibility_attributes(NetworkFacts())
    assert "transaction_processed_at" not in neither


def test_c02_cancellation_anchor_falls_back_to_the_return_scan(now):
    """4513's second clock is "the date the goods and/or services were
    cancelled, refused or returned" -- the cancellation timestamp when there
    is one, otherwise the date the return actually reached the merchant."""
    from arbiter.network import NetworkFacts, load_eligibility_attributes

    cancelled = load_eligibility_attributes(NetworkFacts(cancellation_at=_at(4, now)))
    assert cancelled["goods_returned_or_cancelled_at"] == _at(4, now)

    returned = load_eligibility_attributes(NetworkFacts(return_delivered_at=_at(6, now)))
    assert returned["goods_returned_or_cancelled_at"] == _at(6, now)


def test_ineligible_narration_states_the_basis_and_preserves_reg_z(packs, now):
    """What a party is actually told. Two things must always appear: the
    guide citation the finding rests on, and the fact that this decides the
    merchant's liability only -- a card member whose chargeback right has
    lapsed has NOT lost their Reg Z/Reg E rights against the issuer, and the
    text must not imply otherwise."""
    from arbiter.api.orchestration import _ineligible_narration

    result = evaluate_chargeback_right(
        packs["F29"].chargeback_right,
        {"card_present": True, "transaction_processed_at": _at(10, now)},
        filed_at=now,
    )
    text = _ineligible_narration(result, "F29", "4540")
    assert "Card Present Transactions" in text
    assert "RC 4540 Excluded Transactions" in text
    assert "12 CFR 1026.13" in text and "1005.11" in text
    assert "merchant's liability only" in text


def test_ineligible_narration_explains_a_closed_window(packs, now):
    from arbiter.api.orchestration import _ineligible_narration

    result = evaluate_chargeback_right(
        packs["C02"].chargeback_right,
        {"transaction_processed_at": _at(200, now)},
        filed_at=now,
    )
    text = _ineligible_narration(result, "C02", "4513")
    assert "C02_WINDOW_PROCESSED" in text
    assert "transaction_processed_at" in text


def test_conditions_within_one_exclusion_are_anded():
    right = ChargebackRight(
        network_code="0000", merchant_challenge_days=20,
        filing_window=(FilingWindowBranch("W", 120, ("transaction_processed_at",)),),
        exclusions=(Exclusion("EX", "both", "basis", (
            Condition("card_present", "is", True),
            Condition("contactless", "is", True),
        )),),
    )
    now_ = datetime.now(timezone.utc)
    attrs = {"transaction_processed_at": now_ - timedelta(days=1)}
    assert evaluate_chargeback_right(right, {**attrs, "card_present": True, "contactless": True}, now_).available is False
    assert evaluate_chargeback_right(right, {**attrs, "card_present": True, "contactless": False}, now_).available is True
