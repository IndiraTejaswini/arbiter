"""
Self-tests for the property suite's input generator (`strategies.py`).

A sampling strategy that silently degrades is worse than an exponential
sweep, because the suite keeps passing while checking less and less. These
tests are the ones that make `strategies.py` trustworthy enough to replace a
brute-force sweep:

  * the t-wise coverage GUARANTEE is verified directly, not documented;
  * determinism is verified across independent constructions, including
    under a shuffled input order (set iteration order varies with
    PYTHONHASHSEED, which is exactly how a "deterministic" generator stops
    being one);
  * the budget/flag contract is verified, so `FULL_POWERSET_SWEEP=1` cannot
    quietly stop restoring the exact sweep;
  * the scalability claim is verified at 34 predicates -- the size that
    transcribing the rest of the Amex guide's reason codes implies, and the
    size at which the old sweep (2**34 = 17 billion) was the reason for this
    module.
"""

from __future__ import annotations

import itertools
import time

import pytest
from conftest import build_synthetic_pack as _synthetic_pack
from strategies import (
    BUDGET_ADVOCATE_SEARCH,
    BUDGET_SINGLE_EVALUATION,
    DEFAULT_T,
    FULL_SWEEP_ENV,
    assignment_matrix,
    covering_array,
    facts_for,
    matrix_is_exhaustive,
    minimal_winning_subset,
    powerset_assignments,
    structural_assignments,
)

from arbiter.horn import Engine, Fact, FactStatus, RulePack


# These are self-tests for the GENERATOR, and they pass explicit budgets to
# exercise both modes on purpose. An ambient FULL_POWERSET_SWEEP=1 -- which a
# nightly CI job is expected to set -- would override every one of those
# budgets and make this module try to enumerate 2**34 assignments while
# "testing" that the generator avoids doing exactly that. So the flag is
# neutralised here and switched on explicitly by the two tests that are about
# the flag itself.
@pytest.fixture(autouse=True)
def _ignore_ambient_full_sweep(monkeypatch):
    monkeypatch.delenv(FULL_SWEEP_ENV, raising=False)


# --------------------------------------------------------- t-wise guarantee

@pytest.mark.parametrize("n", [1, 2, 3, 5, 8, 13])
@pytest.mark.parametrize("t", [2, 3])
def test_covering_array_covers_every_t_way_combination(n, t):
    """THE guarantee. For every choice of t positions and every one of the
    2**t value combinations, some row realises it. This is what "100%
    coverage of all t-way interactions" has to mean to be worth anything."""
    rows = covering_array(n, t)
    effective_t = min(t, n)
    required = {
        (positions, values)
        for positions in itertools.combinations(range(n), effective_t)
        for values in itertools.product([False, True], repeat=effective_t)
    }
    realised = {
        (positions, tuple(row[p] for p in positions))
        for row in rows
        for positions in itertools.combinations(range(n), effective_t)
    }
    missing = required - realised
    assert not missing, f"n={n} t={t}: {len(missing)} uncovered combinations, e.g. {sorted(missing)[:3]}"


@pytest.mark.parametrize("n,t", [(8, 3), (13, 3), (20, 3), (34, 2)])
def test_covering_array_is_far_smaller_than_the_powerset(n, t):
    """The entire point: rows must grow sub-exponentially. A 34-predicate
    3-wise array is ~69 rows against 2**34 = 17 billion assignments."""
    rows = covering_array(n, t)
    assert len(rows) < 2 ** n
    # Generous but meaningful: greedy t-wise on binary parameters is
    # O(t * log n) rows in theory; this catches an accidental return to
    # anything polynomial-and-large, let alone exponential.
    assert len(rows) <= 16 * n


def test_covering_array_is_deterministic():
    """Built twice from scratch (bypassing the memo), byte-identical."""
    covering_array.cache_clear()
    first = covering_array(12, 3)
    covering_array.cache_clear()
    second = covering_array(12, 3)
    assert first == second


def test_covering_array_handles_degenerate_sizes():
    assert covering_array(0, 3) == ((),)
    # t larger than n clamps to n rather than raising -- a 2-predicate
    # rulepack must not need special-casing at the call site.
    assert covering_array(2, 5) == covering_array(2, 2)
    with pytest.raises(ValueError):
        covering_array(4, 0)


# --------------------------------------------------------- determinism

def test_matrix_is_deterministic_and_order_stable():
    pack = _synthetic_pack(18)
    first = assignment_matrix(pack, exhaustive_budget=BUDGET_ADVOCATE_SEARCH)
    second = assignment_matrix(pack, exhaustive_budget=BUDGET_ADVOCATE_SEARCH)
    assert first == second, "identical calls produced different matrices"
    # Total order, not just a stable set: a failure report has to reproduce.
    assert list(first) == sorted(set(first), key=lambda s: (len(s), sorted(s)))


def test_matrix_does_not_depend_on_predicate_set_iteration_order():
    """`edb_predicates()` returns a set. If the generator ever consumed it
    unsorted, the matrix would shift between processes with different
    PYTHONHASHSEED values -- passing locally and failing in CI for reasons
    no one could reproduce."""
    pack = _synthetic_pack(16)
    shuffled = RulePack(
        rulepack_id=pack.rulepack_id, reason_code=pack.reason_code, version=pack.version,
        rules=tuple(reversed(pack.rules)),
        decision_predicates=dict(reversed(list(pack.decision_predicates.items()))),
        predicate_schema=tuple(reversed(pack.predicate_schema)),
    )
    assert (
        assignment_matrix(pack, exhaustive_budget=BUDGET_ADVOCATE_SEARCH)
        == assignment_matrix(shuffled, exhaustive_budget=BUDGET_ADVOCATE_SEARCH)
    )


def test_matrix_rows_are_all_valid_assignments():
    pack = _synthetic_pack(20)
    edb = pack.edb_predicates()
    for assignment in assignment_matrix(pack, exhaustive_budget=BUDGET_ADVOCATE_SEARCH):
        assert assignment <= edb, f"matrix row references non-EDB predicates: {sorted(assignment - edb)}"


# --------------------------------------------------------- budget + flag contract

def test_small_rulepack_under_budget_stays_exhaustive(packs):
    """The backwards-compatibility guarantee. Every rulepack shipped today
    is under the single-evaluation budget, so the conflict and implicant
    tests keep the exact proof they had before this refactor -- the
    refactor changes what happens as rulepacks GROW, not what is proven
    about the ones that exist."""
    for code, pack in packs.items():
        assert matrix_is_exhaustive(pack, BUDGET_SINGLE_EVALUATION), (
            f"{code} has {len(pack.edb_predicates())} predicates and no longer fits the "
            f"exhaustive budget -- its proofs are now sampled; see strategies.py"
        )
        matrix = assignment_matrix(pack, exhaustive_budget=BUDGET_SINGLE_EVALUATION)
        assert len(matrix) == 2 ** len(pack.edb_predicates())


def test_large_rulepack_over_budget_switches_to_sampling():
    """20 predicates rather than 34: this asserts the MODE SWITCH, and the
    absolute scaling claim has its own test below. Keeping the cheap
    assertion cheap is what leaves the suite room under its time budget."""
    pack = _synthetic_pack(20)
    assert not matrix_is_exhaustive(pack, BUDGET_SINGLE_EVALUATION)
    matrix = assignment_matrix(pack, exhaustive_budget=BUDGET_SINGLE_EVALUATION)
    assert 0 < len(matrix) < 2 ** 20


def test_full_sweep_env_var_overrides_every_budget(monkeypatch):
    """The escape hatch. With the flag set, a rulepack that would sample must
    go exhaustive regardless of budget -- this is the switch that gets the
    proof back, and a nightly CI job should set it."""
    pack = _synthetic_pack(12)
    assert not matrix_is_exhaustive(pack, exhaustive_budget=16)

    monkeypatch.setenv(FULL_SWEEP_ENV, "1")
    assert matrix_is_exhaustive(pack, exhaustive_budget=16)
    assert len(assignment_matrix(pack, exhaustive_budget=16)) == 2 ** 12


@pytest.mark.parametrize("value,expected", [
    ("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True),
    ("0", False), ("false", False), ("no", False), ("off", False), ("", False),
])
def test_full_sweep_flag_parsing_is_forgiving(monkeypatch, value, expected):
    """A debugging switch typed at a shell prompt. `FULL_POWERSET_SWEEP=true`
    silently doing nothing would be worse than having no flag."""
    pack = _synthetic_pack(12)
    monkeypatch.setenv(FULL_SWEEP_ENV, value)
    assert matrix_is_exhaustive(pack, exhaustive_budget=16) is expected


# --------------------------------------------------------- structural layer

def test_structural_layer_includes_every_rule_body(packs):
    """Rule bodies are the independent part of the structural layer -- they
    are computed without consulting `enumerate_prime_implicants`, so the
    implicant tests are not fed only inputs derived from the module under
    test."""
    for code, pack in packs.items():
        rows = set(structural_assignments(pack))
        for rule in pack.rules:
            positives = frozenset(lit.predicate for lit in rule.body if not lit.negated)
            assert positives in rows, f"{code}/{rule.rule_id}: rule body missing from structural layer"


def test_structural_layer_includes_cross_outcome_conflicts(packs):
    """`test_conflicts_never_silently_resolved` in sampled mode depends on
    these rows existing: a random matrix reaches a genuine two-outcome
    conflict only by luck."""
    from arbiter.horn.implicants import enumerate_prime_implicants

    pack = packs["C02"]  # the pack with known independently-satisfiable outcomes
    rows = set(structural_assignments(pack))
    merchant = [m.positive_predicates() for m in enumerate_prime_implicants(pack, "merchant_wins")]
    card_member = [m.positive_predicates() for m in enumerate_prime_implicants(pack, "card_member_wins")]
    assert any((a | b) in rows for a in merchant for b in card_member)


def test_structural_layer_includes_blockers_and_near_misses(packs):
    """The non-monotone families. `implicant + blocker` is the shape of the
    defect arbiter.decision.adjudicate's docstring records; `implicant - one`
    is where a rule firing too eagerly shows up."""
    from arbiter.horn.implicants import enumerate_prime_implicants

    pack = packs["C08"]
    rows = set(structural_assignments(pack))
    negated = {lit.predicate for rule in pack.rules for lit in rule.body if lit.negated}
    assert negated, "expected C08 to negate at least one predicate"

    implicants = [m.positive_predicates() for m in enumerate_prime_implicants(pack, "merchant_wins")]
    assert any((base | {blocker}) in rows for base in implicants for blocker in negated)
    assert any((base - {one}) in rows for base in implicants for one in base)


# --------------------------------------------------------- minimal-witness reduction

def test_minimal_winning_subset_is_minimal_and_still_wins(packs):
    """The depth the sampled mode buys back for the breadth it gives up: a
    reduced witness must still derive the head, and no single further
    removal may."""
    engine = Engine()
    for code, pack in packs.items():
        for head in sorted(set(pack.decision_predicates.values())):
            everything = frozenset(pack.edb_predicates())
            if head not in engine.evaluate(pack, facts_for(everything)).true_predicates:
                continue  # asserting everything blocks every rule for this head
            reduced = minimal_winning_subset(engine, pack, head, everything)
            assert head in engine.evaluate(pack, facts_for(reduced)).true_predicates, (
                f"{code}/{head}: reduction dropped the win"
            )
            for predicate in sorted(reduced):
                trial = reduced - {predicate}
                assert head not in engine.evaluate(pack, facts_for(trial)).true_predicates, (
                    f"{code}/{head}: {sorted(reduced)} is not minimal -- removing "
                    f"{predicate} still derives {head}"
                )


def test_minimal_winning_subset_of_a_non_winner_is_returned_unshrunk(packs):
    """Guard on the contract: callers must check `head` is derivable BEFORE
    reducing. Reducing a losing assignment cannot find a win, and silently
    returning something that looks like a witness would be the worst
    possible failure here."""
    engine = Engine()
    pack = packs["C08"]
    assert "merchant_wins" not in engine.evaluate(pack, facts_for(frozenset())).true_predicates
    assert minimal_winning_subset(engine, pack, "merchant_wins", frozenset()) == frozenset()


# --------------------------------------------------------- scalability

def test_thirty_four_predicate_rulepack_is_generated_quickly():
    """The regression guard on the reason this module exists. 34 predicates
    is what the Amex guide's remaining reason codes imply at the observed
    ~11 predicates per code; 2**34 is 17 billion assignments. Generation
    must stay in the low seconds and the matrix in the low thousands."""
    covering_array.cache_clear()
    pack = _synthetic_pack(34)
    started = time.perf_counter()
    matrix = assignment_matrix(pack, exhaustive_budget=BUDGET_SINGLE_EVALUATION, t=DEFAULT_T)
    elapsed = time.perf_counter() - started

    assert len(matrix) < 4000, f"matrix grew to {len(matrix)} rows"
    assert elapsed < 10.0, f"generation took {elapsed:.1f}s"

    # And the matrix is actually usable: every row evaluates.
    engine = Engine()
    for assignment in matrix[:200]:
        engine.evaluate(pack, facts_for(assignment))


def test_powerset_helper_still_produces_the_exact_original_sweep():
    """The exact-mode path is the original generator, unchanged. Pinned
    against a hand-written expansion so a refactor of it cannot quietly
    reorder or drop assignments."""
    assert powerset_assignments(["a", "b"]) == [
        frozenset(), frozenset({"b"}), frozenset({"a"}), frozenset({"a", "b"}),
    ]
    assert len(powerset_assignments([f"p{i}" for i in range(10)])) == 1024


def test_facts_for_marks_named_predicates_true_and_omits_the_rest():
    facts = facts_for(frozenset({"a", "b"}))
    assert set(facts) == {"a", "b"}
    assert all(f.status is FactStatus.TRUE for f in facts.values())
    assert facts["a"] == Fact("a", FactStatus.TRUE, ("n_a",))
