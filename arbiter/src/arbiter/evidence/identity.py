"""
Identity coherence layer (§A6, layer 3): the address / device / IP / email
subgraph should cohere around the parties actually involved in the
transaction. This is deliberately narrow and mechanical -- it is a
first-party-misuse *signal*, not a fraud-detection model (the problem
statement excludes fraud detection; per §5.1 idea 23 this stays in scope
only insofar as it feeds adjudication of a dispute already filed).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple


@dataclass(frozen=True)
class IdentityAssertion:
    entity_key: str  # what this assertion is about, e.g. "shipping_address"
    value: str
    node_id: str
    source: str  # e.g. "order_record", "device_session", "cardholder_narrative"


@dataclass(frozen=True)
class IdentityContradiction:
    kind: str
    severity: str
    description: str
    node_ids: Tuple[str, ...]


def _normalize(value: str) -> str:
    return " ".join(value.strip().lower().split())


def detect_identity_incoherence(
    assertions: List[IdentityAssertion],
    linked_keys: Tuple[str, ...] = ("shipping_address", "billing_address", "device_id", "ip_address", "email"),
) -> List[IdentityContradiction]:
    """
    Within each linked identity dimension, evidence nodes disagreeing about
    the value (after normalization) is a coherence signal -- e.g. the order
    record's shipping address does not match the address the delivery
    carrier actually scanned at.
    """
    by_key: Dict[str, List[IdentityAssertion]] = {}
    for a in assertions:
        if a.entity_key in linked_keys:
            by_key.setdefault(a.entity_key, []).append(a)

    out: List[IdentityContradiction] = []
    for key, group in by_key.items():
        normalized = {_normalize(a.value) for a in group}
        if len(normalized) > 1:
            out.append(
                IdentityContradiction(
                    kind="IDENTITY_MISMATCH",
                    severity="MEDIUM",
                    description=(
                        f"'{key}' disagrees across sources: "
                        + "; ".join(f"{a.source}={a.value!r}" for a in group)
                    ),
                    node_ids=tuple(a.node_id for a in group),
                )
            )
    return out
