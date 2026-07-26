"""
Grounded narration (§8, §6.6): template renderer by default, since the proof
tree already contains everything a narration needs to say -- templates are
faithful by construction, free, and deterministic. No LLM runs in this
environment (or, per §8, on ~80% of cases in production either); what's
built here is the template path plus the citation-grounding verifier that
would gate an LLM-generated exception-path narration in the full system:
"every narration sentence must map to an evidence node ID; mechanically
verified; any ungrounded sentence -> drop the LLM output entirely and fall
back to the template renderer" (§6.6). That fallback is implemented and
tested here against a deliberately corrupted narration, not merely
described.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from packages.datalog.engine import EvaluationResult, ProofNode, RulePack
from services.counterfactual.counterfactual import Counterfactual


@dataclass(frozen=True)
class Citation:
    sentence_idx: int
    node_id: str

    def to_dict(self) -> dict:
        return {"sentence_idx": self.sentence_idx, "node_id": self.node_id}


@dataclass(frozen=True)
class Narration:
    text: str
    sentences: Tuple[str, ...]
    citations: Tuple[Citation, ...]
    source: str  # "template" | "llm_exception_path" (never actually taken here)

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "source": self.source,
            "citations": [c.to_dict() for c in self.citations],
        }


def _predicate_phrase(predicate: str, negated: bool) -> str:
    words = predicate.replace("_", " ")
    return f"the absence of {words}" if negated else words


def _sentence_for_rule(node: ProofNode) -> Tuple[str, List[str]]:
    clauses = [_predicate_phrase(w.predicate, w.negated) for w in node.literals]
    evidence_ids: List[str] = []
    for w in node.literals:
        evidence_ids.extend(w.evidence_node_ids)
    clause_text = "; ".join(clauses)
    desc = f" ({node.description.strip()})" if node.description.strip() else ""
    sentence = f"Rule {node.rule_id}{desc} is satisfied by: {clause_text}."
    return sentence, evidence_ids


def render_decision_narration(
    evaluation: EvaluationResult,
    rulepack: RulePack,
    counterfactuals: Optional[Dict[str, Counterfactual]] = None,
) -> Narration:
    sentences: List[str] = []
    citations: List[Citation] = []

    def add(sentence: str, evidence_ids: List[str]) -> None:
        idx = len(sentences)
        sentences.append(sentence)
        for nid in evidence_ids:
            citations.append(Citation(sentence_idx=idx, node_id=nid))

    if evaluation.conflicting_outcomes:
        add(
            f"Reason code {evaluation.reason_code} (rulepack {evaluation.rulepack_hash[:12]}): the evidence "
            f"gathered so far satisfies rules for more than one outcome at once "
            f"({', '.join(evaluation.conflicting_outcomes)}) -- a genuine evidentiary conflict, not an "
            f"absence of evidence. This is not resolved automatically; it requires a human reviewer.",
            [],
        )
        for outcome in evaluation.conflicting_outcomes:
            head = rulepack.decision_predicates.get(outcome)
            if head is not None and head in evaluation.proof_trees:
                sentence, evidence_ids = _sentence_for_rule(evaluation.proof_trees[head])
                add(f"[{outcome}] {sentence}", evidence_ids)
    elif evaluation.decision is None:
        add(
            f"No rule in reason code {evaluation.reason_code} (rulepack {evaluation.rulepack_hash[:12]}) "
            f"was satisfied by the evidence gathered so far; the case does not yet resolve on the merits.",
            [],
        )
        if evaluation.missing_predicates:
            add(
                "The following facts remain unestablished: "
                + ", ".join(sorted(p.replace('_', ' ') for p in evaluation.missing_predicates))
                + ".",
                [],
            )
    else:
        proof = evaluation.proof_trees[evaluation.decision_head]
        add(
            f"Outcome: {evaluation.decision}, under reason code {evaluation.reason_code} "
            f"(rulepack {evaluation.rulepack_hash[:12]}).",
            [],
        )
        sentence, evidence_ids = _sentence_for_rule(proof)
        add(sentence, evidence_ids)

        def walk_children(node: ProofNode) -> None:
            for w in node.literals:
                if w.child is not None:
                    s, eids = _sentence_for_rule(w.child)
                    add(s, eids)
                    walk_children(w.child)

        walk_children(proof)

    if counterfactuals:
        for outcome, cf in counterfactuals.items():
            if outcome == evaluation.decision or cf.already_satisfied or cf.mwc is None:
                continue
            missing_desc = "; ".join(
                f"{'retract' if d.action == 'RETRACT' else 'obtain'} evidence of "
                f"{_predicate_phrase(d.predicate, False)}"
                for d in cf.delta
            )
            add(
                f"For outcome {outcome} to have prevailed instead, the following would need to change: "
                f"{missing_desc}.",
                [],
            )

    text = " ".join(sentences)
    return Narration(text=text, sentences=tuple(sentences), citations=tuple(citations), source="template")


def verify_citations(narration: Narration, valid_node_ids: Set[str]) -> Tuple[bool, List[Citation]]:
    """§6.6: every narration sentence's citations must resolve to a real
    evidence node. Returns (all_valid, ungrounded_citations)."""
    ungrounded = [c for c in narration.citations if c.node_id not in valid_node_ids]
    return (len(ungrounded) == 0, ungrounded)


_FALLBACK_TEXT = "A decision was reached; the detailed narration failed grounding verification and was withheld. See the proof tree for the machine-checked derivation."


def render_narration_safe(
    evaluation: EvaluationResult,
    rulepack: RulePack,
    valid_node_ids: Set[str],
    counterfactuals: Optional[Dict[str, Counterfactual]] = None,
) -> Narration:
    """The actual call site: render, verify, and fall back to a minimal,
    trivially-grounded narration if verification ever fails. In this build
    the template renderer only ever cites node_ids it read directly out of
    the proof tree, so this should never trigger -- it exists because the
    real system's LLM exception path needs exactly this fallback, and the
    fallback logic itself is worth exercising, not just asserting (see
    evals/property_tests.py: test_narration_fallback_on_corruption)."""
    narration = render_decision_narration(evaluation, rulepack, counterfactuals)
    ok, ungrounded = verify_citations(narration, valid_node_ids)
    if ok:
        return narration
    return Narration(text=_FALLBACK_TEXT, sentences=(_FALLBACK_TEXT,), citations=(), source="template_fallback")
