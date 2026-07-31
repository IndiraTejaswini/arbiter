from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from arbiter.auth import Actor, Role, issue_token
from arbiter.auth.deps import get_current_actor
from arbiter.config import get_settings

router = APIRouter(prefix="/v1", tags=["auth"])


class DevTokenRequest(BaseModel):
    role: str  # CARD_MEMBER | MERCHANT | REVIEWER | ADMIN
    bound_id: uuid.UUID | None = None  # card_member_id or merchant_id; required unless REVIEWER/ADMIN
    actor_id: str | None = None


class DevTokenResponse(BaseModel):
    token: str
    expires_in: int


@router.post("/auth/dev-token", response_model=DevTokenResponse)
def issue_dev_token(body: DevTokenRequest):
    """
    DEV/DEMO ONLY -- issues a bearer token for any role/party on request,
    with no actual authentication behind it. This exists so the frontend
    and this build's own demo/eval scripts have something to call without
    standing up a real IdP; it is explicitly NOT a substitute for one.

    GATED: returns 404 unless `ARBITER_ENABLE_DEV_AUTH=true`, which
    defaults to false and which `arbiter.config.validate_for_environment`
    refuses to boot with outside `env=dev`. This route used to be
    registered unconditionally, with its docstring instructing a deployer
    to delete it -- meaning an unauthenticated caller could POST
    `{"role": "ADMIN"}` and receive full administrative authority over
    every case in the system. A route whose security depends on somebody
    remembering to remove it is not secured; the gate is the fix.

    A real deployment leaves this off and issues tokens only from a
    genuine login flow (Amex's card-member/merchant-portal SSO) that
    verifies the caller's actual identity before arbiter.auth.tokens ever
    mints anything. Nothing downstream of `get_current_actor`
    (arbiter.auth.deps) needs to change when that swap happens -- see
    tokens.py's module docstring.
    """
    settings = get_settings()
    if not settings.enable_dev_auth:
        # 404, not 403: an endpoint that is off should be indistinguishable
        # from an endpoint that does not exist. A 403 confirms to an
        # attacker that the route is present and merely disabled.
        raise HTTPException(404, "Not Found")

    try:
        role = Role(body.role)
    except ValueError:
        raise HTTPException(400, f"unknown role {body.role!r}; must be one of {[r.value for r in Role]}")

    if role in (Role.CARD_MEMBER, Role.MERCHANT) and body.bound_id is None:
        raise HTTPException(400, f"bound_id (the {role.value.lower()}'s id) is required for role {role.value}")

    settings = get_settings()
    actor = Actor(
        actor_id=body.actor_id or f"dev-{role.value.lower()}-{uuid.uuid4().hex[:8]}",
        role=role,
        bound_id=str(body.bound_id) if body.bound_id else None,
    )
    token = issue_token(actor, settings.auth_secret, ttl_seconds=settings.auth_token_ttl_seconds)
    return DevTokenResponse(token=token, expires_in=settings.auth_token_ttl_seconds)


# Stream tokens are deliberately short-lived: they travel in a URL query
# parameter (the browser EventSource API cannot set headers), which means
# they land in access logs, proxy logs, and the Referer header. A 60-second
# lifetime bounds the blast radius of that exposure to roughly the time it
# takes to open the connection.
_STREAM_TOKEN_TTL_SECONDS = 60


class StreamTokenResponse(BaseModel):
    access_token: str
    expires_in: int


@router.post("/auth/stream-token", response_model=StreamTokenResponse)
def issue_stream_token(actor: Actor = Depends(get_current_actor)):
    """Exchange a normal bearer token (sent in the Authorization header,
    as usual) for a short-lived token suitable for the SSE query string.

    This exists because `EventSource` cannot set an Authorization header,
    so `GET /v1/cases/{id}/stream` would otherwise be unreachable from a
    browser. Minting a fresh narrow token here -- rather than putting the
    caller's hour-long session token in a URL -- is the difference between
    a bounded exposure and leaking full session authority into every log
    that records a query string.
    """
    settings = get_settings()
    token = issue_token(actor, settings.auth_secret, ttl_seconds=_STREAM_TOKEN_TTL_SECONDS)
    return StreamTokenResponse(access_token=token, expires_in=_STREAM_TOKEN_TTL_SECONDS)
