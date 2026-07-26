from __future__ import annotations

import random
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from arbiter.api.deps import abstention_gate, registry
from arbiter.api.routes import ALL_ROUTERS
from arbiter.config import get_settings


def _seed_calibration() -> None:
    """Seed each reason code's Mondrian calibration pool so the abstention
    gate isn't perpetually under-calibrated on a fresh boot. Real
    calibration data (calibration_sample rows, sourced from analyst review
    or held-out synthetic worlds) should supersede this over time -- see
    evals/calibration.py."""
    rng = random.Random(20260101)
    for code in registry.reason_codes():
        for _ in range(150):
            score = max(0.0, min(1.0, rng.gauss(0.2, 0.1) if rng.random() < 0.75 else rng.gauss(0.55, 0.15)))
            abstention_gate.add_calibration_example(code, score)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    registry.load_dir(settings.rulepack_dir)
    _seed_calibration()
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
    return {"status": "ok", "reason_codes": registry.reason_codes()}
