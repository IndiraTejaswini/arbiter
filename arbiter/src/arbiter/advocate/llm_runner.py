"""
LLM BOUNDARY 3 — advocate search, turned up.

Honest scoping note before the implementation: `arbiter.advocate.runner`'s
deterministic search is not a cut-down stand-in for a smarter LLM search --
`arbiter.horn.implicants.enumerate_prime_implicants` is EXACT and COMPLETE
over the already-derived predicate facts, so there is no rule path a
combinatorial search could find that the deterministic advocate missed. An
LLM advocate's genuine marginal value in this architecture is narrower than
"finds arguments a human would miss": it is reading the evidence graph's
raw extracted fields -- typed `ExtractedField` values, never free prose --
for predicate-relevant signal that `arbiter.ingest.route`'s intentionally
partial `_PREDICATE_HINTS` table didn't mechanically tag. That is a real,
useful job (coverage against under-tagged evidence), just a smaller one
than "search the whole rule space," because the rule space is already
exhaustively searched.

The safety property holds regardless of how much or little value the LLM
adds: every proposed assertion -- whether it turns out to matter or not --
is re-derived from the objective facts by the SAME `verify_assertions`
the deterministic path is checked against. An LLM assertion about a
predicate the graph never established is rejected exactly like a fabricated
one, and counted in `llm_rejections`. This module never special-cases its
own output to bypass that check.

Deterministic search stays the default and the fallback (CLAUDE.md: "the
rules-only path must keep working with every LLM disabled"). This module is
called, if at all, AFTER the deterministic advocate, and only ever ADDS
triples that independently pass verification -- it can enlarge an
ArgumentGraph, never replace one.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from arbiter.horn.clause import RulePack
from arbiter.horn.proof import Fact

from arbiter.llm.client import complete_json, is_available
from .contract import ArgumentGraph, ArgumentTriple
from .verify import TripleVerification, verify_assertions

_SCHEMA = {
    "type": "object",
    "properties": {
        "assertions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "predicate_id": {"type": "string"},
                    "evidence_node_id": {"type": "string"},
                    "reasoning": {"type": "string"},
                },
                "required": ["predicate_id", "evidence_node_id", "reasoning"],
            },
        }
    },
    "required": ["assertions"],
}

_PROMPT_TEMPLATE = """You are one advocate in a dispute-adjudication system, arguing for the \
{side_label} to prevail under outcome {target_outcome}. You do not decide the case -- a \
deterministic rule engine does, using ONLY facts that are independently re-verified against \
the evidence graph. Your job is to propose evidence-to-predicate links a mechanical tagger \
might have missed.

Predicates relevant to your side's outcome (propose ONLY from this list): {predicate_list}

Evidence nodes available (id, type, provenance tier, and any raw extracted fields -- \
transcribe what you see, do not infer beyond it):
{evidence_summary}

For each predicate you believe a specific evidence node supports, propose an assertion: \
predicate_id (must be from the list above), evidence_node_id (must be from the nodes above), \
and a short reasoning string (<=280 chars) citing what in that node supports it. Propose \
nothing if you don't see clear support -- an unsupported proposal will be rejected and logged \
against you. Respond with JSON: {{"assertions": [...]}}."""


def _evidence_summary(graph, node_ids: List[str], max_nodes: int = 40) -> str:
    lines = []
    for node_id in node_ids[:max_nodes]:
        node = graph.nodes.get(node_id)
        if node is None:
            continue
        extracted = node.attrs.get("extracted_fields")
        field_str = ""
        if extracted:
            field_str = "; ".join(f"{f['field_name']}={f['value']!r}" for f in extracted[:6])
        else:
            asserted = node.attrs.get("asserts_predicate")
            if asserted:
                field_str = f"asserts_predicate={asserted}={node.attrs.get('predicate_value')}"
        lines.append(f"- {node_id} [{node.node_type.value}, tier={node.provenance.value}] {field_str}")
    return "\n".join(lines) if lines else "(no evidence nodes)"


def run_llm_advocate(
    rulepack: RulePack,
    graph,
    facts: Dict[str, Fact],
    side: str,
    target_outcome: str,
) -> Tuple[List[ArgumentTriple], List[TripleVerification]]:
    """Returns (verified_triples, all_verifications) -- callers merge
    `verified_triples` into their ArgumentGraph and accumulate
    `all_verifications` (specifically the rejected ones) into
    `decision.llm_rejections`. Returns ([], []) if the model is
    unavailable -- an empty, harmless result, never an exception."""
    if not is_available():
        return [], []

    head = rulepack.decision_predicates.get(target_outcome)
    if head is None:
        return [], []

    schema_predicates = sorted(set(rulepack.predicate_schema) or rulepack.edb_predicates())
    predicate_descriptions = ", ".join(schema_predicates)

    prompt = _PROMPT_TEMPLATE.format(
        side_label="card member" if side == "CM" else "merchant",
        target_outcome=target_outcome,
        predicate_list=predicate_descriptions,
        evidence_summary=_evidence_summary(graph, list(graph.nodes.keys())),
    )

    parsed = complete_json(prompt, _SCHEMA)
    if parsed is None:
        return [], []

    proposed_triples: List[ArgumentTriple] = []
    for item in parsed.get("assertions", [])[:20]:
        predicate_id = str(item.get("predicate_id", ""))
        node_id = str(item.get("evidence_node_id", ""))
        if predicate_id not in schema_predicates or node_id not in graph.nodes:
            continue  # not even worth sending to verification -- malformed proposal
        proposed_triples.append(ArgumentTriple(
            predicate=predicate_id, negated=False, evidence_node_ids=(node_id,), warrant_rule_id=None,
        ))

    if not proposed_triples:
        return [], []

    temp_graph = ArgumentGraph(
        side=side, target_outcome=target_outcome, target_head=head, triples=tuple(proposed_triples),
        missing_literals=(), fully_satisfied=False,
    )
    verifications, _ = verify_assertions([temp_graph], facts)
    verified_triples = [v.triple for v in verifications if v.verified]
    return verified_triples, verifications
