"""
The Referee's pure decision core (C1: rules decide, models never decide).

This package imports nothing outside the Python standard library -- no
SQLAlchemy, no HTTP clients, no LLM SDKs. Enforced by import-linter
(see pyproject.toml) and CLAUDE.md invariant #1. A decision is a proof
tree over booleans, produced by propositional Horn forward chaining.
"""

from .chain import Engine, EvaluationResult
from .clause import Literal, Rule, RulePack, StratificationError
from .counterfactual import (
    Counterfactual,
    CounterfactualItem,
    PerCaseSymmetryProbe,
    counterfactuals_for_all_outcomes,
    load_bearing_predicates,
    minimal_delta,
    per_case_symmetry,
)
from .implicants import PrimeImplicant, enumerate_prime_implicants
from .proof import Fact, FactStatus, LiteralWitness, ProofNode

__all__ = [
    "Literal", "Rule", "RulePack", "StratificationError",
    "Fact", "FactStatus", "LiteralWitness", "ProofNode",
    "Engine", "EvaluationResult",
    "PrimeImplicant", "enumerate_prime_implicants",
    "Counterfactual", "CounterfactualItem", "PerCaseSymmetryProbe",
    "counterfactuals_for_all_outcomes", "load_bearing_predicates",
    "minimal_delta", "per_case_symmetry",
]
