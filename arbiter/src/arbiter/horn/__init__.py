"""
The Referee's pure decision core (C1: rules decide, models never decide).

This package imports nothing outside the Python standard library -- no
SQLAlchemy, no HTTP clients, no LLM SDKs. Enforced by import-linter
(see pyproject.toml) and CLAUDE.md invariant #1. A decision is a proof
tree over booleans, produced by propositional Horn forward chaining.
"""

from .clause import Literal, Rule, RulePack, StratificationError
from .proof import Fact, FactStatus, LiteralWitness, ProofNode
from .chain import Engine, EvaluationResult
from .implicants import PrimeImplicant, enumerate_prime_implicants
from .counterfactual import (
    Counterfactual,
    CounterfactualItem,
    PerCaseSymmetryProbe,
    counterfactuals_for_all_outcomes,
    load_bearing_predicates,
    minimal_delta,
    per_case_symmetry,
)

__all__ = [
    "Literal", "Rule", "RulePack", "StratificationError",
    "Fact", "FactStatus", "LiteralWitness", "ProofNode",
    "Engine", "EvaluationResult",
    "PrimeImplicant", "enumerate_prime_implicants",
    "Counterfactual", "CounterfactualItem", "PerCaseSymmetryProbe",
    "counterfactuals_for_all_outcomes", "load_bearing_predicates",
    "minimal_delta", "per_case_symmetry",
]
