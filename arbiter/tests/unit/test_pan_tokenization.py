"""
PAN tokenisation at the STORAGE boundary (PCI DSS v4.0 scope reduction).

The architecture asserts "PAN never enters the application datastore ...
this keeps 90% of the system out of PCI DSS CDE scope." There was no
tokenisation code. `privacy/redact.py` scrubbed card numbers from LLM
*prompts* only -- nothing stopped a PAN on an uploaded receipt from being
extracted straight into `evidence_node.attrs` as JSONB plaintext, which is
the exact datastore the claim says a PAN never enters.
"""

from __future__ import annotations

from arbiter.evidence.models import ProvenanceTier
from arbiter.ingest.route import _extraction_to_node
from arbiter.ingest.schemas import ExtractedField, ExtractionResult, SourceRef
from arbiter.privacy.tokenize import (
    pan_surrogate,
    tokenize_extracted_fields,
    tokenize_pans,
)

_KEY = b"test-tokenization-key"
# Luhn-valid test PANs (standard non-issued test numbers).
_VISA = "4111111111111111"
_AMEX = "378282246310005"


def test_a_pan_is_replaced_by_a_surrogate():
    result = tokenize_pans(f"Paid with card {_VISA} on 3 March", key=_KEY)
    assert _VISA not in result.text
    assert result.contained_pan
    assert result.tokens_created == 1
    assert "tok_" in result.text


def test_the_surrogate_is_deterministic():
    """Adjudication legitimately needs to ask 'is the PAN on this receipt
    the same PAN that was charged?' without ever holding either."""
    assert pan_surrogate(_VISA, _KEY) == pan_surrogate(_VISA, _KEY)


def test_different_pans_get_different_surrogates():
    assert pan_surrogate(_VISA, _KEY) != pan_surrogate(_AMEX, _KEY)


def test_the_surrogate_is_keyed_not_a_bare_hash():
    """A bare SHA-256 of a PAN is trivially brute-forceable -- the card
    number space is ~10^16 and GPUs are fast. Keying it is the same reason
    ADEC salts its commitments."""
    assert pan_surrogate(_VISA, b"key-a") != pan_surrogate(_VISA, b"key-b")


def test_last_four_are_preserved_for_display():
    """PCI DSS 3.3 permits displaying the last four; an analyst reconciling
    a receipt against a statement line needs something human-readable."""
    assert pan_surrogate(_VISA, _KEY).endswith("1111")


def test_the_surrogate_does_not_contain_the_pan():
    surrogate = pan_surrogate(_VISA, _KEY)
    assert _VISA not in surrogate
    assert _VISA[:12] not in surrogate  # everything but the last four is gone


def test_there_is_no_detokenize_function():
    """A reversible 'token' keeps the datastore in CDE scope. This module
    deliberately exposes no decrypt path at all."""
    import arbiter.privacy.tokenize as module

    for forbidden in ("detokenize", "decrypt", "reverse", "untokenize", "recover_pan"):
        assert not hasattr(module, forbidden), (
            f"{forbidden}() must not exist -- a reversible surrogate does not reduce PCI scope"
        )


def test_non_luhn_digit_runs_are_left_alone():
    """An order number or tracking number is not a card number."""
    text = "Order 1234567890123456 shipped via tracking 9400111899223197428490"
    assert tokenize_pans(text, key=_KEY).tokens_created == 0


def test_clean_text_is_returned_unchanged():
    text = "Delivered to 1 Cardmember Way on 2026-03-03"
    result = tokenize_pans(text, key=_KEY)
    assert result.text == text
    assert not result.contained_pan


def test_empty_input_is_safe():
    assert tokenize_pans("", key=_KEY).text == ""


def test_multiple_pans_in_one_value_are_all_tokenized():
    result = tokenize_pans(f"charged {_VISA} refunded {_AMEX}", key=_KEY)
    assert result.tokens_created == 2
    assert _VISA not in result.text and _AMEX not in result.text


def test_extracted_fields_are_tokenized_and_flagged():
    fields = [
        {"field_name": "card_number", "value": f"Card: {_VISA}", "confidence": 0.9},
        {"field_name": "amount", "value": 8999, "confidence": 1.0},
        {"field_name": "address", "value": "1 Cardmember Way", "confidence": 0.8},
    ]
    out, count = tokenize_extracted_fields(fields)
    assert count == 1
    assert _VISA not in out[0]["value"]
    assert out[0]["pan_tokenized"] is True
    assert out[1]["value"] == 8999, "non-string values pass through untouched"
    assert "pan_tokenized" not in out[2], "clean fields are not flagged"


def test_tokenize_does_not_mutate_the_input():
    fields = [{"field_name": "pan", "value": f"{_VISA}", "confidence": 0.9}]
    tokenize_extracted_fields(fields)
    assert fields[0]["value"] == _VISA, "input must not be mutated in place"


def test_no_pan_survives_into_an_evidence_node():
    """THE end-to-end guarantee: extraction → EvidenceNode is the only path
    into `evidence_node.attrs`, and a PAN must not survive it."""
    extraction = ExtractionResult(
        artifact_id="art-1",
        document_type="receipt",
        extraction_method="vlm",
        fields=[
            ExtractedField(field_name="payment_method", value=f"AMEX {_AMEX}",
                           confidence=0.92, source_ref=SourceRef(artifact_id="art-1", page=0)),
            ExtractedField(field_name="delivered", value="true",
                           confidence=0.95, source_ref=SourceRef(artifact_id="art-1", page=0)),
        ],
    )
    node = _extraction_to_node("case-1", "art-1", extraction, ProvenanceTier.SUBMITTED)

    serialized = str(node.attrs)
    assert _AMEX not in serialized, "a card number reached evidence_node.attrs"
    assert node.attrs["pans_tokenized"] == 1
    # The non-PAN signal still survives -- tokenisation must not cost evidence.
    assert node.attrs.get("asserts_predicate") == "delivery_confirmed"
