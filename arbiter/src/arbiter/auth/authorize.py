"""
Party-binding enforcement: a case is visible only to the card member who
filed it, the merchant it was filed against, or a reviewer/admin. Nothing
else. This is the mechanical fix for the gap CLAUDE.md's LLM-boundary
invariants never covered -- authorization has nothing to do with LLM
trust, but a merchant reading another merchant's dispute is exactly as
serious a failure as a hallucinated verdict, just from a different cause,
and it was the first thing left completely open in the original build.

`filter_graph_for_party` is the composition with the disclosure story
(CLAUDE.md's grounding/tier-gating pattern): a merchant should not see a
card member's personal claim narrative or raw identity/PII evidence in
their own case's proof tree, even though the referee itself needs that
evidence to adjudicate. Filtering happens only at the API response
boundary -- derivation, the referee, and the audit trail all still see the
complete graph; only what's *returned to a non-privileged caller* is
narrowed.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from arbiter.db import models as m

from .tokens import Actor, Role

# Node types that carry the card member's personal narrative or identity
# material -- never shown to a merchant, even on the merchant's own case.
# The referee, advocates, and audit trail still operate over the full
# graph; this list only gates what a merchant-scoped API response returns.
_CARD_MEMBER_ONLY_NODE_TYPES = frozenset({"claim", "identity"})


def require_case_access(actor: Actor, case: m.DisputeCase) -> None:
    """Raises 403 unless `actor` is a reviewer/admin, the card member who
    filed `case`, or the merchant `case` was filed against."""
    if actor.is_privileged:
        return
    if actor.role == Role.CARD_MEMBER and actor.bound_id == str(case.card_member_id):
        return
    if actor.role == Role.MERCHANT and actor.bound_id == str(case.merchant_id):
        return
    raise HTTPException(403, "not authorized to access this case")


def require_reviewer(actor: Actor) -> None:
    if not actor.is_privileged:
        raise HTTPException(403, "reviewer/admin role required")


def filter_graph_for_party(nodes: list[dict[str, Any]], actor: Actor) -> list[dict[str, Any]]:
    """Drops card-member-only evidence nodes from a merchant's view of the
    graph/timeline. Reviewers, admins, and the card member themself see
    everything; a merchant does not see the other party's personal claim
    narrative or raw identity evidence, only what the referee derived from
    it (the fired predicates and proof tree, which are never filtered --
    those are the system's own reasoning, not raw evidence)."""
    if actor.role != Role.MERCHANT:
        return nodes
    return [n for n in nodes if n.get("node_type") not in _CARD_MEMBER_ONLY_NODE_TYPES]
