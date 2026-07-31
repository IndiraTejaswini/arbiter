from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from arbiter.api.deps import abstention_gate, privacy_vault, provenance_service, registry
from arbiter.api.routes import ALL_ROUTERS
from arbiter.api.routes.evidence import MAX_ARTIFACTS_PER_CASE
from arbiter.config import get_settings, validate_for_environment

logger = logging.getLogger(__name__)


def _load_calibration() -> None:
    """Load REAL nonconformity scores from `calibration_sample` into the
    Mondrian gate.

    This replaces `_seed_calibration()`, which used to invent 150 Gaussian
    random scores per reason code at every boot so the gate would have a
    threshold to compare against. That produced a confident-looking
    coverage claim with nothing behind it: the resulting threshold
    (q_hat = 0.688 at alpha=0.05) auto-resolved nearly everything,
    including cases carrying a CRITICAL unresolved contradiction. The
    number `evals/calibration.py` reports was real; the number the service
    used was not.

    If the store is empty or unreachable, the gate stays uncalibrated and
    escalates every case (see ConformalAbstentionGate.require_real_
    calibration). That is the correct failure mode: a dispute system with
    no calibration data should route to humans, not guess with a fabricated
    threshold. Populate it with `python scripts/seed_calibration.py`, or
    let analyst reviews accumulate.
    """
    from sqlalchemy import select

    from arbiter.db import models as m
    from arbiter.db.session import session_scope
    from arbiter.decision.review_sampling import calibration_weight

    try:
        with session_scope() as session:
            rows = session.execute(
                select(
                    m.CalibrationSample.reason_code,
                    m.CalibrationSample.score,
                    m.CalibrationSample.selection_probability,
                )
            ).all()
    except Exception as exc:
        logger.warning(
            "could not load calibration_sample (%s) -- the conformal gate is "
            "UNCALIBRATED and will escalate every case until it is populated. "
            "Run scripts/seed_calibration.py once the database is reachable.",
            exc,
        )
        return

    # Inverse-probability weighted: escalation reviews and audit reviews
    # enter the pool at very different rates, so an unweighted pool is a
    # biased subsample of the deployment distribution.
    counts = abstention_gate.load_calibration(
        (code, float(score), calibration_weight(probability))
        for code, score, probability in rows
    )
    if not counts:
        logger.warning(
            "calibration_sample is empty -- the conformal gate will escalate every "
            "case until it is populated (scripts/seed_calibration.py)."
        )
        return

    for code in registry.reason_codes():
        n = counts.get(code, 0)
        if abstention_gate.is_calibrated(code):
            logger.info(
                "conformal gate calibrated for %s: n=%d (effective %.1f), threshold=%.4f",
                code, n, abstention_gate.effective_sample_size(code),
                abstention_gate.threshold_for(code),
            )
            # A threshold at the top of the score range auto-resolves every
            # case that reaches the comparison. That is a broken gate wearing
            # a calibrated one's clothes: `is_calibrated` is true, the log
            # line above looks healthy, and nothing is ever abstained on. It
            # is caused by calibration rows the gate is never asked to rule
            # on -- no-decision cases score exactly 1.0 -- so the diagnostic
            # names that cause directly rather than leaving it to be inferred
            # from an abstention rate nobody is watching.
            if abstention_gate.is_inert(code):
                logger.error(
                    "conformal gate for %s is INERT: threshold %.4f is at the top of the "
                    "nonconformity range, so every case that reaches the comparison "
                    "auto-resolves. %.0f%% of this stratum's calibration mass sits at the "
                    "maximum score, which is the signature of no-decision cases in the pool "
                    "-- those never reach the threshold in production and must not calibrate "
                    "it. Re-run scripts/seed_calibration.py, which now excludes them.",
                    code, abstention_gate.threshold_for(code),
                    abstention_gate.saturated_fraction(code) * 100,
                )
        else:
            logger.warning(
                "conformal gate UNDER-CALIBRATED for %s: n=%d, effective n=%.1f (<%d) -- "
                "every %s case will escalate to human review.",
                code, n, abstention_gate.effective_sample_size(code),
                abstention_gate.min_n_for_guarantee, code,
            )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    # Fail closed at boot rather than serve traffic with a known-insecure
    # configuration (default auth secret, dev-token route enabled, ephemeral
    # signing key). Raising here stops the process; that is the intent.
    validate_for_environment(settings)

    registry.load_dir(settings.rulepack_dir)
    _load_calibration()

    # Rehydrate the durable process state that used to live only in RAM:
    # the ADEC transparency log and the per-subject crypto-shredding keys.
    # Before this, a restart destroyed every commitment ever made and made
    # every encrypted PII field permanently unrecoverable.
    provenance_service.rehydrate()
    privacy_vault.rehydrate()

    if settings.enable_dev_auth:
        logger.warning(
            "ARBITER_ENABLE_DEV_AUTH is on -- POST /v1/auth/dev-token will mint bearer "
            "tokens for ANY role, including ADMIN, with no authentication. Development only."
        )
    yield


app = FastAPI(title="ARBITER", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for router in ALL_ROUTERS:
    app.include_router(router)


@app.get("/health")
def health():
    """Liveness. Deliberately does not touch the database -- a liveness
    probe that fails on a transient DB blip causes the orchestrator to kill
    healthy pods.

    `limits` carries the request constraints a client has to respect. The
    upload cap in particular is CONFIGURABLE (`ARBITER_MAX_ARTIFACT_BYTES`)
    and the console hardcoded 25 MB, so raising or lowering it server-side
    left the UI confidently stating the wrong number and rejecting files the
    API would have accepted. A limit the client must honour is part of the
    contract, not an implementation detail.
    """
    settings = get_settings()
    return {
        "status": "ok",
        "reason_codes": registry.reason_codes(),
        "limits": {
            "max_artifact_bytes": settings.max_artifact_bytes,
            "max_artifacts_per_case": MAX_ARTIFACTS_PER_CASE,
        },
    }


@app.get("/ready")
def ready():
    """Readiness: is this instance actually able to serve adjudications?
    Reports calibration status per reason code, because an uncalibrated
    gate escalates everything -- that is a degraded mode operators need to
    see, not a silent one."""
    codes = registry.reason_codes()
    calibration = {
        code: {
            "n": abstention_gate.calibration_size(code),
            # Effective n after inverse-probability weighting. A pool
            # dominated by a few heavily-weighted audit samples carries
            # less information than its raw count suggests, and the gate
            # is calibrated against THIS number, not the raw one.
            "effective_n": round(abstention_gate.effective_sample_size(code), 1),
            "calibrated": abstention_gate.is_calibrated(code),
        }
        for code in codes
    }
    fully_ready = bool(codes) and all(v["calibrated"] for v in calibration.values())
    return {
        "status": "ready" if fully_ready else "degraded",
        "rulepacks_loaded": len(codes),
        "calibration": calibration,
        # The threshold `calibrated` is measured against. Configurable
        # (`ARBITER_CONFORMAL_MIN_N`), and the console hardcoded "n >= 100" as
        # a statement of fact -- so a deployment that tuned it displayed a
        # number no longer true of itself. Served alongside the counts it
        # explains, since a progress bar without its target is decoration.
        "min_calibration_n": abstention_gate.min_n_for_guarantee,
        "conformal_alpha": get_settings().conformal_alpha,
        "note": None if fully_ready else (
            "one or more reason codes are under-calibrated; those cases escalate to "
            "human review instead of auto-resolving"
        ),
    }
