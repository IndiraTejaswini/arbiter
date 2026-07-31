"""
Adjudication worker.

    python scripts/run_adjudication_worker.py              # loop until stopped
    python scripts/run_adjudication_worker.py --once       # drain and exit
    python scripts/run_adjudication_worker.py --concurrency 4

Safe to run on every replica: `arbiter.decision.jobs.claim` uses
`SELECT ... FOR UPDATE SKIP LOCKED`, so concurrent workers take disjoint
jobs rather than blocking on or duplicating each other's.

Why a separate process rather than a thread in the API: adjudication is
~10 seconds of CPU and blocking I/O. Running it inside the web process
meant one slow case consumed a request-handling thread, and the service
saturated at roughly 4 QPS. Separating them lets the two scale on their
own axes -- web on RPS, workers on queue depth.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arbiter.api.deps import get_abstention_gate, get_registry
from arbiter.config import get_settings
from arbiter.db import models as m
from arbiter.db.session import session_scope
from arbiter.decision import jobs

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger("adjudication-worker")

_shutdown = False


def _request_shutdown(signum, _frame):
    """Finish the job in hand, then stop.

    Killing a worker mid-adjudication is safe -- the job returns to the
    queue via `requeue_stale` -- but it wastes ten seconds of work and
    leaves a case in RUNNING for the stale window. Draining is cheap.
    """
    global _shutdown
    logger.info("signal %s received; finishing the current job then exiting", signum)
    _shutdown = True


def process_one() -> bool:
    """Claim and run one job. Returns False when the queue is empty."""
    with session_scope() as session:
        job = jobs.claim(session)
        if job is None:
            return False

        job_id, case_id = job.job_id, job.case_id
        logger.info("claimed job %s for case %s (attempt %d)", job_id, case_id, job.attempts)

        try:
            case = session.get(m.DisputeCase, case_id)
            if case is None:
                # The case was deleted between enqueue and claim. A missing
                # case is permanent, so fail it out rather than retrying.
                job.attempts = job.max_attempts
                jobs.fail(session, job, f"case {case_id} no longer exists")
                return True

            from arbiter.api.orchestration import adjudicate_case

            outcome = adjudicate_case(session, case, get_registry(), get_abstention_gate())
            jobs.succeed(session, job, outcome.decision_row.decision_id)
            logger.info(
                "job %s finished: case %s -> %s",
                job_id, case_id, outcome.decision_row.outcome.value,
            )
        except Exception as exc:  # noqa: BLE001 -- the worker must survive any case
            session.rollback()
            with session_scope() as retry_session:
                retry = retry_session.get(m.AdjudicationJob, job_id)
                if retry is not None:
                    jobs.fail(retry_session, retry, f"{type(exc).__name__}: {exc}")
            logger.exception("job %s for case %s raised", job_id, case_id)
        return True


def drain(concurrency: int) -> int:
    """Process everything currently visible. Returns how many jobs ran."""
    if concurrency <= 1:
        processed = 0
        while process_one():
            processed += 1
            if _shutdown:
                break
        return processed

    processed = 0
    with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="adjudicate") as pool:
        while not _shutdown:
            futures = [pool.submit(process_one) for _ in range(concurrency)]
            results = [f.result() for f in futures]
            processed += sum(1 for r in results if r)
            if not any(results):
                break
    return processed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="drain the queue once and exit")
    parser.add_argument("--poll", type=float, default=2.0, help="seconds between polls when idle")
    parser.add_argument(
        "--concurrency", type=int, default=1,
        help="jobs in flight per worker process. Each holds a DB connection and "
             "runs two parallel LLM calls, so keep this well under the pool size.",
    )
    args = parser.parse_args()

    signal.signal(signal.SIGINT, _request_shutdown)
    signal.signal(signal.SIGTERM, _request_shutdown)

    settings = get_settings()
    get_registry().load_dir(settings.rulepack_dir)

    # Load real calibration; without it the gate escalates every case, which
    # is correct behaviour but worth logging loudly from a worker.
    from arbiter.main import _load_calibration

    _load_calibration()

    if args.once:
        logger.info("draining the queue once")
        logger.info("processed %d job(s)", drain(args.concurrency))
        return

    logger.info("worker started (poll=%.1fs, concurrency=%d)", args.poll, args.concurrency)
    stale_check = 0.0
    while not _shutdown:
        try:
            # Reclaim jobs abandoned by a dead worker. Without this a crash
            # strands a case in RUNNING forever and the partial unique index
            # then blocks anyone from re-adjudicating it.
            now = time.monotonic()
            if now - stale_check > 60:
                stale_check = now
                with session_scope() as session:
                    requeued = jobs.requeue_stale(session)
                if requeued:
                    logger.warning("requeued %d abandoned job(s)", requeued)

            if drain(args.concurrency) == 0:
                time.sleep(args.poll)
        except Exception:
            # A worker that dies on a transient database blip is a worker
            # that stops draining the queue while regulatory clocks run.
            logger.exception("worker loop error; continuing")
            time.sleep(max(args.poll, 5.0))

    logger.info("worker stopped cleanly")


if __name__ == "__main__":
    main()
