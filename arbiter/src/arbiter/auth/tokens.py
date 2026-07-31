"""
Actor identity and bearer tokens -- the authorization boundary that was
missing entirely from the original build. Every case-scoped route accepted
any caller for any case_id; a merchant could read another merchant's
decision, evidence graph, or audit trail with nothing but the UUID. This
module and `arbiter.auth.authorize` close that gap.

This is not an LLM boundary, so CLAUDE.md's LLM-proposes/verifier-disposes
pattern doesn't directly apply -- but the shape rhymes: nothing calling
`get_current_actor` (arbiter.auth.deps) trusts a bare case_id in a URL to
mean "this caller may see this case." Every claim of identity is decoded
and verified before anything downstream relies on it.

Token format, stated honestly as a hackathon-scoped stand-in for a real IdP
(OAuth/OIDC -- Amex's actual card-member and merchant-portal SSO would issue
these): `base64url(json_payload).base64url(hmac_sha256(payload, secret))`,
the same two-part shape as a JWS compact serialization, verified with a
single shared secret rather than a full JOSE library because nothing here
needs asymmetric keys or algorithm negotiation. Swapping in real OIDC
verification is a drop-in replacement at `decode_token`'s boundary only --
`Actor` and everything that consumes it (`arbiter.auth.authorize`, every
route's `Depends(get_current_actor)`) does not change.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Role(str, Enum):
    CARD_MEMBER = "CARD_MEMBER"
    MERCHANT = "MERCHANT"
    REVIEWER = "REVIEWER"
    ADMIN = "ADMIN"


@dataclass(frozen=True)
class Actor:
    actor_id: str
    role: Role
    # The card_member_id or merchant_id this actor is bound to, as a string
    # UUID. None for REVIEWER/ADMIN, who are not scoped to a single party.
    bound_id: Optional[str] = None

    @property
    def is_privileged(self) -> bool:
        return self.role in (Role.REVIEWER, Role.ADMIN)

    def to_dict(self) -> dict:
        return {"actor_id": self.actor_id, "role": self.role.value, "bound_id": self.bound_id}


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def issue_token(actor: Actor, secret: str, ttl_seconds: int = 3600) -> str:
    """Issues a token for `actor`, valid for `ttl_seconds`. In production
    this is what a real login/SSO flow would call after authenticating the
    caller against Amex's actual identity provider -- never exposed as a
    "become anyone" endpoint outside of the explicitly-dev-only route this
    build wires it to (arbiter.api.routes.auth)."""
    payload = {
        "actor_id": actor.actor_id,
        "role": actor.role.value,
        "bound_id": actor.bound_id,
        "exp": int(time.time()) + ttl_seconds,
    }
    body = _b64url_encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    tag = _b64url_encode(hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{tag}"


def decode_token(token: str, secret: str) -> Optional[Actor]:
    """Returns the Actor the token claims, or None if the signature doesn't
    verify, the token is malformed, or it has expired. Never raises -- every
    caller (arbiter.auth.deps.get_current_actor) treats None as "reject the
    request," never as a hard failure to propagate."""
    try:
        body, tag = token.split(".", 1)
        expected_tag = _b64url_encode(hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(tag, expected_tag):
            return None
        payload = json.loads(_b64url_decode(body))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        role = Role(payload["role"])
        return Actor(actor_id=str(payload["actor_id"]), role=role, bound_id=payload.get("bound_id"))
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None
