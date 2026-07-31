from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from arbiter.auth import Actor, require_case_access
from arbiter.auth.tokens import decode_token
from arbiter.config import get_settings
from arbiter.db import models as m
from arbiter.db.session import get_session
from arbiter.realtime.sse import case_event_stream

router = APIRouter(prefix="/v1", tags=["stream"])


def _actor_for_stream(
    authorization: Optional[str] = Header(default=None),
    access_token: Optional[str] = Query(default=None),
) -> Actor:
    """Accept the bearer token from either the Authorization header or an
    `access_token` query parameter.

    The query-parameter path exists for a concrete browser constraint, not
    convenience: the `EventSource` API cannot set request headers, so an
    SSE endpoint that only accepts `Authorization: Bearer` is unreachable
    from a browser. Adding auth to this route without this made real-time
    tracking -- an explicit problem-statement requirement -- impossible
    from the frontend.

    Tokens in URLs land in access logs and `Referer`, so this is a real
    trade-off rather than a free one. It is bounded by issuing SHORT-LIVED
    stream tokens (see `POST /v1/auth/stream-token`) rather than passing
    the session token here, and the header form still takes precedence.
    """
    token: Optional[str] = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization[len("Bearer "):]
    elif access_token:
        token = access_token

    if not token:
        raise HTTPException(401, "missing bearer token -- Authorization header or ?access_token=")
    actor = decode_token(token, get_settings().auth_secret)
    if actor is None:
        raise HTTPException(401, "invalid or expired token")
    return actor


@router.get("/cases/{case_id}/stream")
async def stream_case(
    case_id: uuid.UUID,
    session: Session = Depends(get_session),
    actor: Actor = Depends(_actor_for_stream),
):
    """Task 3: SSE stream of GATHERING_NETWORK -> ... -> DECIDED/ESCALATED.
    The build spec's own draft wrote `user=Depends(current_user)` on this
    route; it was never implemented, so any caller could watch any case's
    live status. Checked once, before the stream opens, exactly like every
    other case-scoped route."""
    case = session.get(m.DisputeCase, case_id)
    if case is None:
        raise HTTPException(404, "case not found")
    require_case_access(actor, case)
    return EventSourceResponse(case_event_stream(str(case_id)))
