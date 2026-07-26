"""GET /v1/cases/{id}/stream -- Server-Sent Events over Redis pub/sub."""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

import redis.asyncio as aioredis
from sse_starlette.sse import ServerSentEvent

from arbiter.config import get_settings


async def case_event_stream(case_id: str) -> AsyncIterator[ServerSentEvent]:
    settings = get_settings()
    client = aioredis.from_url(settings.redis_url, decode_responses=True)
    pubsub = client.pubsub()
    channel = f"case:{case_id}"
    await pubsub.subscribe(channel)
    try:
        yield ServerSentEvent(data=json.dumps({"stage": "CONNECTED", "case_id": case_id}), event="connected")
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=15.0)
            if message is None:
                yield ServerSentEvent(data="", event="ping")
                continue
            data = message["data"]
            yield ServerSentEvent(data=data, event="stage")
            try:
                payload = json.loads(data)
                if payload.get("stage") in ("DECIDED", "ESCALATED"):
                    break
            except (json.JSONDecodeError, TypeError):
                pass
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.close()
        await client.close()
