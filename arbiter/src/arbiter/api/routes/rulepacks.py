from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from arbiter.api.deps import get_registry
from arbiter.auth import Actor, require_reviewer
from arbiter.auth.deps import get_current_actor

router = APIRouter(prefix="/v1", tags=["rulepacks"])


@router.get("/rulepacks")
def list_rulepacks(registry=Depends(get_registry), actor: Actor = Depends(get_current_actor)):
    """The catalogue of loaded reason codes — NOT the rules.

    Readable by any authenticated caller, and the split from
    `GET /v1/rulepacks/{hash}` below is the whole point. That route returns
    every rule body, i.e. the complete decision function, and is
    reviewer/admin only because handing it to a party to a live dispute —
    alongside the counterfactual ledger, which already tells them the
    minimal fact set that flips their outcome — is the complete toolkit for
    targeting a decision path with fabricated evidence.

    This route returns none of that. It answers a different and entirely
    benign question: *which disputes can I file, and what do they mean?* A
    card member choosing "I never received it" needs the list; nothing here
    reveals how the case will be judged.

    Why it exists at all: the console hardcoded F29/C08/C02 and their
    descriptions in three separate TypeScript files. Dropping a fourth
    rulepack into `rulepacks/amex/` made it adjudicable by the backend and
    invisible in the UI — unfilable and unfilterable — so "adding a reason
    code is a YAML file" was true of the engine and false of the product.
    `RulepackRegistry.network_codes()` even carried a docstring claiming it
    existed "for the API's rulepack listing"; there was no listing.
    """
    out = []
    for code in registry.reason_codes():
        pack = registry.latest(code)
        right = pack.chargeback_right
        out.append({
            "reason_code": code,
            # The four-digit code the published Amex guide uses, and the one
            # a merchant reads off their own "Resolve Disputes" screen.
            # `RulepackRegistry.resolve()` accepts either dialect, so a
            # client can file with whichever its user actually holds.
            "network_code": right.network_code if right else None,
            # Falls back to the code itself rather than emitting an empty
            # label: a rulepack the engine can adjudicate must never be one
            # the console cannot name.
            "title": pack.title or code,
            "description": pack.description,
            "version": pack.version,
            # Lets a client deep-link the full rulepack view without first
            # having to find a decision that pinned this hash.
            "content_hash": pack.content_hash(),
            "rule_count": len(pack.rules),
            "predicate_count": len(pack.predicate_schema),
            # Counts, not contents. That an exclusion exists is public; which
            # conditions fire it is decision logic and stays behind the
            # reviewer-only route.
            "exclusion_count": len(right.exclusions) if right else 0,
            "has_chargeback_right_gate": right is not None,
        })
    return {"rulepacks": out}


@router.get("/rulepacks/{content_hash}")
def get_rulepack(
    content_hash: str, registry=Depends(get_registry), actor: Actor = Depends(get_current_actor)
):
    """Reviewer/admin only. This route returns every rule body verbatim --
    i.e. the complete decision function. Combined with the counterfactual
    ledger (which tells a party the minimal fact set that flips their
    outcome), anonymous access to the rulepack is the complete toolkit for
    targeting a specific decision path with fabricated evidence -- exactly
    the adversary `evals/gaming_resistance.py` measures. Rulepack
    transparency is still real: it is disclosed to compliance, auditors,
    and regulators, who hold this role. It is not disclosed to the parties
    to a live dispute."""
    require_reviewer(actor)
    pack = registry.by_hash(content_hash)
    if pack is None:
        raise HTTPException(404, "no rulepack with that content hash is loaded")
    right = pack.chargeback_right
    return {
        "rulepack_id": pack.rulepack_id,
        "reason_code": pack.reason_code,
        # The four-digit code the Amex chargeback guide and a merchant's own
        # "Resolve Disputes" screen use for this same dispute.
        "network_code": right.network_code if right else None,
        "version": pack.version,
        "content_hash": pack.content_hash(),
        "decision_predicates": pack.decision_predicates,
        "predicate_schema": list(pack.predicate_schema),
        "rules": [
            {"rule_id": r.rule_id, "head": r.head,
             "body": [f"not {l.predicate}" if l.negated else l.predicate for l in r.body],
             "description": r.description, "legal_basis": r.legal_basis}
            for r in pack.rules
        ],
        # The pre-referee gate, disclosed to the same audience and under the
        # same reasoning as the rules: an exclusion can end a dispute
        # outright, so an auditor who can see every rule body but not the
        # conditions under which no rule runs at all has been shown the less
        # consequential half of the decision function.
        "chargeback_right": None if right is None else {
            "source": right.source,
            "merchant_challenge_days": right.merchant_challenge_days,
            "filing_window": [
                {"branch_id": b.branch_id, "days": b.days, "from": list(b.from_attributes),
                 "absolute_cap_days": b.absolute_cap_days, "cap_from": b.cap_from_attribute,
                 "description": b.description}
                for b in right.filing_window
            ],
            "exclusions": [
                {"id": e.exclusion_id, "description": e.description, "legal_basis": e.legal_basis,
                 "when": [c.describe() for c in e.conditions]}
                for e in right.exclusions
            ],
        },
    }
