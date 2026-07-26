from __future__ import annotations

import uuid

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

from arbiter.realtime.sse import case_event_stream

router = APIRouter(prefix="/v1", tags=["stream"])


@router.get("/cases/{case_id}/stream")
async def stream_case(case_id: uuid.UUID):
    """★ Task 3: SSE stream of GATHERING_NETWORK -> ... -> DECIDED/ESCALATED."""
    return EventSourceResponse(case_event_stream(str(case_id)))
