"""
Stage events published as each case moves through the pipeline -- Task 3
(real-time tracking). Published to Redis pub/sub on channel `case:{id}`;
arbiter.realtime.sse subscribes and forwards to connected browsers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from typing import Any, Optional

import redis

from arbiter.config import get_settings

STAGES = (
    "GATHERING_NETWORK",
    "PARSING_EVIDENCE",
    "VERIFYING_PROVENANCE",
    "BUILDING_GRAPH",
    "CONSTRUCTING_ARGUMENTS",
    "ADJUDICATING",
    "DECIDED",
    "ESCALATED",
)


@dataclass(frozen=True)
class StageEvent:
    case_id: str
    stage: str
    message: str
    progress: float  # 0..1
    timestamp: str

    def to_json(self) -> str:
        return json.dumps(asdict(self))


_redis_client: Optional[redis.Redis] = None


def get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis.from_url(get_settings().redis_url, decode_responses=True)
    return _redis_client


def publish_stage(case_id: str, stage: str, message: str, progress: float) -> None:
    from datetime import datetime, timezone

    event = StageEvent(case_id=case_id, stage=stage, message=message, progress=progress,
                        timestamp=datetime.now(timezone.utc).isoformat())
    try:
        get_redis().publish(f"case:{case_id}", event.to_json())
    except redis.RedisError:
        # Real-time status is a UX nicety, not a correctness dependency
        # (CLAUDE.md: prefer failing closed on DECISIONS, not on telemetry).
        # A Redis outage must never fail the adjudication pipeline itself.
        pass
