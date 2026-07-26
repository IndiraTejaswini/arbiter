"""
Semantic contradiction layer (§A6, layer 4) -- deterministic stand-in for
DeBERTa-v3-large-MNLI entailment/contradiction classification (§8's model
table).

No model weights are available in this environment, so this module
reproduces the *interface and outcome* the real component would produce --
typed claim pairs in, contradiction findings out -- using direct polarity
comparison over a normalized subject key instead of a trained classifier.
Swapping in the real model is a drop-in replacement: for every pair of
claims sharing a subject_key, feed (claim_a.source_text, claim_b.source_text)
to the NLI model and use its CONTRADICTION label instead of the polarity
check below. The call site (services/graph/graph.py) does not need to
change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple


@dataclass(frozen=True)
class SemanticClaim:
    subject_key: str  # normalized real-world fact this claim is about
    polarity: bool
    node_id: str
    source_text: str = ""


@dataclass(frozen=True)
class SemanticContradiction:
    kind: str
    severity: str
    description: str
    node_ids: Tuple[str, ...]


def detect_semantic_contradictions(claims: List[SemanticClaim]) -> List[SemanticContradiction]:
    by_subject: Dict[str, List[SemanticClaim]] = {}
    for c in claims:
        by_subject.setdefault(c.subject_key, []).append(c)

    out: List[SemanticContradiction] = []
    for subject, group in by_subject.items():
        if len(group) < 2:
            continue
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                ci, cj = group[i], group[j]
                if ci.polarity != cj.polarity:
                    out.append(
                        SemanticContradiction(
                            kind="CLAIM_POLARITY_CONFLICT",
                            severity="HIGH",
                            description=(
                                f"'{subject}' asserted {ci.polarity} by {ci.node_id}"
                                + (f" ({ci.source_text!r})" if ci.source_text else "")
                                + f" but {cj.polarity} by {cj.node_id}"
                                + (f" ({cj.source_text!r})" if cj.source_text else "")
                            ),
                            node_ids=(ci.node_id, cj.node_id),
                        )
                    )
    return out
