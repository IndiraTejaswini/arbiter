"""
Stage events published as each case moves through the pipeline -- Task 3
(real-time tracking). Published to Redis pub/sub on channel `case:{id}`;
arbiter.realtime.sse subscribes and forwards to connected browsers.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Optional

import redis

from arbiter.config import get_settings

# The stages a case stream actually emits, in order. This is the contract
# the frontend's progress indicator renders against, so a stage the pipeline
# publishes and this tuple omits shows up as an un-highlighted step -- which
# is what happened to CHECKING_CHARGEBACK_RIGHT: it is the FIRST stage of
# every adjudication (arbiter.api.orchestration, stage 0) and it was absent
# here and in the UI, so the progress bar moved to 5% with nothing lit.
#
# There is no CLASSIFYING stage. Intent classification happens in
# `POST /v1/disputes`, before a case exists, so there is no `case:{id}`
# channel to publish it on; it was previously published against the
# transaction_id, to a channel with no subscriber by construction.
STAGES = (
    "CHECKING_CHARGEBACK_RIGHT",
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
