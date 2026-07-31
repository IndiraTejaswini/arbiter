"""Structural validation of a loaded rulepack, beyond what the YAML parser
checks -- this is what the Phase-2 property tests (tests/property/) exercise
at scale; this module is the single-rulepack, load-time version of the same
checks, meant to fail fast and loud when a rulepack is broken rather than
letting a bad rulepack reach the Referee.
"""

from __future__ import annotations

from typing import List

from arbiter.core.errors import RulepackError
from arbiter.eligibility.models import ATTRIBUTE_VOCABULARY, AttrType, ChargebackRight
from arbiter.horn.chain import Engine
from arbiter.horn.clause import RulePack, StratificationError
from arbiter.horn.implicants import enumerate_prime_implicants

# Which attribute types each condition operator can meaningfully compare.
# Checked at load time so a rulepack that writes `amount_minor is: true`
# fails the API's boot rather than evaluating to something arbitrary on a
# real case.
_OPERATOR_TYPES = {
    "is": {AttrType.BOOL},
    "equals": {AttrType.STRING, AttrType.INT},
    "one_of": {AttrType.STRING, AttrType.INT, AttrType.STRING_LIST},
    "lte": {AttrType.INT, AttrType.DATETIME},
    "gte": {AttrType.INT, AttrType.DATETIME},
}


def _validate_chargeback_right(reason_code: str, right: ChargebackRight) -> List[str]:
    """Structural validation of the pre-referee gate.

    Everything here is fatal rather than a warning, because the failure mode
    it prevents is silent: an exclusion referencing a misspelled attribute
    never fires, and an exclusion that never fires is indistinguishable in
    production from one that was never written. The whole point of a closed
    attribute vocabulary is that the typo is catchable, so it is caught.
    """
    warnings: List[str] = []

    seen_exclusions = set()
    for exclusion in right.exclusions:
        if exclusion.exclusion_id in seen_exclusions:
            raise RulepackError(f"{reason_code}: duplicate exclusion id {exclusion.exclusion_id!r}")
        seen_exclusions.add(exclusion.exclusion_id)
        if not exclusion.legal_basis:
            warnings.append(
                f"{reason_code}/{exclusion.exclusion_id}: no legal_basis -- an exclusion that "
                f"ends a dispute should cite the rule it comes from"
            )
        for condition in exclusion.conditions:
            spec = ATTRIBUTE_VOCABULARY.get(condition.attribute)
            if spec is None:
                raise RulepackError(
                    f"{reason_code}/{exclusion.exclusion_id}: condition references "
                    f"{condition.attribute!r}, which is not in the eligibility attribute "
                    f"vocabulary (arbiter.eligibility.models.ATTRIBUTE_VOCABULARY)"
                )
            allowed = _OPERATOR_TYPES[condition.operator]
            if spec.type not in allowed:
                raise RulepackError(
                    f"{reason_code}/{exclusion.exclusion_id}: operator {condition.operator!r} "
                    f"cannot apply to {condition.attribute!r} of type {spec.type.value}"
                )

    seen_branches = set()
    for branch in right.filing_window:
        if branch.branch_id in seen_branches:
            raise RulepackError(f"{reason_code}: duplicate filing_window branch id {branch.branch_id!r}")
        seen_branches.add(branch.branch_id)
        if branch.days <= 0:
            raise RulepackError(f"{reason_code}/{branch.branch_id}: filing window days must be positive")
        anchors = list(branch.from_attributes)
        if branch.cap_from_attribute:
            anchors.append(branch.cap_from_attribute)
        for name in anchors:
            spec = ATTRIBUTE_VOCABULARY.get(name)
            if spec is None:
                raise RulepackError(
                    f"{reason_code}/{branch.branch_id}: anchor {name!r} is not in the eligibility "
                    f"attribute vocabulary"
                )
            if spec.type is not AttrType.DATETIME:
                raise RulepackError(
                    f"{reason_code}/{branch.branch_id}: anchor {name!r} is {spec.type.value}, "
                    f"not a date -- a filing window can only be measured from a date"
                )
        if branch.absolute_cap_days is not None and not branch.cap_from_attribute:
            raise RulepackError(
                f"{reason_code}/{branch.branch_id}: absolute_cap_days is set with no cap_from "
                f"anchor, so the cap could never be applied"
            )

    if not right.filing_window:
        warnings.append(
            f"{reason_code}: chargeback_right declares no filing_window -- no dispute under this "
            f"reason code will ever be found out of time"
        )
    return warnings


def validate_rulepack(rulepack: RulePack) -> List[str]:
    """Returns a list of warnings (non-fatal). Raises RulepackError on any
    fatal defect: unreachable outcome (PT-4), cyclic head dependency (PT-5),
    a body atom that's never declared in predicate_schema (part of PT-6), or
    a chargeback_right block referencing an attribute outside the closed
    eligibility vocabulary."""
    warnings: List[str] = []

    if rulepack.chargeback_right is not None:
        warnings.extend(_validate_chargeback_right(rulepack.reason_code, rulepack.chargeback_right))

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
