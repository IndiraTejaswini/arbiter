from . import audit, commitments, decisions, disputes, evidence, fairness, rulepacks, stream

ALL_ROUTERS = [
    disputes.router,
    evidence.router,
    decisions.router,
    commitments.router,
    audit.router,
    fairness.router,
    stream.router,
    rulepacks.router,
]
