"""
LLM exception-path narration (used only when proof-tree depth > 4 -- the
spec's own ~20% case; a shallow proof is already read perfectly well by
`narrate.template`).

This is the fourth LLM boundary in CLAUDE.md's table, and it obeys the same
pattern as the other three: the model proposes, a deterministic verifier
disposes. Here the verifier is `narrate.ground.verify_citations`, and its
veto is absolute -- a SINGLE sentence citing a node id that does not exist
discards the ENTIRE narration and the deterministic template renders
instead. That is deliberately harsher than dropping the offending sentence:
a narration that cites a fabricated node is not "mostly right", it is a
document whose provenance claims cannot be trusted, and this text is shown
to a card member and a merchant as the reason a dispute went the way it did.

Three things this boundary must never do, all enforced structurally rather
than by prompt wording:

  - **It cannot change the verdict.** The outcome is computed by
    `arbiter.horn` before this function is called and is passed in as a
    fact to describe. Nothing in the return type can carry an outcome:
    `Narration` is text, a sentence split, and citations. A model that
    argued for the opposite result would produce prose that fails grounding
    or simply describes the wrong thing -- it could not move the decision,
    because no caller reads a verdict from here.
  - **It cannot invent evidence.** The prompt lists the exact node ids that
    exist, and every citation is checked against the case's real node set
    by the caller. Citations are NOT filtered here before returning:
    pre-filtering a hallucinated id would hide it from the verifier and
    quietly defeat the veto this boundary exists to demonstrate.
  - **It cannot fail loudly.** Every failure path -- Ollama down, model not
    pulled, timeout, malformed JSON, empty completion -- returns None, and
    the caller treats None exactly like a grounding failure. See CLAUDE.md
    invariant #11.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set

from arbiter.horn.chain import EvaluationResult
from arbiter.horn.clause import RulePack
from arbiter.llm.client import complete_json

from .template import Citation, Narration

logger = logging.getLogger(__name__)

# A narration is an explanation, not an essay. The cap bounds both the
# tokens spent and the surface a reader has to check.
_MAX_SENTENCES = 8
_MAX_SENTENCE_CHARS = 400

_RESPONSE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "sentences": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "cites": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "cites"],
            },
        },
    },
    "required": ["sentences"],
}


def _depth(node) -> int:
    if not node.literals:
        return 1
    return 1 + max((_depth(w.child) if w.child else 0 for w in node.literals), default=0)


def _literal_count(node) -> int:
    return len(node.literals) + sum(_literal_count(w.child) for w in node.literals if w.child)


def should_use_llm_narration(
    evaluation: EvaluationResult,
    depth_threshold: int = 4,
    breadth_threshold: int = 3,
) -> bool:
    """Whether this proof is hard enough to read that a generated
    explanation earns its cost.

    Depth alone was the original gate, and on the rulepacks this build
    actually ships it selects NOTHING: all three are flat -- every rule
    body is made of base predicates, so no `LiteralWitness.child` is ever
    set and EVERY proof tree is depth 1. Measured over 490 real decisions
    in a seeded database: 490 at depth 1, zero above. A gate that cannot
    fire is the same defect as a layer that receives no input; it just
    hides in a different place.

    So breadth is the real signal here, and it is a fair one -- a flat
    proof resting on four separate conditions is exactly the case where
    the template's mechanical rule-by-rule rendering reads worst. The same
    490 decisions carry 1 literal (42), 2 (260), 3 (181) and 4 (7), so
    `breadth_threshold=3` selects roughly 38% of decided cases. That is
    higher than the spec's "~20% exception path" and deliberately so: the
    cost of being wrong is one wasted local model call and an instant
    fallback, while the cost of the gate never opening is the whole
    boundary sitting dead again.

    Depth is retained, not vestigial: a rulepack with genuine IDB chaining
    would produce nested proofs, and that is precisely when a reader most
    needs prose.
    """
    if evaluation.decision_head is None or evaluation.decision_head not in evaluation.proof_trees:
        return False

    tree = evaluation.proof_trees[evaluation.decision_head]
    return _depth(tree) > depth_threshold or _literal_count(tree) >= breadth_threshold


def _walk(node, lines: List[str], node_ids: Set[str], indent: int = 0) -> None:
    """Flatten the proof tree into prompt lines, collecting citable ids.

    The legal basis is rendered on its own labelled line rather than inline.
    A first version appended it in brackets after the rule description, and
    the model read the bracketed text as an identifier -- it returned
    "Reg Z 12 CFR 1026.13(a)(3) -- no billing error where..." as a
    `cites` entry four times in one narration. The veto caught all four and
    discarded the narration, which is the system working; but a prompt that
    reliably induces the failure it is guarded against wastes a model call
    on every case, so the layout does not invite the mistake any more.
    """
    pad = "  " * indent
    detail = f" -- {node.description}" if node.description else ""
    lines.append(f"{pad}Rule {node.rule_id} established '{node.head}'{detail}")
    if node.legal_basis:
        lines.append(f"{pad}  (legal basis, prose only, NOT an id: {node.legal_basis})")
    for witness in node.literals:
        state = "satisfied" if witness.satisfied else "NOT satisfied"
        negated = "NOT " if witness.negated else ""
        lines.append(f"{pad}  - {negated}{witness.predicate}: {state}")
        for nid in witness.evidence_node_ids:
            lines.append(f"{pad}      established by node id: {nid}")
        if not witness.evidence_node_ids:
            lines.append(f"{pad}      (derived; no evidence node id to cite)")
        node_ids.update(witness.evidence_node_ids)
        if witness.child is not None:
            _walk(witness.child, lines, node_ids, indent + 2)


def _build_prompt(evaluation: EvaluationResult, rulepack: RulePack,
                  lines: List[str], node_ids: Set[str]) -> str:
    allowed = "\n".join(f"  - {nid}" for nid in sorted(node_ids))
    derivation = "\n".join(lines)
    example_id = sorted(node_ids)[0]
    return f"""You are writing the written explanation that a card member and a merchant will \
both read, explaining a payment dispute that has ALREADY BEEN DECIDED by a deterministic \
rule engine.

REASON CODE: {rulepack.reason_code}
OUTCOME ALREADY DECIDED: {evaluation.decision_head}

This outcome is a fact you are describing. It is not yours to review, question, \
soften, or argue against. Your only job is to explain, in plain language, how the \
derivation below leads to it.

THE DERIVATION:
{derivation}

THE COMPLETE LIST OF EVIDENCE NODE IDS YOU MAY CITE. Every one is a UUID. \
Nothing else in this prompt is a node id -- not rule names, not predicate names, \
and not the legal-basis text:
{allowed}

Requirements, all mandatory:
1. Write at most {_MAX_SENTENCES} sentences of plain, non-technical English.
2. Every sentence must cite at least one node id, in its "cites" array.
3. A "cites" entry must be one of the UUIDs listed above, copied exactly, \
character for character. Never put a rule name, a regulation, a legal citation, \
or a sentence into "cites". If you cite anything that is not on that list, the \
ENTIRE explanation is discarded and a human's time is wasted.
4. Do not invent facts, dates, amounts, or documents that do not appear above.
5. Do not speculate about what either party intended or should have done.

Return JSON in exactly this shape:
{{"sentences": [{{"text": "Plain English sentence.", "cites": ["{example_id}"]}}]}}"""


def render_llm_narration(evaluation: EvaluationResult, rulepack: RulePack) -> Optional[Narration]:
    """Generate an exception-path narration, or None.

    None is returned for every failure mode -- no decision head, no proof
    tree, no citable evidence, model unreachable, malformed output. The
    caller (`narrate.ground.render_narration_safe`) treats None and a
    failed grounding check identically, so there is exactly one fallback
    path to reason about.
    """
    head = evaluation.decision_head
    if head is None or head not in evaluation.proof_trees:
        return None

    lines: List[str] = []
    node_ids: Set[str] = set()
    _walk(evaluation.proof_trees[head], lines, node_ids)

    # Nothing to cite means nothing this boundary can safely produce: every
    # sentence is required to carry a citation, so a narration generated
    # with no available node ids could only ever be ungrounded.
    if not node_ids:
        return None

    try:
        completion = complete_json(
            _build_prompt(evaluation, rulepack, lines, node_ids),
            schema=_RESPONSE_SCHEMA,
        )
    except Exception:  # pragma: no cover - complete_json swallows its own failures
        logger.warning("LLM narration call raised; falling back to the template", exc_info=True)
        return None

    if not isinstance(completion, dict):
        return None

    sentences: List[str] = []
    citations: List[Citation] = []
    for raw in (completion.get("sentences") or [])[:_MAX_SENTENCES]:
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text") or "").strip()[:_MAX_SENTENCE_CHARS]
        if not text:
            continue
        idx = len(sentences)
        sentences.append(text)
        for cited in raw.get("cites") or []:
            # Deliberately NOT filtered against `node_ids`. A fabricated id
            # must reach the verifier -- that is the veto, and screening it
            # out here would turn this boundary into an unguarded one.
            if isinstance(cited, str) and cited.strip():
                citations.append(Citation(sentence_idx=idx, node_id=cited.strip()))

    if not sentences:
        return None

    # Every sentence must carry at least one citation. Prose with an empty
    # `cites` array is exactly the uncited assertion this boundary exists
    # to refuse, and `verify_citations` cannot catch it -- it checks the
    # citations that are present, not the sentences that have none.
    cited_indices = {c.sentence_idx for c in citations}
    if any(i not in cited_indices for i in range(len(sentences))):
        logger.info("LLM narration discarded: %d sentence(s) carried no citation",
                    len(sentences) - len(cited_indices))
        return None

    return Narration(
        text=" ".join(sentences),
        sentences=tuple(sentences),
        citations=tuple(citations),
        source="llm_exception_path",
    )
