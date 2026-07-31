"""
Durable adjudication queue.

The defect this fixes: `POST /v1/cases/{id}/adjudicate` was a synchronous
route that held a Starlette threadpool worker for the entire pipeline --
around ten seconds with the LLM advocates running. With a default pool of
40 threads and a SQLAlchemy pool of 15 connections, the service saturated
at roughly 4 QPS and then queued unboundedly with no timeout, no bulkhead,
and no backpressure. A client that gave up got no signal that the work was
still running, and a retry started a second full adjudication of the same
case.

**Why Postgres and not a broker.** At ~10 QPS peak (10M disputes/year is
~2 QPS sustained) `SELECT ... FOR UPDATE SKIP LOCKED` is not a compromise,
it is the correct engineering answer: the queue lives in the same
transaction as the state change, which eliminates the dual-write bug an
external broker introduces at every enqueue. The cutover trigger is stated
rather than guessed -- move to Kafka/Redpanda when sustained ingest exceeds
~2k events/s, or when a third independent consumer group needs replay from
arbitrary offsets. Naming a trigger rather than adopting a broker
pre-emptively is the difference between engineering and stack assembly.

**At most one live job per case**, enforced by a partial unique index
rather than by every caller remembering to check. Two concurrent
adjudications of one case would race on the `case_event` hash chain and
write two decision rows for a single evidence set.

**Failures back off.** A deterministically-failing case would otherwise
spin a worker at full speed until it exhausted `max_attempts`.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from arbiter.db import models as m

logger = logging.getLogger(__name__)

QUEUED = "QUEUED"
RUNNING = "RUNNING"
SUCCEEDED = "SUCCEEDED"
FAILED = "FAILED"

TERMINAL_STATES = frozenset({SUCCEEDED, FAILED})

# Exponential backoff between attempts, capped. Deliberately in seconds
# rather than minutes: an adjudication failure is usually a transient
# dependency blip, and a case sitting in a queue is a regulatory clock
# running.
_BACKOFF_BASE_SECONDS = 15
_BACKOFF_CAP_SECONDS = 600

# A job that has been RUNNING longer than this is presumed dead -- the
# worker crashed, was evicted, or lost its connection -- and is returned to
# the queue. Generous relative to the ~10s pipeline so a slow LLM call is
# never mistaken for a dead worker.
STALE_RUNNING_AFTER = timedelta(minutes=15)


@dataclass(frozen=True)
class JobView:
    job_id: str
    case_id: str
    state: str
    attempts: int
    max_attempts: int
    requested_by: str
    error: Optional[str]
    decision_id: Optional[str]
    enqueued_at: datetime
    started_at: Optional[datetime]
    finished_at: Optional[datetime]

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "case_id": self.case_id,
            "state": self.state,
            "terminal": self.terminal,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "requested_by": self.requested_by,
            "error": self.error,
            "decision_id": self.decision_id,
            "enqueued_at": self.enqueued_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }


def _view(row: m.AdjudicationJob) -> JobView:
    return JobView(
        job_id=str(row.job_id), case_id=str(row.case_id), state=row.state,
        attempts=row.attempts, max_attempts=row.max_attempts,
        requested_by=row.requested_by, error=row.error,
        decision_id=str(row.decision_id) if row.decision_id else None,
        enqueued_at=row.enqueued_at, started_at=row.started_at, finished_at=row.finished_at,
    )


class JobAlreadyQueued(Exception):
    """A live job already exists for this case.

    Not an error condition from the caller's point of view -- it carries
    the existing job so the API can return 202 with it rather than making
    the client handle a conflict for something that is already happening.
    """

    def __init__(self, existing: JobView):
        super().__init__(f"case {existing.case_id} already has a {existing.state} job")
        self.existing = existing


def enqueue(session: Session, case_id: uuid.UUID, requested_by: str) -> JobView:
    """Queue an adjudication. Idempotent per live job: if one is already
    QUEUED or RUNNING for this case, raises `JobAlreadyQueued` carrying it
    rather than creating a duplicate."""
    existing = session.execute(
        select(m.AdjudicationJob)
        .where(m.AdjudicationJob.case_id == case_id, m.AdjudicationJob.state.in_([QUEUED, RUNNING]))
        .limit(1)
    ).scalars().first()
    if existing is not None:
        raise JobAlreadyQueued(_view(existing))

    job = m.AdjudicationJob(
        job_id=uuid.uuid4(), case_id=case_id, state=QUEUED, requested_by=requested_by,
    )
    session.add(job)
    try:
        session.flush()
    except IntegrityError as exc:
        # Lost the race against the partial unique index. That is the index
        # doing its job, not a failure -- re-read and return the winner.
        session.rollback()
        winner = session.execute(
            select(m.AdjudicationJob)
            .where(m.AdjudicationJob.case_id == case_id, m.AdjudicationJob.state.in_([QUEUED, RUNNING]))
            .limit(1)
        ).scalars().first()
        if winner is None:
            raise exc
        raise JobAlreadyQueued(_view(winner)) from exc

    session.commit()
    return _view(job)


def get(session: Session, job_id: uuid.UUID) -> Optional[JobView]:
    row = session.get(m.AdjudicationJob, job_id)
    return _view(row) if row else None


def latest_for_case(session: Session, case_id: uuid.UUID) -> Optional[JobView]:
    row = session.execute(
        select(m.AdjudicationJob)
        .where(m.AdjudicationJob.case_id == case_id)
        .order_by(m.AdjudicationJob.enqueued_at.desc())
        .limit(1)
    ).scalars().first()
    return _view(row) if row else None


def requeue_stale(session: Session, now: Optional[datetime] = None) -> int:
    """Return jobs abandoned by a dead worker to the queue.

    Without this a worker crash strands a case in RUNNING forever, and the
    partial unique index then blocks anyone from re-adjudicating it — a
    single crash would permanently wedge that case.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - STALE_RUNNING_AFTER
    stale = session.execute(
        select(m.AdjudicationJob)
        .where(m.AdjudicationJob.state == RUNNING, m.AdjudicationJob.started_at < cutoff)
        .with_for_update(skip_locked=True)
    ).scalars().all()

    for job in stale:
        logger.warning(
            "requeueing job %s for case %s -- RUNNING since %s with no completion, "
            "presumed abandoned by a dead worker",
            job.job_id, job.case_id, job.started_at,
        )
        job.state = QUEUED
        job.started_at = None
        job.visible_at = now
    session.commit()
    return len(stale)


def claim(session: Session, now: Optional[datetime] = None) -> Optional[m.AdjudicationJob]:
    """Claim the oldest visible queued job for this worker.

    `SKIP LOCKED` is what makes running N workers safe: concurrent claimers
    take disjoint rows instead of blocking on, or duplicating, each other's.
    """
    now = now or datetime.now(timezone.utc)
    job = session.execute(
        select(m.AdjudicationJob)
        .where(m.AdjudicationJob.state == QUEUED, m.AdjudicationJob.visible_at <= now)
        .order_by(m.AdjudicationJob.visible_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).scalars().first()
    if job is None:
        return None

    job.state = RUNNING
    job.started_at = now
    job.attempts += 1
    session.commit()
    return job


def succeed(session: Session, job: m.AdjudicationJob, decision_id: Optional[uuid.UUID]) -> JobView:
    job.state = SUCCEEDED
    job.finished_at = datetime.now(timezone.utc)
    job.decision_id = decision_id
    job.error = None
    session.commit()
    return _view(job)


def fail(session: Session, job: m.AdjudicationJob, error: str) -> JobView:
    """Record a failure, and either schedule a backed-off retry or give up.

    Giving up is a real outcome, not a swallowed error: the case stays
    un-adjudicated and visible in the failed-jobs view, because a case that
    silently never adjudicates while its Reg Z clock runs is worse than one
    that is loudly broken.
    """
    now = datetime.now(timezone.utc)
    # Truncate: an exception repr can be enormous, and the useful signal is
    # always in the first line.
    job.error = error[:2000]

    if job.attempts >= job.max_attempts:
        job.state = FAILED
        job.finished_at = now
        logger.error(
            "job %s for case %s FAILED permanently after %d attempts: %s",
            job.job_id, job.case_id, job.attempts, job.error,
        )
    else:
        delay = min(_BACKOFF_CAP_SECONDS, _BACKOFF_BASE_SECONDS * (2 ** (job.attempts - 1)))
        job.state = QUEUED
        job.started_at = None
        job.visible_at = now + timedelta(seconds=delay)
        logger.warning(
            "job %s for case %s failed (attempt %d/%d), retrying in %ds: %s",
            job.job_id, job.case_id, job.attempts, job.max_attempts, delay, job.error,
        )
    session.commit()
    return _view(job)


def queue_depth(session: Session) -> dict:
    """Queue health. Depth and oldest-queued age are the two numbers that
    tell an operator whether workers are keeping up."""
    rows = session.execute(
        select(m.AdjudicationJob.state, func.count()).group_by(m.AdjudicationJob.state)
    ).all()
    counts = {state: count for state, count in rows}
    oldest = session.execute(
        select(func.min(m.AdjudicationJob.enqueued_at)).where(m.AdjudicationJob.state == QUEUED)
    ).scalar()
    return {
        "queued": counts.get(QUEUED, 0),
        "running": counts.get(RUNNING, 0),
        "succeeded": counts.get(SUCCEEDED, 0),
        "failed": counts.get(FAILED, 0),
        "oldest_queued_at": oldest.isoformat() if oldest else None,
        "oldest_queued_age_seconds": (
            (datetime.now(timezone.utc) - oldest).total_seconds() if oldest else None
        ),
    }


def recent_failures(session: Session, limit: int = 20) -> List[JobView]:
    rows = session.execute(
        select(m.AdjudicationJob)
        .where(m.AdjudicationJob.state == FAILED)
        .order_by(m.AdjudicationJob.finished_at.desc())
        .limit(limit)
    ).scalars().all()
    return [_view(r) for r in rows]
