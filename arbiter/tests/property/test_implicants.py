"""
Verification that `arbiter.horn.implicants.enumerate_prime_implicants` is
what A4 ("the counterfactual ledger") claims it is: an EXACT, COMPLETE
characterization of every minimal winning coalition for a rulepack outcome,
checked against brute-force ground truth rather than against the module's own
internal helpers.

This matters beyond internal correctness. The counterfactual ledger is what a
merchant or card member reads to learn exactly which predicate(s) would flip
their case ("what would have changed this"). That disclosure is only safe --
doesn't hand out a fabrication blueprint -- to the extent the counterfactual
is provably the SMALLEST true requirement and nothing is silently missing
from the enumeration (see README's disclosure-safety section: safety there
rests on ADEC/tier-gating making fabrication of a counterfactual-named
predicate structurally unable to reach COMMITTED/NETWORK tier, but that
argument only holds if the named predicate set is actually complete and
minimal in the first place -- which is what this module checks).

For every rulepack and every decision-outcome head:

  W = { S subseteq EDB : asserting exactly S TRUE derives `head` }
      (ground truth, computed directly via Engine.evaluate -- no dependency
      on implicants.py at all)

  M = { mwc.positive_predicates() for mwc in enumerate_prime_implicants(head) }

Soundness:    every m in M is itself a member of W (asserting it really
              does derive the head -- an MWC that doesn't actually work
              would be a false "this is sufficient" claim).
Completeness: every S in W is a superset of some m in M (nothing that
              actually wins is missing from the enumeration -- an omitted
              winning coalition would be a false "this is impossible"
              claim, the more dangerous direction for a counterfactual to
              get wrong). Strengthened here beyond the superset test: each
              winning S is first reduced to a minimal winning subset, and
              THAT set must be an enumerated implicant exactly.
Minimality:   no m in M is a strict superset of another m' in M (M contains
              only genuinely minimal coalitions, not redundant larger ones a
              caller might mistakenly treat as "the" requirement).

## How W is enumerated, and what that costs the proof

`W` used to be computed over the full predicate powerset -- every one of the
2**len(EDB) assignments. `strategies.py` explains at length why that had to
change (2**34 for a fully-transcribed reason-code corpus). The short version
as it applies here:

  * Every rulepack shipped today has 10-13 EDB predicates and runs the FULL
    powerset under `BUDGET_SINGLE_EVALUATION`, so nothing these tests prove
    today becomes weaker. `test_strategies.py::
    test_small_rulepack_under_budget_stays_exhaustive` fails the build if
    that stops being true silently.
  * A rulepack too large for the budget is checked over a covering matrix
    instead: strong evidence, not a proof. `FULL_POWERSET_SWEEP=1` forces the
    proof back for any rulepack, at exponential cost, and is what a nightly
    CI job should set.
  * SOUNDNESS stays exact in both modes regardless of size, because
    `strategies.structural_assignments` includes every enumerated implicant
    by construction -- the sampled matrix cannot omit the very sets
    soundness is about.
"""

from __future__ import annotations

from typing import Dict, FrozenSet, Set, Tuple

import pytest
from conftest import skip_if_full_sweep
from strategies import (
    BUDGET_SINGLE_EVALUATION,
    assignment_matrix,
    facts_for,
    matrix_is_exhaustive,
    minimal_winning_subset,
)

from arbiter.horn import RulePack
from arbiter.horn.implicants import enumerate_prime_implicants


@pytest.fixture(scope="session")
def winning_sets(packs, engine) -> Dict[Tuple[str, str], Set[FrozenSet[str]]]:
    """`W` for every (reason code, decision head), computed once.

    Session-scoped because soundness and completeness both need it and it is
    the expensive part: recomputing it per test doubled the sweep. Ground
    truth is `Engine.evaluate` and nothing else -- in particular not
    `implicants.py`'s own `_is_implicant`, which shares an Engine with the
    code under test and so could not catch a bug the two have in common.
    """
    out: Dict[Tuple[str, str], Set[FrozenSet[str]]] = {}
    for code, pack in packs.items():
        matrix = assignment_matrix(pack, exhaustive_budget=BUDGET_SINGLE_EVALUATION)
        for head in sorted(set(pack.decision_predicates.values())):
            out[(code, head)] = {
                assignment for assignment in matrix
                if head in engine.evaluate(pack, facts_for(assignment)).true_predicates
            }
    return out


def _mode(pack: RulePack) -> str:
    return "exhaustive" if matrix_is_exhaustive(pack, BUDGET_SINGLE_EVALUATION) else "sampled"


def test_prime_implicants_are_sound(packs, winning_sets):
    """Every enumerated MWC really does derive the head when asserted --
    checked by actually running it through the engine, not by trusting
    implicants.py's own internal `_is_implicant` helper (which uses the
    same Engine, so a bug shared between them wouldn't be caught by that
    alone; this test's ground truth is independently recomputed).

    Exact in both sweep modes: the sampled matrix always contains every
    enumerated implicant (`strategies.structural_assignments`), so there is
    no sampling gap on this side of the property.
    """
    for code, pack in packs.items():
        for outcome, head in pack.decision_predicates.items():
            winning = winning_sets[(code, head)]
            for mwc in enumerate_prime_implicants(pack, head):
                assert mwc.positive_predicates() in winning, (
                    f"{code} {outcome}: enumerated MWC {sorted(mwc.positive_predicates())} "
                    f"does not actually derive {head} when asserted -- unsound implicant"
                )


def test_prime_implicants_are_complete(packs, engine, winning_sets):
    """The direction that matters most for disclosure safety: every evidence
    combination that genuinely wins is a superset of some enumerated MWC. If
    this failed, the counterfactual ledger could tell a party "no smaller
    path exists" when one actually does -- either overstating what's required
    (unfair coaching) or, worse, omitting a real minimal path from what gets
    disclosed as safe/unsafe.

    WHY THE REDUCTION RUNS ONLY IN SAMPLED MODE. Over the FULL powerset the
    superset assertion already entails the stronger statement, so reducing
    would be pure cost: let S be any minimal winning set. The powerset
    contains S, so the assertion gives S ⊇ m for some enumerated implicant m,
    and m wins (soundness), so S minimal forces S = m. Every minimal winning
    set therefore already has to BE an enumerated implicant -- there is
    nothing left for a reduction to catch.

    That entailment breaks the moment W is partial: a sampled matrix need not
    contain the minimal S at all, only some superset of it, and a superset
    can satisfy the assertion via a different implicant while S itself is
    missing from the enumeration. So in sampled mode each winning assignment
    is greedily reduced to a minimal winning subset and that set must be an
    enumerated implicant exactly. Sampling gives up breadth; the reduction
    buys back depth on exactly the direction sampling weakened.
    """
    for code, pack in packs.items():
        sampled = _mode(pack) == "sampled"
        for outcome, head in pack.decision_predicates.items():
            mwc_sets = [m.positive_predicates() for m in enumerate_prime_implicants(pack, head)]
            for subset in sorted(winning_sets[(code, head)], key=lambda s: (len(s), sorted(s))):
                assert any(m <= subset for m in mwc_sets), (
                    f"{code} {outcome} [{_mode(pack)}]: winning set {sorted(subset)} is not a "
                    f"superset of any enumerated MWC -- enumerate_prime_implicants is missing a "
                    f"real winning coalition"
                )
                if not sampled:
                    continue
                reduced = minimal_winning_subset(engine, pack, head, subset)
                assert reduced in mwc_sets, (
                    f"{code} {outcome} [sampled]: winning set {sorted(subset)} reduces to "
                    f"the minimal winning subset {sorted(reduced)}, which is NOT one of the "
                    f"enumerated implicants {[sorted(m) for m in mwc_sets]} -- the enumeration "
                    f"is missing a genuinely minimal coalition"
                )


def test_prime_implicants_are_minimal(packs):
    """No enumerated MWC is a strict superset of another -- M contains only
    genuinely minimal coalitions. (This is also asserted inside
    implicants.py's own `enumerate_prime_implicants`; re-checked here against
    the same MWC list the soundness/completeness tests use, so a regression
    that broke minimization would show up in the property test suite, not
    only via internal trust.)

    No sweep at all -- this is a property of M alone, so it is unaffected by
    how W is enumerated and stays exact at every rulepack size.
    """
    for code, pack in packs.items():
        for outcome, head in pack.decision_predicates.items():
            sets = [mwc.positive_predicates() for mwc in enumerate_prime_implicants(pack, head)]
            for i, a in enumerate(sets):
                for j, b in enumerate(sets):
                    if i != j:
                        assert not (b < a), (
                            f"{code} {outcome}: MWC {sorted(a)} is a strict superset of "
                            f"MWC {sorted(b)} -- not minimal"
                        )


@skip_if_full_sweep
def test_completeness_holds_under_sampling_on_a_large_rulepack(engine, synthetic_pack):
    """The sampled path, exercised today rather than whenever a large rulepack
    lands.

    Every shipped rulepack runs exhaustive, so without this test the sampling
    strategy, the covering array and the minimal-witness reduction would all
    be untested code that CI reports as passing -- and the first large
    rulepack would be the thing that discovers whether any of it works. A
    24-predicate pack is 16.7M assignments exhaustively, well past the budget,
    so this takes the sampled branch by construction.

    Note this is the STRONG form: every winning assignment in the matrix is
    reduced to a minimal winning subset, and each must be an enumerated
    implicant exactly.
    """
    pack = synthetic_pack(24)
    assert not matrix_is_exhaustive(pack, BUDGET_SINGLE_EVALUATION), (
        "a 24-predicate rulepack should exceed the exhaustive budget"
    )
    matrix = assignment_matrix(pack, exhaustive_budget=BUDGET_SINGLE_EVALUATION)

    for head in sorted(set(pack.decision_predicates.values())):
        mwc_sets = [m.positive_predicates() for m in enumerate_prime_implicants(pack, head)]
        checked = 0
        for assignment in matrix:
            if head not in engine.evaluate(pack, facts_for(assignment)).true_predicates:
                continue
            checked += 1
            assert any(m <= assignment for m in mwc_sets), (
                f"synthetic/{head}: winning set {sorted(assignment)} is not a superset of any "
                f"enumerated MWC"
            )
            reduced = minimal_winning_subset(engine, pack, head, assignment)
            assert reduced in mwc_sets, (
                f"synthetic/{head}: {sorted(assignment)} reduces to {sorted(reduced)}, which is "
                f"not an enumerated implicant"
            )
        assert checked > 0, f"synthetic/{head}: no winning assignment in the sampled matrix"


def test_every_head_has_winning_assignments_in_the_matrix(packs, winning_sets):
    """A guard on the GENERATOR, not on implicants.py.

    If a future sampling change produced a matrix containing no winning
    assignment for some head, `test_prime_implicants_are_complete` would
    iterate an empty set and pass vacuously -- the failure mode that makes
    sampled property tests untrustworthy. This makes that impossible to miss.
    """
    for code, pack in packs.items():
        for outcome, head in pack.decision_predicates.items():
            assert winning_sets[(code, head)], (
                f"{code} {outcome}: the assignment matrix contains no assignment that derives "
                f"{head}, so the completeness check would pass without checking anything"
            )
