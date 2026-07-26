"""Structural validation of a loaded rulepack, beyond what the YAML parser
checks -- this is what the Phase-2 property tests (tests/property/) exercise
at scale; this module is the single-rulepack, load-time version of the same
checks, meant to fail fast and loud when a rulepack is broken rather than
letting a bad rulepack reach the Referee.
"""

from __future__ import annotations

from typing import List

from arbiter.core.errors import RulepackError
from arbiter.horn.chain import Engine
from arbiter.horn.clause import RulePack, StratificationError
from arbiter.horn.implicants import enumerate_prime_implicants


def validate_rulepack(rulepack: RulePack) -> List[str]:
    """Returns a list of warnings (non-fatal). Raises RulepackError on any
    fatal defect: unreachable outcome (PT-4), cyclic head dependency (PT-5),
    or a body atom that's never declared in predicate_schema (part of PT-6)."""
    warnings: List[str] = []

    # PT-5: acyclicity / valid stratification -- raises StratificationError,
    # which we re-wrap so every rulepack.* module raises the same family.
    try:
        Engine()  # importing here keeps validate.py's only Horn dependency explicit
        from arbiter.horn.chain import _stratify  # type: ignore[attr-defined]

        _stratify(rulepack)
    except StratificationError as e:
        raise RulepackError(f"{rulepack.reason_code}: {e}") from e

    # PT-6: completeness -- every declared predicate is referenced; every
    # body atom is declared (when a schema is given).
    schema = set(rulepack.predicate_schema)
    if schema:
        referenced = rulepack.edb_predicates()
        undeclared = referenced - schema
        if undeclared:
            raise RulepackError(
                f"{rulepack.reason_code}: body literals reference predicates not in "
                f"predicate_schema: {sorted(undeclared)}"
            )
        unreferenced = schema - referenced
        if unreferenced:
            warnings.append(
                f"{rulepack.reason_code}: predicate_schema declares predicates never "
                f"referenced by any rule body: {sorted(unreferenced)}"
            )

    # PT-4: reachability -- every declared outcome must have at least one
    # satisfiable prime-implicant path.
    for outcome, head in rulepack.decision_predicates.items():
        mwcs = enumerate_prime_implicants(rulepack, head)
        if not mwcs:
            raise RulepackError(
                f"{rulepack.reason_code}: outcome {outcome} ({head}) has no reachable decision path"
            )

    return warnings
