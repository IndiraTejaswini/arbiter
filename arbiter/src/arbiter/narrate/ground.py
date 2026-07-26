"""Citation grounding: the gate an LLM-generated narration must clear before
it's ever shown (C5-adjacent: even generated prose is verified against real
evidence node ids before being trusted, exactly like an advocate assertion).
Every sentence must map to a real evidence node; any ungrounded sentence
discards the whole narration and falls back to the template."""

from __future__ import annotations

from typing import List, Optional, Set, Tuple

from .template import Citation, Narration, render_decision_narration

_FALLBACK_TEXT = (
    "A decision was reached; the detailed narration failed grounding verification and was "
    "withheld. See the proof tree for the machine-checked derivation."
)


def verify_citations(narration: Narration, valid_node_ids: Set[str]) -> Tuple[bool, List[Citation]]:
    """Every narration sentence's citations must resolve to a real evidence
    node. Returns (all_valid, ungrounded_citations)."""
    ungrounded = [c for c in narration.citations if c.node_id not in valid_node_ids]
    return (len(ungrounded) == 0, ungrounded)


def render_narration_safe(
    evaluation,
    rulepack,
    valid_node_ids: Set[str],
    counterfactuals: Optional[dict] = None,
) -> Narration:
    """The actual call site: render, verify, and fall back to a minimal,
    trivially-grounded narration if verification ever fails. The template
    renderer only ever cites node_ids it read directly out of the proof
    tree, so failure here should never trigger for the template path -- it
    exists because narrate.llm's exception-path narration needs exactly
    this fallback, and the fallback logic itself is worth exercising, not
    just asserting."""
    narration = render_decision_narration(evaluation, rulepack, counterfactuals)
    ok, ungrounded = verify_citations(narration, valid_node_ids)
    if ok:
        return narration
    return Narration(text=_FALLBACK_TEXT, sentences=(_FALLBACK_TEXT,), citations=(), source="template_fallback")
