"""
LLM exception-path narration (used only when proof-tree depth > 4 or an
unusual rule combination fires -- the spec's own ~20% case).

Not wired to a live model in this build: no LLM call happens here. The
function exists so the call site (arbiter.decision) has a single, stable
place to invoke it later without a signature change, and so it's obvious in
a diff when it goes from stub to real. Returning None is a deliberate,
honest signal -- callers must treat it exactly like a grounding failure and
fall back to narrate.template's deterministic renderer (ground.py already
does this for you via render_narration_safe). Per CLAUDE.md: "be right or
silent, never confidently wrong" -- a stub that fabricated prose here would
violate that; a stub that abstains does not.
"""

from __future__ import annotations

from typing import Optional

from arbiter.horn.chain import EvaluationResult
from arbiter.horn.clause import RulePack

from .template import Narration


def should_use_llm_narration(evaluation: EvaluationResult, depth_threshold: int = 4) -> bool:
    if evaluation.decision_head is None or evaluation.decision_head not in evaluation.proof_trees:
        return False

    def depth(node) -> int:
        if not node.literals:
            return 1
        return 1 + max((depth(w.child) if w.child else 0 for w in node.literals), default=0)

    return depth(evaluation.proof_trees[evaluation.decision_head]) > depth_threshold


def render_llm_narration(evaluation: EvaluationResult, rulepack: RulePack) -> Optional[Narration]:
    """Always returns None in this build (no LLM wired for narration). See
    module docstring."""
    return None
