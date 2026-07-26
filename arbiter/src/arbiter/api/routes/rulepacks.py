from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from arbiter.api.deps import get_registry

router = APIRouter(prefix="/v1", tags=["rulepacks"])


@router.get("/rulepacks/{content_hash}")
def get_rulepack(content_hash: str, registry=Depends(get_registry)):
    pack = registry.by_hash(content_hash)
    if pack is None:
        raise HTTPException(404, "no rulepack with that content hash is loaded")
    return {
        "rulepack_id": pack.rulepack_id,
        "reason_code": pack.reason_code,
        "version": pack.version,
        "content_hash": pack.content_hash(),
        "decision_predicates": pack.decision_predicates,
        "predicate_schema": list(pack.predicate_schema),
        "rules": [
            {"rule_id": r.rule_id, "head": r.head,
             "body": [f"not {l.predicate}" if l.negated else l.predicate for l in r.body],
             "description": r.description}
            for r in pack.rules
        ],
    }
