"""Shared fixtures for the property suite.

Also makes `strategies` importable as a plain module. With no `__init__.py`
in this directory pytest already inserts it into `sys.path` (rootdir-relative
insertion), so `from strategies import ...` works today -- but that is a
side effect of pytest's import mode, not a promise. Doing it explicitly here
means the suite keeps working under `--import-mode=importlib` and when a test
module is run directly.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from strategies import full_sweep_requested  # noqa: E402

from arbiter.horn import Engine, Literal, Rule, RulePack  # noqa: E402
from arbiter.rulepack import load_rulepack_dir  # noqa: E402

RULEPACK_DIR = _HERE.parent.parent / "rulepacks" / "amex"

# Tests that exist specifically to exercise the SAMPLED path cannot run when
# the flag forces exhaustive mode -- and must not merely fail: a 24-predicate
# synthetic rulepack under an exhaustive sweep is 16.7M assignments, so the
# nightly full-sweep job would hang rather than report. Skipped explicitly,
# with the reason visible in the run, instead of guarded by a bare `if` that
# would silently report a pass.
skip_if_full_sweep = pytest.mark.skipif(
    full_sweep_requested(),
    reason="FULL_POWERSET_SWEEP forces exhaustive mode; this test exercises the sampled path",
)


def build_synthetic_pack(n_predicates: int, rules_of: int = 3) -> RulePack:
    """A rulepack with `n_predicates` EDB predicates and overlapping rule
    bodies.

    Exists so the SAMPLED code paths in `strategies.py` are exercised by CI
    today. Every shipped rulepack is small enough to run exhaustive, so
    without this the sampling strategy, the covering array and the
    minimal-witness reduction would all sit untested until the first large
    rulepack landed -- the "fully implemented, never exercised" failure mode
    this repo has already been bitten by twice (tier gating, the
    contradiction layers).

    Includes a negated literal deliberately: it makes the decision function
    non-monotone, which is what the blocker family in
    `strategies.structural_assignments` is for, and a purely monotone
    synthetic pack would make the sampled path look easier than it is.
    """
    predicates = [f"p{i:02d}" for i in range(n_predicates)]
    rules = [
        Rule(
            rule_id=f"R{index}", head="merchant_wins",
            body=tuple(Literal(p) for p in predicates[index:index + rules_of]),
        )
        for index in range(0, n_predicates - rules_of, 2)
    ]
    rules.append(Rule(
        rule_id="R_CM", head="card_member_wins",
        body=(Literal(predicates[-1]), Literal(predicates[0], negated=True)),
    ))
    return RulePack(
        rulepack_id="synthetic-v1", reason_code="SYN", version="1.0.0",
        rules=tuple(rules),
        decision_predicates={"MERCHANT_WINS": "merchant_wins", "CARD_MEMBER_WINS": "card_member_wins"},
        predicate_schema=tuple(predicates),
    )


@pytest.fixture(scope="session")
def synthetic_pack():
    """Factory fixture: `synthetic_pack(24)` -> a 24-predicate RulePack."""
    return build_synthetic_pack


@pytest.fixture(scope="session")
def packs() -> Dict[str, RulePack]:
    """Every shipped rulepack, keyed by reason code.

    Session-scoped rather than module-scoped: three test modules now consume
    it, and `load_rulepack_dir` re-parses YAML and re-derives content hashes
    each time. Rulepacks are frozen dataclasses, so sharing one instance
    across modules cannot leak state between tests.
    """
    return load_rulepack_dir(RULEPACK_DIR)


@pytest.fixture(scope="session")
def engine() -> Engine:
    """`Engine` is documented as stateless (`arbiter.horn.chain.Engine`:
    "Stateless; call evaluate() per case"), so one instance is shared."""
    return Engine()
