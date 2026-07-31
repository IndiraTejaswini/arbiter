"""
Unit coverage for arbiter.privacy: PII redaction at the LLM-prompt boundary
(redact.py) and crypto-shredding (shredding.py) -- the mechanism GDPR
Article 17 erasure is built on, and the reason it doesn't conflict with
CLAUDE.md invariant #8 (case_event/decision are append-only): erasure
destroys a key, never mutates a row.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from arbiter.privacy.redact import redact_text
from arbiter.privacy.shredding import SubjectKeyVault, decrypt_extracted_fields, encrypt_extracted_fields, erase_subject


def test_redact_text_catches_card_ssn_email_phone():
    text = "card 4111 1111 1111 1111, ssn 123-45-6789, email a@b.com, phone 555-123-4567"
    redacted, matches = redact_text(text)

    assert "4111" not in redacted
    assert "123-45-6789" not in redacted
    assert "a@b.com" not in redacted
    assert "555-123-4567" not in redacted
    assert {m.kind for m in matches} == {"CREDIT_CARD", "SSN", "EMAIL", "PHONE"}


def test_redact_text_does_not_mangle_surrounding_words():
    """Regression: an early version of the card-number pattern could
    consume the space immediately after a card number, since the digit
    class's optional separator included the boundary space itself."""
    redacted, _ = redact_text("card 4111111111111111 and more text")
    assert redacted == "card [REDACTED_CARD] and more text"


def test_redact_text_leaves_non_luhn_digit_runs_alone():
    """A 16-digit run that fails the Luhn check (a case_id, an order
    number) is not a card number and must not be redacted -- redaction
    that over-triggers on ordinary transaction identifiers would make the
    system less useful without making it any safer."""
    not_a_card = "1234567890123456"  # fails Luhn
    redacted, matches = redact_text(f"order id {not_a_card}")
    assert not_a_card in redacted
    assert matches == []


def test_redact_text_empty_and_clean_input():
    assert redact_text("")[0] == ""
    clean = "nothing sensitive here"
    assert redact_text(clean) == (clean, [])


def test_shredding_round_trip():
    vault = SubjectKeyVault()
    fields = [
        {"field_name": "name", "value": "Jane Doe", "confidence": 0.9, "source_ref": {}},
        {"field_name": "delivered", "value": True, "confidence": 0.9, "source_ref": {}},
    ]
    encrypted = encrypt_extracted_fields(vault, "subject-1", fields)

    assert encrypted[0]["value"] != "Jane Doe"  # string field encrypted
    assert encrypted[1]["value"] is True  # non-string field passed through untouched

    decrypted = decrypt_extracted_fields(vault, "subject-1", encrypted)
    assert decrypted[0]["value"] == "Jane Doe"
    assert decrypted[1]["value"] is True


def test_erasure_makes_ciphertext_permanently_unrecoverable():
    vault = SubjectKeyVault()
    fields = [{"field_name": "name", "value": "Jane Doe", "confidence": 0.9, "source_ref": {}}]
    encrypted = encrypt_extracted_fields(vault, "subject-2", fields)

    assert erase_subject(vault, "subject-2") is True
    assert vault.is_erased("subject-2")

    decrypted_after_erasure = decrypt_extracted_fields(vault, "subject-2", encrypted)
    assert decrypted_after_erasure[0]["value"] == "[ERASED]"

    # New writes for an erased subject must not silently re-establish a
    # key that would make old ciphertext readable again.
    assert vault.encrypt("subject-2", "new data") is None


def test_erasure_is_idempotent_on_unknown_subject():
    vault = SubjectKeyVault()
    assert erase_subject(vault, "never-seen") is False
    assert erase_subject(vault, "never-seen") is False


# -- Read-through: the vault is a cache, not the system of record ---------
#
# `SubjectKeyVault` is explicitly documented as a cache in front of Postgres,
# and the deployment runs several API replicas. But `decrypt` and `is_erased`
# consulted `self._keys` alone, so a key written by replica A was simply
# absent on replica B:
#
#   - B rendered a live card member's evidence as "[ERASED]"
#   - B reported `is_erased() == False` for a subject another replica HAD
#     erased -- wrong in the direction that matters, since callers ask
#     precisely so they can stop handling that subject's data
#
# That is the failure this module's own docstring calls "an unrequested GDPR
# erasure of every card member's evidence", reached horizontally across
# replicas rather than vertically across a restart.


class _FakeStore:
    """A durable store shared between two vaults, standing in for Postgres."""

    def __init__(self):
        self.rows: dict = {}
        self.save_calls = 0

    def load_all(self):
        return dict(self.rows)

    def load_one(self, subject_id):
        return self.rows.get(subject_id)

    def save(self, subject_id, record):
        self.save_calls += 1
        existing = self.rows.get(subject_id)
        if existing is not None:
            return existing  # the row that won
        self.rows[subject_id] = record
        return record

    def erase(self, subject_id, erased_at):
        row = self.rows.get(subject_id)
        if row is not None:
            row.key = None
            row.erased_at = erased_at


def test_a_second_replica_can_decrypt_what_the_first_encrypted():
    """THE regression. Replica B never called rehydrate() after A minted the
    key, so B saw a live subject as erased."""
    from arbiter.privacy.shredding import SubjectKeyVault

    store = _FakeStore()
    replica_a = SubjectKeyVault(store=store)
    replica_b = SubjectKeyVault(store=store)  # cold cache, deliberately

    ciphertext = replica_a.encrypt("subject-1", "14 Rue de la Paix")
    assert ciphertext is not None

    assert replica_b.decrypt("subject-1", ciphertext) == "14 Rue de la Paix", (
        "a replica with a cold cache must read the key through to the store, not "
        "report the subject as erased"
    )


def test_erasure_on_one_replica_is_visible_on_another():
    """The other direction, and the more dangerous one: a replica must not
    answer 'not erased' for a subject who has been erased elsewhere."""
    from arbiter.privacy.shredding import SubjectKeyVault

    store = _FakeStore()
    replica_a = SubjectKeyVault(store=store)
    replica_b = SubjectKeyVault(store=store)

    replica_a.encrypt("subject-2", "sensitive")
    assert replica_a.erase("subject-2") is True

    assert replica_b.is_erased("subject-2") is True
    assert replica_b.decrypt("subject-2", "anything") is None


def test_concurrent_first_write_adopts_the_persisted_key():
    """Two replicas both miss the cache and both mint a key for the same new
    subject. One row wins. The loser must ADOPT it -- before, it kept its own
    key, which was never persisted, so everything it encrypted became
    unrecoverable the moment that process exited."""
    from arbiter.privacy.shredding import SubjectKeyVault

    store = _FakeStore()
    replica_a = SubjectKeyVault(store=store)
    replica_b = SubjectKeyVault(store=store)

    ct_a = replica_a.encrypt("subject-3", "same subject")
    ct_b = replica_b.encrypt("subject-3", "same subject")
    assert ct_a is not None and ct_b is not None

    # The proof: a third, cold reader holding only the persisted key can read
    # BOTH ciphertexts. If the loser had kept its own key, one would be lost.
    reader = SubjectKeyVault(store=store)
    assert reader.decrypt("subject-3", ct_a) == "same subject"
    assert reader.decrypt("subject-3", ct_b) == "same subject"


def test_a_store_outage_does_not_read_as_erasure():
    """A transient lookup failure must not be indistinguishable from a
    subject exercising their right to erasure."""
    from arbiter.privacy.shredding import SubjectKeyVault

    class _BrokenStore(_FakeStore):
        def load_one(self, subject_id):
            raise RuntimeError("database unreachable")

    vault = SubjectKeyVault(store=_BrokenStore())
    # Nothing cached and the store is down: report "no key", never "erased".
    assert vault.decrypt("subject-4", "ciphertext") is None
    assert vault.is_erased("subject-4") is False
