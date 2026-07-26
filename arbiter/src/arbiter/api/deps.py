"""Process-wide singletons the routes depend on: the loaded rulepack
registry and the conformal calibration gate. Both are populated once at
app startup (arbiter.main's lifespan) and are read-only from request
handlers."""

from __future__ import annotations

from arbiter.decision.conformal import ConformalAbstentionGate
from arbiter.provenance import ProvenanceService, TransparencyLog
from arbiter.rulepack import RulepackRegistry

registry = RulepackRegistry()
abstention_gate = ConformalAbstentionGate(alpha=0.05, min_n_for_guarantee=100)
provenance_service = ProvenanceService(TransparencyLog())


def get_registry() -> RulepackRegistry:
    return registry


def get_abstention_gate() -> ConformalAbstentionGate:
    return abstention_gate


def get_provenance_service() -> ProvenanceService:
    return provenance_service
