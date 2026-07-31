"""
The chargeback right itself, as data: filing window + excluded transactions.

Stated as the gap it closes. Every reason code in Amex's own merchant
chargeback guide has two fields ARBITER had no representation for at all:

    Maximum time a dispute can be raised   120 days from the date American
                                           Express Network processed the
                                           Transaction (4554 adds an
                                           alternate clock capped at 540)
    Excluded Transactions                  "Card Present Transactions",
                                           "Transactions that qualify for
                                           American Express SafeKey Fraud
                                           Liability Shift", ...

Both are dispositive, both are mechanical, and both are *prior to the
merits*. A SafeKey-authenticated transaction is not a case the merchant
happens to win -- it is a case in which the chargeback right does not exist,
which is a materially different fact about the world and a different thing
to tell both parties. Modelling it as a merchant-favouring Horn rule (which
is what F29_R_3DS effectively did, alone) collapses that distinction and
makes the system claim to have weighed evidence it never needed to weigh.

So the gate is separate from, and runs before, `arbiter.horn`. It is data
for the same reason rules are data (CLAUDE.md invariant #9): it is pinned
into `RulePack.content_hash()`, so a decision that turned on an exclusion
can be replayed against exactly the exclusion text that produced it.

WHY THERE IS NO EXPRESSION LANGUAGE HERE. `Condition` is a fixed vocabulary
of typed attribute names and five operators -- no arithmetic, no nesting, no
`eval`, no callables loaded from YAML. A rulepack is untrusted-ish data
loaded from disk at boot; an expression evaluator in it would be a code path
from a YAML file to the interpreter, in the one component whose entire value
proposition is that its behaviour is inspectable. Anything the guide
actually says ("Card Present Transactions", "amount less than or equal to
AU$100 for Contactless") fits in this vocabulary; anything that does not
should become a new named attribute with a derivation someone reviewed,
not a more powerful language.

THE FAIL DIRECTION IS DELIBERATE AND ASYMMETRIC. An unknown attribute value
means an exclusion does NOT fire and the filing window is NOT treated as
breached. That is the opposite of this codebase's usual "fail closed"
default, and on purpose: every other gate here fails closed *for the card
member's protection* (abstain, escalate, ask a human), whereas an exclusion
firing REMOVES a card member's dispute right. Failing closed on an unknown
would silently bar disputes on missing data. Instead the unknown is recorded
in `EligibilityResult.undetermined` and the case proceeds to the merits,
where the merchant can still win on the evidence. Not-excluding wrongly
costs a merchant an argument they can make again downstream; excluding
wrongly costs a card member the case outright, with no downstream at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Tuple


class AttrType(str, Enum):
    BOOL = "BOOL"
    STRING = "STRING"
    INT = "INT"
    DATETIME = "DATETIME"
    STRING_LIST = "STRING_LIST"


@dataclass(frozen=True)
class AttributeSpec:
    """One member of the closed attribute vocabulary a rulepack may reference.

    `source` is not decoration: it records where the value is expected to
    come from, which is the difference between an attribute a real Amex
    deployment can populate and one that only exists because a rule wanted
    it. An exclusion keyed on an attribute nothing populates is an exclusion
    that never fires -- indistinguishable, in production, from not having
    written it.
    """

    name: str
    type: AttrType
    description: str
    source: str


def _spec(name: str, type_: AttrType, description: str, source: str) -> AttributeSpec:
    return AttributeSpec(name=name, type=type_, description=description, source=source)


# The closed vocabulary. Every attribute here is either a field Amex's own
# authorization/settlement record carries, or a date the network stamps --
# see `source`. Adding one is a deliberate act: `arbiter.rulepack.validate`
# rejects at load time any rulepack referencing a name that is not in here,
# so a typo in a YAML exclusion fails the API's boot rather than quietly
# never firing.
ATTRIBUTE_VOCABULARY: Mapping[str, AttributeSpec] = {
    s.name: s
    for s in (
        # -- how the card was presented (guide, Definitions p.33) -----------
        _spec("card_present", AttrType.BOOL,
              "Card Present Charge: the Card was presented at the point of purchase, "
              "including In Person Charges and Charges made at CATs.",
              "authorization record: POS entry mode"),
        _spec("transaction_channel", AttrType.STRING,
              "IN_PERSON | CAT | MAIL | TELEPHONE | INTERNET | RECURRING.",
              "authorization record: POS entry mode / MOTO indicator"),
        _spec("contactless", AttrType.BOOL,
              "Contactless Transaction (Chip Card or Mobile Device read via the "
              "contactless interface at a Contactless-enabled POS device).",
              "authorization record: POS entry mode"),
        _spec("digital_wallet_contactless_initiated", AttrType.BOOL,
              "Digital Wallet Contactless-initiated Transaction (guide, Definitions p.34).",
              "authorization record: wallet indicator"),
        _spec("digital_wallet_application_initiated", AttrType.BOOL,
              "Digital Wallet Application-initiated Transaction: initiated by a digital "
              "wallet via a merchant application, NOT via the contactless interface.",
              "authorization record: wallet indicator"),
        _spec("digital_wallet_mst", AttrType.BOOL,
              "Digital Wallet MST (magnetic secure transmission) Transaction.",
              "authorization record: wallet indicator"),

        # -- chip / fallback ------------------------------------------------
        _spec("chip_transaction", AttrType.BOOL,
              "Processed as a Chip Transaction.",
              "authorization record: POS entry mode"),
        _spec("transaction_certificate_provided", AttrType.BOOL,
              "A validated Transaction Certificate (the Chip Card's digital signature "
              "over the Authorisation) was provided in the Submission.",
              "submission record: EMV TC field"),
        _spec("fallback_transaction", AttrType.BOOL,
              "Identified as a Fallback Transaction in the Submission: a Chip Card "
              "Transaction that could not complete via chip and ran as magnetic stripe.",
              "authorization record: fallback indicator"),
        _spec("magnetic_stripe_full_track_sent", AttrType.BOOL,
              "The full magnetic stripe was sent to the Issuer with the Authorisation Request.",
              "authorization request: track data present"),
        _spec("offline_authorised_by_chip", AttrType.BOOL,
              "The Transaction Certificate shows Authorisation was granted Offline by the "
              "Chip Card application.",
              "submission record: EMV TC decision byte"),

        # -- CNP authentication signals -------------------------------------
        _spec("safekey_authenticated", AttrType.BOOL,
              "Qualifies for American Express SafeKey Fraud Liability Shift "
              "(3-D Secure authenticated).",
              "authorization record: SafeKey / 3DS authentication result"),
        _spec("pcsc_provided", AttrType.BOOL,
              "The Printed Card Security Code was provided to the Issuer during the "
              "Authorisation Request.",
              "authorization request: CSC field present"),
        _spec("pcsc_validation_returned", AttrType.BOOL,
              "The Issuer returned a validation of Yes or No for the PCSC in the "
              "Authorisation Response. FALSE means the Issuer failed to validate -- which "
              "is the condition the 4540 exclusion turns on, not a mismatch.",
              "authorization response: CSC validation result"),
        _spec("avs_address_verified_match", AttrType.BOOL,
              "The Card Member address information was provided during the Authorisation "
              "Request and the Issuer responded, via Automated Address Verification, that "
              "the address matched.",
              "authorization response: AAV result"),
        _spec("physical_goods_shipped_to_verified_address", AttrType.BOOL,
              "The Merchant shipped physical goods to the address the Issuer verified.",
              "carrier record + order record, reconciled"),

        # -- retrieval-request context (guide pp.31-32) ----------------------
        _spec("retrieval_request_code", AttrType.STRING,
              "6003 | 6006 | 6008 | 6013 | 6014 | 6016 -- the Retrieval Request code that "
              "preceded this dispute, if any. Several exclusions turn on it.",
              "issuer case record"),
        _spec("no_signature_no_pin_program", AttrType.BOOL,
              "The Transaction qualifies under the No Signature / No PIN Program.",
              "merchant program flags"),

        # -- amounts ---------------------------------------------------------
        _spec("amount_minor", AttrType.INT,
              "Transaction amount in minor units of `currency`.",
              "settlement record"),
        _spec("currency", AttrType.STRING,
              "ISO-4217 alpha code of the settled amount.",
              "settlement record"),

        # -- cross-code precedence -------------------------------------------
        _spec("qualifies_under_reason_codes", AttrType.STRING_LIST,
              "Other network reason codes this transaction could be charged back under. "
              "The guide uses this as an exclusion in both directions -- 4554 excludes "
              "transactions chargeable under 4513, 4536 excludes those chargeable under "
              "4521 -- so a dispute cannot be routed to the more permissive code.",
              "issuer triage: prior-code qualification check"),

        # -- clocks -----------------------------------------------------------
        _spec("transaction_processed_at", AttrType.DATETIME,
              "The date the American Express Network processed the Transaction. Every "
              "filing window in the guide is measured from this unless stated otherwise.",
              "network processing record"),
        _spec("expected_delivery_at", AttrType.DATETIME,
              "The date the Card Member expected to receive the goods and/or services (4554).",
              "order record / card member statement"),
        _spec("became_aware_at", AttrType.DATETIME,
              "The date the Card Member became aware the expected goods/services would not "
              "be provided (4554).",
              "card member statement / merchant notice"),
        _spec("goods_returned_or_cancelled_at", AttrType.DATETIME,
              "The date the goods and/or services were cancelled, refused or returned (4513).",
              "carrier return scan / merchant record"),
        _spec("credit_acknowledgment_at", AttrType.DATETIME,
              "The date the Merchant provided the Card Member written acknowledgement of "
              "Credit due (4513).",
              "merchant communication on record"),
    )
}

_OPERATORS = ("is", "equals", "one_of", "lte", "gte")


@dataclass(frozen=True)
class Condition:
    """One typed comparison against one vocabulary attribute.

    `evaluate` returns `None`, not `False`, when the attribute is unknown.
    The distinction is the whole point: "this transaction was not card
    present" and "nobody told us how this transaction was presented" must
    not collapse into the same answer at a gate that can end a dispute.
    """

    attribute: str
    operator: str
    value: Any

    def describe(self) -> str:
        return f"{self.attribute} {self.operator} {self.value!r}"

    def evaluate(self, attrs: Mapping[str, Any]) -> Optional[bool]:
        actual = attrs.get(self.attribute)
        if actual is None:
            return None
        if self.operator == "is":
            return bool(actual) is bool(self.value)
        if self.operator == "equals":
            return actual == self.value
        if self.operator == "one_of":
            # STRING_LIST attributes match if they intersect the given set;
            # scalars match by membership. The list form is what makes
            # cross-code precedence ("chargeable under 4513") expressible.
            if isinstance(actual, (list, tuple, set)):
                return bool(set(actual) & set(self.value))
            return actual in self.value
        if self.operator == "lte":
            return actual <= self.value
        if self.operator == "gte":
            return actual >= self.value
        raise ValueError(f"unknown operator {self.operator!r}")  # pragma: no cover - load-time validated

    def canonical(self) -> dict:
        value = sorted(self.value) if isinstance(self.value, (list, tuple, set)) else self.value
        return {"attribute": self.attribute, "operator": self.operator, "value": value}


@dataclass(frozen=True)
class Exclusion:
    """One "Excluded Transactions" bullet from the guide.

    Conditions are ANDed; exclusions are ORed. That matches how the guide
    reads: each bullet is one independent way the right disappears, and a
    bullet with two clauses ("PCSC was provided AND the Issuer failed to
    validate") needs both.
    """

    exclusion_id: str
    description: str
    legal_basis: str
    conditions: Tuple[Condition, ...]

    def canonical(self) -> dict:
        return {
            "exclusion_id": self.exclusion_id,
            "conditions": [c.canonical() for c in self.conditions],
        }


@dataclass(frozen=True)
class FilingWindowBranch:
    """One "Maximum time a dispute can be raised" clause.

    `from_attributes` holds more than one name only for the guide's
    "whichever occurred first" construction (4554: 120 days from the expected
    receipt date OR from the awareness date, *whichever occurred first*).
    Modelling those as two independent branches would be materially more
    permissive than the guide -- an OR over two start dates lets the later
    one govern, which is exactly backwards -- so the earliest known anchor is
    used instead.
    """

    branch_id: str
    days: int
    from_attributes: Tuple[str, ...]
    description: str = ""
    absolute_cap_days: Optional[int] = None
    cap_from_attribute: Optional[str] = None

    def canonical(self) -> dict:
        return {
            "branch_id": self.branch_id,
            "days": self.days,
            "from": list(self.from_attributes),
            "absolute_cap_days": self.absolute_cap_days,
            "cap_from": self.cap_from_attribute,
        }


@dataclass(frozen=True)
class ChargebackRight:
    """Whether Amex may pass this loss to the merchant at all.

    NOT the same question as "must Amex investigate this billing error",
    and the codebase should never let them blur. Reg Z 12 CFR 1026.13 gives
    the card member a right against the ISSUER on the issuer's own clock;
    the network chargeback window here is a merchant-facing right the
    issuer holds. A dispute that misses the 120-day chargeback window is
    still a billing error Amex must resolve -- it just resolves at Amex's
    own cost instead of the merchant's. `arbiter.decision.deadlines` owns
    the Reg Z/Reg E clocks; this owns the network one, and neither reads
    the other's numbers.
    """

    network_code: str
    merchant_challenge_days: int
    filing_window: Tuple[FilingWindowBranch, ...]
    exclusions: Tuple[Exclusion, ...]
    source: str = ""

    def canonical(self) -> dict:
        """The part of this that changes outcomes, canonicalised for
        `RulePack.content_hash()`. `description`/`legal_basis`/`source` are
        excluded for the same reason a Rule's `description` is: prose about
        a check is not the check."""
        return {
            "network_code": self.network_code,
            "merchant_challenge_days": self.merchant_challenge_days,
            "filing_window": [b.canonical() for b in self.filing_window],
            "exclusions": [e.canonical() for e in self.exclusions],
        }


def coerce_attributes(raw: Mapping[str, Any]) -> Dict[str, Any]:
    """Normalise a caller-supplied attribute mapping to the vocabulary's types.

    Unknown names are dropped rather than raising: attributes arrive from a
    JSONB blob that a future ledger integration will widen, and a new field
    in that blob must not take down adjudication. Rulepack-side references
    are the ones validated strictly (at load time), because those are ours.

    Datetimes arrive as ISO strings after a JSONB round-trip -- the same
    coercion `arbiter.network.loader._as_datetime` performs, for the same
    reason, kept local so this package stays stdlib-only.
    """
    out: Dict[str, Any] = {}
    for name, value in raw.items():
        spec = ATTRIBUTE_VOCABULARY.get(name)
        if spec is None or value is None:
            continue
        if spec.type is AttrType.DATETIME:
            parsed = _as_datetime(value)
            if parsed is not None:
                out[name] = parsed
        elif spec.type is AttrType.BOOL:
            out[name] = bool(value)
        elif spec.type is AttrType.INT:
            try:
                out[name] = int(value)
            except (TypeError, ValueError):
                continue
        elif spec.type is AttrType.STRING_LIST:
            if isinstance(value, (list, tuple, set)):
                out[name] = [str(v) for v in value]
        else:
            out[name] = str(value)
    return out


def _as_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None
