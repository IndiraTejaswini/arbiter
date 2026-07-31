"""
Predicate-assignment generation for the property suite.

## The problem this replaces

Every sweep in `tests/property/` used to be `itertools.product([False, True],
repeat=len(edb))` -- the full powerset of a rulepack's EDB predicates. That is
exact, and at today's sizes it is cheap: the three shipped rulepacks have 10,
12 and 13 EDB predicates, so 2**13 = 8192 assignments is the worst case.

It does not survive the next rulepack. Amex's published merchant guide lists
22 chargeback reason codes; three are modelled. At the observed ~11
predicates per code, transcribing the rest puts individual rulepacks well past
20 predicates, and 2**34 is 17 billion evaluations -- not slow, *impossible*.
A test suite that must be deleted before the system can grow is not
protecting the system.

## What this module does instead

`assignment_matrix(pack, exhaustive_budget=...)` returns a deterministic,
deduplicated tuple of assignments (each a frozenset of predicates asserted
TRUE). It has two modes and picks between them by cost, not by preference:

  EXHAUSTIVE   when 2**len(edb) <= exhaustive_budget. Every assignment,
               exactly as before. The caller's budget expresses what one
               assignment costs it -- `test_conflicts_never_silently_
               resolved` pays one `Engine.evaluate` and can afford 65536;
               the advocate test pays a full dual-advocate search plus two
               evaluations and cannot.

  SAMPLED      otherwise. Three layers, unioned:

               1. STRUCTURAL. Assignments derived from the rulepack's own
                  shape -- every rule body, every prime implicant, every
                  implicant unioned with each negated literal that could
                  block it, every cross-outcome implicant pair, every
                  implicant with one literal removed. This is the layer that
                  actually catches things: a decision function's interesting
                  boundary is at its rule bodies and one step either side of
                  them, not at a uniformly random point in {0,1}^N.
               2. t-WISE COVERING ARRAY. A minimal deterministic matrix
                  guaranteeing that every t-way combination of predicate
                  truth values appears in at least one row (default t=3, so
                  all 8*C(N,3) triples). This is the layer that catches what
                  nobody thought to target.
               3. SEEDED RANDOM. A fixed-seed sample at several densities,
                  for diversity the first two layers are blind to by
                  construction.

## What is and is not proven

Read this before trusting a green run.

In EXHAUSTIVE mode the property is *proven* over the whole input space. In
SAMPLED mode it is checked against a covering matrix -- strong evidence, not
a proof. The distinction matters most for
`test_prime_implicants_are_complete`, because `tests/property/
test_exhaustive_implicants.py`'s own docstring rests the disclosure-safety
argument on completeness being exact. So:

  * Every rulepack shipped today runs EXHAUSTIVE for both implicant tests
    and for the conflict test. Nothing that is proven today becomes sampled
    by this refactor.
  * `FULL_POWERSET_SWEEP=1` forces EXHAUSTIVE everywhere, ignoring budgets.
    That is the switch to reach for when a rulepack grows past a budget and
    you need the proof back for a specific rulepack, and it is what a
    nightly CI job should set.
  * The sampled implicant test additionally applies a *greedy minimal-witness
    reduction* to every winning assignment it finds (see
    `minimal_winning_subset`), which is a strictly stronger assertion per
    assignment than the exhaustive test's "is a superset of some enumerated
    MWC". Sampling loses breadth; the reduction buys back depth.

## Determinism

No unseeded randomness anywhere. The covering array is a deterministic
greedy construction over predicate *indices*, memoized by `(n, t)` and mapped
onto the sorted predicate list; the random layer uses a fixed seed. Output is
sorted before returning, so row order does not depend on set iteration order
(which varies with PYTHONHASHSEED). Two runs on one machine, and runs across
machines, produce byte-identical matrices -- the same requirement PT-3
already places on the proof trees themselves.
"""

from __future__ import annotations

import itertools
import os
import random
from functools import lru_cache
from typing import Dict, FrozenSet, Iterable, List, Sequence, Tuple

from arbiter.horn import Fact, FactStatus, RulePack
from arbiter.horn.implicants import enumerate_prime_implicants

Assignment = FrozenSet[str]

FULL_SWEEP_ENV = "FULL_POWERSET_SWEEP"

# t=3 rather than t=2. Pairwise is the usual default, but the failure this
# suite exists to catch is shaped like a three-literal rule body: C08_R1 is
# `delivery_confirmed AND address_matches_avs AND NOT signature_missing`, and
# a 2-wise matrix need never place all three at their firing values in one
# row. 3-wise costs a few dozen extra rows on a 34-predicate rulepack -- the
# covering array grows with log(N), not N -- which is nothing next to being
# blind to the most common rule shape in the corpus.
DEFAULT_T = 3

# Assignment counts that keep an exhaustive sweep affordable, expressed as a
# budget per caller because per-assignment cost differs by two orders of
# magnitude across these tests. Named rather than inlined so the numbers are
# reviewable in one place.
#
# CHEAP: one Engine.evaluate (~30us). 65536 covers every rulepack up to 16
# predicates, which is all three shipped ones with headroom.
BUDGET_SINGLE_EVALUATION = 65_536
# EXPENSIVE: a full run_dual_advocacy plus two evaluations (~1.2ms). 512
# means today's rulepacks already run sampled here -- deliberately: this
# test's 8192-assignment sweep was 10 of the suite's 13 seconds, and its
# property is a regression guard whose failure mode is structural (see the
# test's own docstring), which is exactly what the STRUCTURAL layer targets.
BUDGET_ADVOCATE_SEARCH = 512

_RANDOM_SEED = 20260728
_RANDOM_ROWS = 48


def full_sweep_requested() -> bool:
    """True when FULL_POWERSET_SWEEP is set to anything truthy.

    Deliberately permissive about the value ("1", "true", "yes"): this is a
    debugging switch typed by hand at a shell prompt, and a flag that
    silently does nothing because someone wrote `true` instead of `1` is
    worse than no flag.
    """
    raw = os.environ.get(FULL_SWEEP_ENV, "").strip().lower()
    return raw not in ("", "0", "false", "no", "off")


def powerset_assignments(edb: Sequence[str]) -> List[Assignment]:
    """The original generator, kept verbatim as the exact-mode path."""
    return [
        frozenset(p for p, is_true in zip(edb, bits, strict=False) if is_true)
        for bits in itertools.product([False, True], repeat=len(edb))
    ]


# --------------------------------------------------------------- covering array

@lru_cache(maxsize=None)
def covering_array(n: int, t: int) -> Tuple[Tuple[bool, ...], ...]:
    """A deterministic t-wise covering array over `n` binary parameters.

    Guarantees: for every choice of t distinct parameter positions and every
    one of the 2**t value combinations, at least one returned row realises it.
    `test_strategies.py` asserts that guarantee directly rather than trusting
    this docstring.

    Construction is greedy one-row-at-a-time (the classic AETG shape): seed
    each row with a still-uncovered combination, then fill the remaining
    positions with whichever value covers more currently-uncovered
    combinations, breaking ties toward False. Not provably the minimum-size
    array -- computing that is NP-hard and irrelevant here, since greedy
    lands within a few rows of the known bounds and the absolute numbers are
    tiny (a 34-parameter 3-wise array is on the order of 40 rows).

    Memoized on `(n, t)` because it depends on nothing else: the array is
    built over positions, then mapped onto a sorted predicate list by the
    caller. Two rulepacks with the same predicate count share the work.
    """
    if t < 1:
        raise ValueError("t must be >= 1")
    if n == 0:
        return ((),)
    t = min(t, n)

    # Every t-way combination that must appear: (positions, values).
    uncovered = {
        (positions, values)
        for positions in itertools.combinations(range(n), t)
        for values in itertools.product([False, True], repeat=t)
    }

    rows: List[Tuple[bool, ...]] = []
    while uncovered:
        # Deterministic seed choice: the lexicographically first uncovered
        # combination. Picking "the one covering most" would be marginally
        # tighter and would make row order depend on set iteration order.
        seed_positions, seed_values = min(uncovered)
        row: Dict[int, bool] = dict(zip(seed_positions, seed_values, strict=True))

        for position in range(n):
            if position in row:
                continue
            fixed = sorted(row)
            gain_false = _gain(position, False, row, fixed, uncovered, t)
            gain_true = _gain(position, True, row, fixed, uncovered, t)
            row[position] = gain_true > gain_false  # ties -> False

        full_row = tuple(row[position] for position in range(n))
        rows.append(full_row)
        uncovered -= _combinations_in_row(full_row, t)

    return tuple(rows)


def _gain(
    position: int, value: bool, partial: Dict[int, bool], fixed: List[int], uncovered: set, t: int
) -> int:
    """How many uncovered combinations setting `position` to `value` would cover.

    Only combinations that INCLUDE `position` and whose other t-1 positions
    are all already fixed can be affected -- combinations among the fixed
    positions alone do not depend on `value`, and ones touching a
    still-unfixed position are not yet determined. Counting just those is
    what makes the greedy fill affordable: it costs C(|fixed|, t-1) lookups
    instead of C(|fixed|, t), which at n=34, t=3 is the difference between a
    ~6-second build and a sub-second one.
    """
    if len(fixed) < t - 1:
        return 0
    total = 0
    for others in itertools.combinations(fixed, t - 1):
        positions = tuple(sorted(others + (position,)))
        values = tuple(value if q == position else partial[q] for q in positions)
        if (positions, values) in uncovered:
            total += 1
    return total


def _combinations_in_row(row: Tuple[bool, ...], t: int) -> set:
    return {
        (positions, tuple(row[p] for p in positions))
        for positions in itertools.combinations(range(len(row)), t)
    }


# --------------------------------------------------------------- structural layer

def structural_assignments(pack: RulePack) -> List[Assignment]:
    """Assignments derived from the rulepack's own structure.

    Each family here corresponds to a way a property in this suite has
    actually broken, or could:

    * `rule_bodies` -- the positive literals of every rule. A rule that
      cannot fire on its own body is broken, and every derivation path in
      the DNF `arbiter.horn.implicants` expands is rooted in one of these.
      This family is computed WITHOUT consulting `enumerate_prime_implicants`,
      which is what keeps the implicant tests from being fed only inputs
      derived from the module they are testing.
    * `implicants` -- every prime implicant of every head, decision or not.
    * `implicant + blocker` -- an implicant unioned with a single predicate
      that appears negated somewhere in the rulepack. THE important family:
      the decision function is non-monotone in exactly these predicates, so
      this is where "adding evidence changed the answer" lives. It is the
      shape of the defect `arbiter.decision.adjudicate`'s docstring records
      (facts={service_never_rendered: TRUE, refund_issued: TRUE}, where a
      rule should have been blocked and was not).
    * `cross-outcome pairs` -- one implicant of outcome A unioned with one of
      outcome B, the minimal genuine conflict. `test_conflicts_never_
      silently_resolved` is about precisely these, and a random matrix
      reaches them only by luck.
    * `implicant - one literal` -- near misses, which is where a rule firing
      too eagerly shows up.
    * the empty and full assignments -- the trivial-implicant and
      everything-at-once boundaries.
    """
    edb = sorted(pack.edb_predicates())
    out: List[Assignment] = [frozenset(), frozenset(edb)]

    for predicate in edb:
        out.append(frozenset({predicate}))

    for rule in pack.rules:
        out.append(frozenset(lit.predicate for lit in rule.body if not lit.negated))

    negated_anywhere = sorted(
        {lit.predicate for rule in pack.rules for lit in rule.body if lit.negated}
    )

    implicants_by_head: Dict[str, List[Assignment]] = {}
    for head in sorted(pack.heads()):
        sets = [mwc.positive_predicates() for mwc in enumerate_prime_implicants(pack, head)]
        implicants_by_head[head] = sets
        out.extend(sets)
        for base in sets:
            for blocker in negated_anywhere:
                out.append(base | {blocker})
            for literal in sorted(base):
                out.append(base - {literal})

    decision_heads = sorted(set(pack.decision_predicates.values()))
    for head_a, head_b in itertools.combinations(decision_heads, 2):
        for a in implicants_by_head.get(head_a, []):
            for b in implicants_by_head.get(head_b, []):
                out.append(a | b)

    return out


def _random_assignments(edb: Sequence[str], rows: int) -> List[Assignment]:
    """Fixed-seed sample across several densities.

    Densities rather than uniform coin flips: a uniform sample over 34
    predicates concentrates hard around 17 true, and both tails -- sparse
    assignments near a minimal implicant, dense ones near "everything
    asserted" -- are where the structural families live. Sampling only the
    middle would add rows that duplicate what the covering array already
    reaches.
    """
    rng = random.Random(_RANDOM_SEED)
    out: List[Assignment] = []
    densities = (0.1, 0.25, 0.5, 0.75, 0.9)
    for index in range(rows):
        density = densities[index % len(densities)]
        out.append(frozenset(p for p in edb if rng.random() < density))
    return out


# --------------------------------------------------------------- entry point

def matrix_is_exhaustive(pack: RulePack, exhaustive_budget: int) -> bool:
    if full_sweep_requested():
        return True
    return 2 ** len(pack.edb_predicates()) <= exhaustive_budget


def assignment_matrix(
    pack: RulePack,
    *,
    exhaustive_budget: int,
    t: int = DEFAULT_T,
    random_rows: int = _RANDOM_ROWS,
) -> Tuple[Assignment, ...]:
    """Deterministic assignments to check a property over, for one rulepack.

    `exhaustive_budget` is the number of assignments the CALLER can afford,
    not a global preference -- see this module's docstring. Pass one of the
    `BUDGET_*` constants.
    """
    edb = sorted(pack.edb_predicates())
    if matrix_is_exhaustive(pack, exhaustive_budget):
        return tuple(_dedupe(powerset_assignments(edb)))

    rows: List[Assignment] = list(structural_assignments(pack))
    rows.extend(
        frozenset(p for p, is_true in zip(edb, row, strict=True) if is_true)
        for row in covering_array(len(edb), t)
    )
    rows.extend(_random_assignments(edb, random_rows))
    return tuple(_dedupe(rows))


def _dedupe(rows: Iterable[Assignment]) -> List[Assignment]:
    """Deduplicate and impose a total order.

    Sorted by (size, sorted members) rather than left in first-seen order:
    set iteration order varies with PYTHONHASHSEED, and a matrix whose row
    order shifts between runs makes a failure report irreproducible even
    though the row SET is stable.
    """
    return sorted(set(rows), key=lambda s: (len(s), sorted(s)))


def facts_for(assignment: Assignment) -> Dict[str, Fact]:
    """The fact dict an assignment denotes: named predicates TRUE, the rest
    absent (UNKNOWN, which under closed-world satisfies a negated literal).
    Every sweep in this suite built this inline and identically."""
    return {p: Fact(p, FactStatus.TRUE, (f"n_{p}",)) for p in sorted(assignment)}


def minimal_winning_subset(engine, pack: RulePack, head: str, assignment: Assignment) -> Assignment:
    """Greedily shrink a winning assignment to a minimal winning subset.

    Removes predicates one at a time, in sorted order, keeping each removal
    that leaves `head` derivable. The result is minimal by construction: no
    single further removal preserves the win.

    Why this exists. The exhaustive completeness test asserts "every winning
    assignment is a superset of some enumerated MWC", which is cheap to
    satisfy -- a large winning assignment usually contains some implicant
    incidentally. Reducing it first and asserting the REDUCED set is itself
    one of the enumerated implicants is strictly stronger, and it is what
    keeps the sampled mode honest: 200 reduced witnesses probe the
    enumeration harder than 8192 unreduced ones.

    Note this is a *minimal* subset, not the *minimum* one, and it depends on
    removal order -- which is fixed (sorted) precisely so a failure
    reproduces. Under a non-monotone function different orders can reach
    different minimal sets, and every one of them must be an enumerated
    implicant, so any order is a valid probe.
    """
    current = set(assignment)
    for predicate in sorted(assignment):
        trial = current - {predicate}
        if head in engine.evaluate(pack, facts_for(frozenset(trial))).true_predicates:
            current = trial
    return frozenset(current)
