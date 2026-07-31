"""
Semantic contradiction layer (A6, layer 4) -- **DeBERTa-NLI only**.

This layer detects when two pieces of evidence make claims about the same
real-world fact that cannot both be true: the merchant's communication says
the order shipped complete, the carrier's exception record says it was
returned to sender.

**Engine policy, which is a hard constraint and not a configurable
default:** the classifier is DeBERTa-v3-MNLI and nothing else. No
generative model, no LLM, no "fall back to a heuristic if the model is
missing." The reasoning is in `arbiter.evidence.nli`'s module docstring;
the short version is that this layer hard-blocks auto-resolution (D24), and
a component with that authority cannot be a sampled generation, cannot be a
fourth unguarded injection surface, and cannot share a failure mode with
the model that extracted the text it reads.

**This layer is MANDATORY.** It previously used a polarity comparison over
a boolean `claim_polarity` attribute that nothing in the system ever
populated -- so it ran on every case, found nothing on every case, and
`contradiction_clarity` was a constant 1.0. A layer that always returns
"clean" is indistinguishable from one that is switched off, which is how
it went unnoticed. Now:

  - the layer reports its own status alongside its findings;
  - if the classifier cannot load, the status is UNAVAILABLE and the case
    **escalates** -- it is never recorded as "no contradictions found".

An unrunnable mandatory check is an unknown. An unknown is a human's
problem, not a pass.
"""

from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Tuple

from .nli import NLIUnavailable, classify_pairs

logger = logging.getLogger(__name__)

# Cap on pairs sent to the cross-encoder per case. Comparisons are
# O(k^2) within a subject group; at 10^2-10^3 evidence nodes per case with
# type-compatible pruning this is generous, and it bounds worst-case
# latency on a pathological case rather than letting one case stall a
# worker.
MAX_PAIRS_PER_CASE = 240


class LayerStatus(Enum):
    OK = "OK"                    # ran to completion
    UNAVAILABLE = "UNAVAILABLE"  # engine could not load -- MUST escalate
    NOT_APPLICABLE = "NOT_APPLICABLE"  # fewer than two comparable claims


@dataclass(frozen=True)
class SemanticClaim:
    """One textual assertion about a normalised real-world fact.

    `subject_key` groups claims that are ABOUT the same thing, so the
    classifier only ever compares type-compatible pairs -- feeding it every
    pair of sentences in a case would be both quadratically expensive and
    semantically meaningless.
    """

    subject_key: str
    node_id: str
    source_text: str
    # Retained for display and for the evidence graph's own bookkeeping.
    # NOT used for detection: a boolean polarity flag is exactly the
    # heuristic this layer replaced.
    polarity: Optional[bool] = None


@dataclass(frozen=True)
class SemanticContradiction:
    kind: str
    severity: str
    description: str
    node_ids: Tuple[str, ...]
    confidence: float = 0.0


@dataclass(frozen=True)
class SemanticAnalysis:
    status: LayerStatus
    contradictions: List[SemanticContradiction]
    pairs_evaluated: int = 0
    unavailable_reason: Optional[str] = None

    @property
    def must_escalate(self) -> bool:
        """True when a mandatory layer could not run over evidence it
        should have examined."""
        return self.status is LayerStatus.UNAVAILABLE


def _comparable_pairs(claims: List[SemanticClaim]) -> List[Tuple[SemanticClaim, SemanticClaim]]:
    by_subject: Dict[str, List[SemanticClaim]] = {}
    for claim in claims:
        if claim.source_text and claim.source_text.strip():
            by_subject.setdefault(claim.subject_key, []).append(claim)

    pairs: List[Tuple[SemanticClaim, SemanticClaim]] = []
    for group in by_subject.values():
        if len(group) < 2:
            continue
        pairs.extend(itertools.combinations(group, 2))
    return pairs[:MAX_PAIRS_PER_CASE]


def analyze_semantic_claims(claims: List[SemanticClaim]) -> SemanticAnalysis:
    """Run the mandatory semantic layer and report its status honestly."""
    pairs = _comparable_pairs(claims)
    if not pairs:
        # Genuinely nothing to compare -- distinct from "the engine could
        # not run". A case with fewer than two textual claims about the
        # same subject has no semantic contradiction to find, and must not
        # be escalated for that.
        return SemanticAnalysis(LayerStatus.NOT_APPLICABLE, [], 0)

    try:
        verdicts = classify_pairs([(a.source_text, b.source_text) for a, b in pairs])
    except NLIUnavailable as exc:
        return SemanticAnalysis(
            LayerStatus.UNAVAILABLE, [], len(pairs), unavailable_reason=str(exc),
        )

    found: List[SemanticContradiction] = []
    for (claim_a, claim_b), verdict in zip(pairs, verdicts, strict=True):
        if not verdict.is_contradiction:
            continue
        found.append(SemanticContradiction(
            kind="SEMANTIC_CONTRADICTION",
            # HIGH, not CRITICAL: two sources disagreeing in natural
            # language is a genuine conflict a human should adjudicate, but
            # it is a model's reading of text, not an arithmetic
            # impossibility like a delivery predating its own shipment.
            severity="HIGH",
            description=(
                f"'{claim_a.subject_key}': {claim_a.node_id} states "
                f"{claim_a.source_text!r} but {claim_b.node_id} states "
                f"{claim_b.source_text!r} -- DeBERTa-NLI classifies these as "
                f"contradictory (p={verdict.confidence:.2f})"
            ),
            node_ids=(claim_a.node_id, claim_b.node_id),
            confidence=verdict.confidence,
        ))

    return SemanticAnalysis(LayerStatus.OK, found, len(pairs))


def detect_semantic_contradictions(claims: List[SemanticClaim]) -> List[SemanticContradiction]:
    """Findings only.

    Prefer `analyze_semantic_claims`, which also reports whether the layer
    actually ran -- callers that need the mandatory-layer guarantee cannot
    get it from a bare list, because an empty list is ambiguous between
    "clean" and "did not run".
    """
    return analyze_semantic_claims(claims).contradictions
