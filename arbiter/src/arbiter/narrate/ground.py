"""Citation grounding: the gate an LLM-generated narration must clear before
it's ever shown (C5-adjacent: even generated prose is verified against real
evidence node ids before being trusted, exactly like an advocate assertion).
Every sentence must map to a real evidence node; any ungrounded sentence
discards the whole narration and falls back to the template."""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import List, Optional, Set, Tuple

from .llm import render_llm_narration, should_use_llm_narration
from .template import Citation, Narration, render_decision_narration

logger = logging.getLogger(__name__)

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
    """The actual call site: try the LLM exception path when the proof is
    deep enough to warrant it, verify whatever comes back, and fall back to
    the deterministic template on any failure.

    The order matters and is the whole point. `render_llm_narration` may
    return prose citing a node that does not exist; `verify_citations` is
    what stops that reaching a reader, and it discards the WHOLE narration
    rather than the offending sentence. A generated narration that was
    produced and then vetoed is reported as `template_fallback`, not
    `template`: the reader of an explanation is entitled to know that the
    grounding check fired, and collapsing the two would make a vetoed case
    indistinguishable from one where no model ever ran.
    """
    llm_vetoed = False
    if should_use_llm_narration(evaluation):
        generated = render_llm_narration(evaluation, rulepack)
        if generated is not None:
            ok, ungrounded = verify_citations(generated, valid_node_ids)
            if ok:
                return generated
            llm_vetoed = True
            logger.warning(
                "LLM narration discarded: %d ungrounded citation(s) %s -- falling back to "
                "the deterministic template",
                len(ungrounded), [c.node_id for c in ungrounded[:5]],
            )

    narration = render_decision_narration(evaluation, rulepack, counterfactuals)
    ok, _ungrounded = verify_citations(narration, valid_node_ids)
    if not ok:
        # The template renderer only ever cites node_ids it read directly
        # out of the proof tree, so this should never trigger -- it is the
        # backstop for the backstop.
        return Narration(text=_FALLBACK_TEXT, sentences=(_FALLBACK_TEXT,), citations=(),
                         source="template_fallback")
    return replace(narration, source="template_fallback") if llm_vetoed else narration
