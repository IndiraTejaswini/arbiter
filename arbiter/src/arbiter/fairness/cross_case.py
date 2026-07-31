"""
Cross-case signals: patterns visible only across MANY cases (a device
fingerprint or perceptual hash recurring across dozens of disputes filed by
different card members against the same merchant) that a single case's
evidence graph can never surface on its own.

These are exactly the kind of finding a human reviewer should see and
weigh -- "this device fingerprint has touched 12 disputes from 9 different
card members in the last quarter" is a strong signal of a fraud ring; "this
exact document template, same background artifacts, recurs across 5
disputes against the same merchant" (the same perceptual-hash technique
`arbiter.ingest.forensics` already uses per-artifact, applied here across
cases) is a strong signal of template reuse. And it is exactly the kind of
finding that must NEVER become a rulepack predicate.

A predicate has to be about a SINGLE case's own evidence, evaluated on its
own terms (C3); a cross-case frequency count is a different kind of fact
entirely -- population-level, not case-level -- and folding it into the
per-case decision function would punish an individual case for a pattern
that case has no way to rebut (the card member or merchant in THIS case
cannot produce counter-evidence about what a dozen OTHER cases looked
like). Note the distinction from F29's own `prior_undisputed_pattern`-style
predicates: those are about the SAME card member's own prior transaction
history with the SAME merchant, which the case's own party can speak to;
what this module reports spans DIFFERENT card members, which no single
case's advocate has standing to argue about at all.

This module's only intended consumer is `arbiter.decision.escalate` (the
human-reviewer dossier). `arbiter.horn` and `arbiter.evidence.derive` are
mechanically forbidden from importing it at all (pyproject.toml
import-linter contract "Referee (horn) is pure", which lists
`arbiter.fairness.cross_case` explicitly) -- "this cannot influence a
verdict" is a CI failure if violated, not a code-review judgment call.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Tuple


@dataclass(frozen=True)
class CaseFingerprint:
    """One case's cross-case-relevant identifiers -- deliberately NOT an
    EvidenceNode or Fact. This is population-level input, gathered by an
    offline job over many cases, never evidence-graph input a single
    case's derivation could see."""

    case_id: str
    card_member_id: str
    merchant_id: str
    device_ids: Tuple[str, ...] = ()
    perceptual_hashes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class CrossCaseSignal:
    kind: str  # DEVICE_FINGERPRINT_RING | TEMPLATE_REUSE
    key: str  # the shared identifier (device_id, or "merchant_id:phash")
    case_ids: Tuple[str, ...]
    distinct_card_members: int
    severity: str  # LOW | MEDIUM | HIGH

    def to_dict(self) -> dict:
        return {
            "kind": self.kind, "key": self.key, "case_ids": list(self.case_ids),
            "distinct_card_members": self.distinct_card_members, "severity": self.severity,
        }


def _severity_for(distinct_card_members: int, n_cases: int) -> str:
    if distinct_card_members >= 10 or n_cases >= 15:
        return "HIGH"
    if distinct_card_members >= 4 or n_cases >= 6:
        return "MEDIUM"
    return "LOW"


def find_device_rings(fingerprints: List[CaseFingerprint], min_distinct_card_members: int = 3) -> List[CrossCaseSignal]:
    """A device_id shared across `min_distinct_card_members`+ DIFFERENT
    card members is the signature of a fraud ring or a compromised device
    pool. One card member's device recurring across THEIR OWN prior
    undisputed transactions is a different, legitimate, single-case signal
    F29's own predicates already use (arbiter.evidence.derive); this
    function only reports devices spanning multiple DISTINCT card members,
    which no single case's evidence graph could ever see on its own."""
    by_device: Dict[str, List[CaseFingerprint]] = defaultdict(list)
    for fp in fingerprints:
        for device_id in fp.device_ids:
            by_device[device_id].append(fp)

    signals = []
    for device_id, fps in by_device.items():
        distinct_members = {fp.card_member_id for fp in fps}
        if len(distinct_members) < min_distinct_card_members:
            continue
        signals.append(CrossCaseSignal(
            kind="DEVICE_FINGERPRINT_RING", key=device_id,
            case_ids=tuple(sorted({fp.case_id for fp in fps})),
            distinct_card_members=len(distinct_members),
            severity=_severity_for(len(distinct_members), len(fps)),
        ))
    return sorted(signals, key=lambda s: (-s.distinct_card_members, s.key))


def find_template_reuse(fingerprints: List[CaseFingerprint], min_cases: int = 3) -> List[CrossCaseSignal]:
    """A perceptual hash recurring across many cases from the SAME
    merchant is the signature of a document template being reused/doctored
    across disputes (dates edited, same background artifacts) -- see
    rulepacks/README's adversarial suite for the single-artifact version
    of this same technique. Reported at merchant granularity, distinct
    from the cross-merchant device-ring signal above."""
    by_merchant_hash: Dict[Tuple[str, str], List[CaseFingerprint]] = defaultdict(list)
    for fp in fingerprints:
        for phash in fp.perceptual_hashes:
            by_merchant_hash[(fp.merchant_id, phash)].append(fp)

    signals = []
    for (merchant_id, phash), fps in by_merchant_hash.items():
        case_ids = sorted({fp.case_id for fp in fps})
        if len(case_ids) < min_cases:
            continue
        distinct_members = len({fp.card_member_id for fp in fps})
        signals.append(CrossCaseSignal(
            kind="TEMPLATE_REUSE", key=f"{merchant_id}:{phash}",
            case_ids=tuple(case_ids), distinct_card_members=distinct_members,
            severity=_severity_for(distinct_members, len(case_ids)),
        ))
    return sorted(signals, key=lambda s: (-len(s.case_ids), s.key))


def signals_for_case(case_id: str, all_signals: List[CrossCaseSignal]) -> List[CrossCaseSignal]:
    """The dossier-building call site (arbiter.decision.escalate): filters
    the population-level signal list down to what's relevant to ONE case,
    for a human reviewer's display -- never for a decision."""
    return [s for s in all_signals if case_id in s.case_ids]
