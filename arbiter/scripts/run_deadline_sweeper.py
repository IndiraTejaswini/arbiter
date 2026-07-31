"""
Regulatory clock runner.

    python scripts/run_deadline_sweeper.py            # one pass, then exit
    python scripts/run_deadline_sweeper.py --loop 60  # every 60 seconds

Deploy as a Kubernetes CronJob or a sidecar. It is safe to run on every
replica simultaneously: `sweep_deadlines` claims cases with
`SELECT ... FOR UPDATE SKIP LOCKED`, so concurrent runners take disjoint
batches rather than duplicating work.

Why a separate process rather than a thread in the API: a regulatory clock
that stops when the last web request finishes is not a clock. Deadlines
accrue whether or not anyone is browsing.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arbiter.db.session import session_scope
from arbiter.decision.deadlines import sweep_deadlines

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger("deadline-sweeper")


def one_pass() -> dict:
    with session_scope() as session:
        return sweep_deadlines(session).to_dict()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--loop", type=int, default=0,
        help="seconds between passes; 0 (default) runs a single pass and exits",
    )
    args = parser.parse_args()

    if args.loop <= 0:
        report = one_pass()
        print(report)
        return

    logger.info("regulatory clock started, interval=%ds", args.loop)
    while True:
        try:
            report = one_pass()
            if report["total_actions"]:
                logger.info("swept: %s", report)
        except Exception:
            # A sweep failure must not kill the clock. Deadlines keep
            # accruing; the next pass picks up whatever this one missed,
            # because every branch is idempotent and guarded by the column
            # it sets.
            logger.exception("deadline sweep failed; retrying next interval")
        time.sleep(args.loop)


if __name__ == "__main__":
    main()
