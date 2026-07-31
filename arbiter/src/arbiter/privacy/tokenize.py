"""
PAN tokenisation at the extraction boundary (PCI DSS v4.0 scope reduction).

Stated as the gap it closes: the architecture asserts "PAN never enters the
application datastore -- tokenise at the edge gateway; downstream services
see a surrogate. This keeps 90% of the system out of PCI DSS CDE scope."
That is one of the strongest business arguments in the design, and there
was **no tokenisation code**. `arbiter.privacy.redact` redacts card numbers
from LLM *prompts* only -- nothing stopped a PAN inside an uploaded receipt
from being extracted into `evidence_node.attrs` as JSONB plaintext, which
is precisely the datastore the claim says a PAN never enters.

The two boundaries are different and both are needed:

    redact.py    -> prompt boundary. Removes PII before it leaves the
                    process toward a model. Destructive, no surrogate,
                    because a prompt never needs to match on the value.
    tokenize.py  -> STORAGE boundary. Replaces a PAN with a surrogate
                    BEFORE it is persisted, so the datastore never holds
                    one. Deterministic, because adjudication legitimately
                    needs to ask "is the PAN on this receipt the same PAN
                    that was charged?" without ever holding either.

Construction: `surrogate = HMAC-SHA256(PAN, tokenization_key)`, truncated
and rendered with the last four digits preserved for human display
(PCI DSS 3.3 permits the first six / last four to be displayed).

- **Irreversible** without the key. Unlike encryption there is no decrypt
  path at all -- this module deliberately exposes none, because a reversible
  "token" keeps the datastore in CDE scope.
- **Deterministic**, so equality matching works across artifacts and cases.
- **Keyed**, so the surrogate is not brute-forceable from the 10^16
  card-number space the way a bare SHA-256 of a PAN would be. This is the
  same reason ADEC salts its commitments (arbiter.provenance.commitment).

Scope, stated honestly: a production deployment tokenises at the EDGE, in a
PCI-validated vault, before bytes reach application code at all -- which
also covers the artifact object store, which this module does not. What
this module guarantees is narrower and still worth having: no PAN reaches
`evidence_node.attrs`, which is the structured datastore every downstream
service, projection, and export reads.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Same candidate pattern as redact.py: a run of 13-19 digits with optional
# single separators, ending on a bare digit so the match's right edge stays
# pinned to the last digit. Luhn is what promotes a candidate to a PAN.
_CARD_CANDIDATE = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")


def _luhn_valid(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


@dataclass(frozen=True)
class TokenizationResult:
    text: str
    tokens_created: int

    @property
    def contained_pan(self) -> bool:
        return self.tokens_created > 0


def _tokenization_key() -> bytes:
    from arbiter.config import get_settings

    settings = get_settings()
    key = settings.pan_tokenization_key
    if key:
        return key.encode("utf-8")
    # Deriving from the audit signing seed is a documented fallback, not a
    # design: it couples two key lifetimes that should rotate independently.
    # Loud, because a deployment that silently derives its tokenisation key
    # from another secret has a rotation problem it does not know about.
    if settings.signing_key_seed:
        logger.warning(
            "ARBITER_PAN_TOKENIZATION_KEY is not set -- deriving the PAN tokenisation "
            "key from ARBITER_SIGNING_KEY_SEED. These should be independent keys with "
            "independent rotation schedules. Set the dedicated key."
        )
        return hashlib.sha256(b"pan-tokenization|" + settings.signing_key_seed.encode()).digest()
    logger.error(
        "No PAN tokenisation key configured. Surrogates will be derived from a "
        "process-local key, so the same PAN will tokenise differently after a "
        "restart and cross-artifact matching will silently stop working. Set "
        "ARBITER_PAN_TOKENIZATION_KEY."
    )
    return b"arbiter-insecure-default-tokenization-key"


def pan_surrogate(pan_digits: str, key: Optional[bytes] = None) -> str:
    """`tok_<16 hex>_<last4>`.

    Last four preserved deliberately: PCI DSS 3.3 permits displaying the
    first six and last four, and an analyst reconciling a receipt against a
    statement line needs *something* human-readable. The other digits are
    unrecoverable from this string without the key.
    """
    key = key or _tokenization_key()
    digest = hmac.new(key, pan_digits.encode("ascii"), hashlib.sha256).hexdigest()
    return f"tok_{digest[:16]}_{pan_digits[-4:]}"


def tokenize_pans(text: str, key: Optional[bytes] = None) -> TokenizationResult:
    """Replace every Luhn-valid card number in `text` with a surrogate.

    Never raises: a tokenisation failure must not lose the evidence, and a
    value that cannot be scanned is returned unchanged rather than dropped.
    """
    if not text:
        return TokenizationResult(text, 0)

    key = key or _tokenization_key()
    out: List[str] = []
    cursor = 0
    count = 0

    for match in _CARD_CANDIDATE.finditer(text):
        digits = re.sub(r"[ -]", "", match.group())
        if not (13 <= len(digits) <= 19 and _luhn_valid(digits)):
            continue
        out.append(text[cursor:match.start()])
        out.append(pan_surrogate(digits, key))
        cursor = match.end()
        count += 1

    if count == 0:
        return TokenizationResult(text, 0)
    out.append(text[cursor:])
    return TokenizationResult("".join(out), count)


def tokenize_extracted_fields(fields: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    """Tokenise every string field value before persistence.

    Returns (new_fields, tokens_created). Does not mutate the input.
    Non-string values are passed through: a bool or float extracted field is
    a derived signal, not free text that could carry a card number.

    Called from `arbiter.ingest.route._extraction_to_node`, i.e. BEFORE an
    EvidenceNode exists -- so no code path can persist an untokenised value
    even by accident.
    """
    out: List[Dict[str, Any]] = []
    total = 0
    for field in fields:
        value = field.get("value")
        if not isinstance(value, str):
            out.append(dict(field))
            continue
        result = tokenize_pans(value)
        new_field = dict(field)
        if result.contained_pan:
            new_field["value"] = result.text
            new_field["pan_tokenized"] = True
            total += result.tokens_created
        out.append(new_field)
    return out, total
