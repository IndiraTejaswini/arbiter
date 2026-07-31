"""
Unit coverage for arbiter.auth: token round-trip and the party-binding
guarantee itself, in isolation from FastAPI's request plumbing. This is the
gap the build spec's own draft named (`user=Depends(current_user)` on the
SSE route) and left unimplemented -- these tests exercise the fix directly.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from arbiter.auth.authorize import filter_graph_for_party, require_case_access, require_reviewer
from arbiter.auth.tokens import Actor, Role, decode_token, issue_token


def _fake_case(card_member_id: str, merchant_id: str):
    return SimpleNamespace(card_member_id=uuid.UUID(card_member_id), merchant_id=uuid.UUID(merchant_id))


def test_token_round_trip():
    actor = Actor(actor_id="cm-1", role=Role.CARD_MEMBER, bound_id="abc")
    token = issue_token(actor, secret="s3cret", ttl_seconds=60)
    decoded = decode_token(token, secret="s3cret")
    assert decoded == actor


def test_token_wrong_secret_rejected():
    actor = Actor(actor_id="cm-1", role=Role.CARD_MEMBER, bound_id="abc")
    token = issue_token(actor, secret="s3cret", ttl_seconds=60)
    assert decode_token(token, secret="wrong-secret") is None


def test_token_expired_rejected():
    actor = Actor(actor_id="cm-1", role=Role.CARD_MEMBER, bound_id="abc")
    token = issue_token(actor, secret="s3cret", ttl_seconds=-1)  # already expired
    assert decode_token(token, secret="s3cret") is None


def test_token_tampered_payload_rejected():
    actor = Actor(actor_id="cm-1", role=Role.CARD_MEMBER, bound_id="abc")
    token = issue_token(actor, secret="s3cret", ttl_seconds=60)
    body, tag = token.split(".", 1)
    # Flip a character in the payload -- must fail HMAC verification, not
    # silently decode to a mutated actor.
    tampered_body = body[:-1] + ("A" if body[-1] != "A" else "B")
    assert decode_token(f"{tampered_body}.{tag}", secret="s3cret") is None


def test_malformed_token_rejected():
    assert decode_token("not-a-real-token", secret="s3cret") is None


def test_card_member_can_access_own_case_only():
    cm_id = str(uuid.uuid4())
    other_cm_id = str(uuid.uuid4())
    case = _fake_case(cm_id, str(uuid.uuid4()))

    require_case_access(Actor("cm-1", Role.CARD_MEMBER, cm_id), case)  # does not raise

    with pytest.raises(HTTPException) as exc:
        require_case_access(Actor("cm-2", Role.CARD_MEMBER, other_cm_id), case)
    assert exc.value.status_code == 403


def test_merchant_can_access_own_case_only():
    merchant_id = str(uuid.uuid4())
    case = _fake_case(str(uuid.uuid4()), merchant_id)

    require_case_access(Actor("m-1", Role.MERCHANT, merchant_id), case)  # does not raise

    with pytest.raises(HTTPException) as exc:
        require_case_access(Actor("m-2", Role.MERCHANT, str(uuid.uuid4())), case)
    assert exc.value.status_code == 403


def test_reviewer_and_admin_access_any_case():
    case = _fake_case(str(uuid.uuid4()), str(uuid.uuid4()))
    require_case_access(Actor("r-1", Role.REVIEWER, None), case)
    require_case_access(Actor("a-1", Role.ADMIN, None), case)


def test_require_reviewer_rejects_parties():
    with pytest.raises(HTTPException):
        require_reviewer(Actor("cm-1", Role.CARD_MEMBER, "x"))
    with pytest.raises(HTTPException):
        require_reviewer(Actor("m-1", Role.MERCHANT, "x"))
    require_reviewer(Actor("r-1", Role.REVIEWER, None))  # does not raise


def test_filter_graph_for_party_hides_identity_and_claim_from_merchant_only():
    nodes = [
        {"node_id": "1", "node_type": "identity", "attrs": {}},
        {"node_id": "2", "node_type": "claim", "attrs": {}},
        {"node_id": "3", "node_type": "avs_result", "attrs": {}},
    ]
    merchant = Actor("m-1", Role.MERCHANT, "x")
    filtered = filter_graph_for_party(nodes, merchant)
    assert {n["node_id"] for n in filtered} == {"3"}

    card_member = Actor("cm-1", Role.CARD_MEMBER, "x")
    assert filter_graph_for_party(nodes, card_member) == nodes

    reviewer = Actor("r-1", Role.REVIEWER, None)
    assert filter_graph_for_party(nodes, reviewer) == nodes
