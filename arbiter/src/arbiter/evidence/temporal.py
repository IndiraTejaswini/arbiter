"""
Temporal consistency layer (§A6, layer 1): Allen's interval algebra applied
to dated evidence assertions.

Scoping note, stated honestly: the architecture calls for "Allen's interval
algebra + O(n^3) path consistency" over a general constraint network. The
fully general version needs the 13x13 Allen composition table and handles
disjoint, disconnected timelines. Every event in a dispute case, however,
lives on a single wall-clock timeline -- there is no scheduling-style
branching -- so the general composition-table machinery is more than the
problem requires. What is implemented here is the fragment that actually
matters for disputes: (1) an exact, always-well-defined Allen relation
classifier between two concrete time ranges, used for narration ("the
cancellation email precedes the carrier's delivery scan"), and (2) O(n^2)
same-fact-disagreement detection plus O(n^2) domain-ordering-constraint
checking, which is where real dispute timelines actually break. This is a
deliberate scope reduction from the general path-consistency algorithm, not
a missed corner -- see the module-level comment in packages/datalog/engine.py
for the same kind of honest-scoping call made elsewhere in this system.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Sequence, Tuple


class AllenRelation(Enum):
    BEFORE = "before"
    AFTER = "after"
    MEETS = "meets"
    MET_BY = "met_by"
    OVERLAPS = "overlaps"
    OVERLAPPED_BY = "overlapped_by"
    STARTS = "starts"
    STARTED_BY = "started_by"
    DURING = "during"
    CONTAINS = "contains"
    FINISHES = "finishes"
    FINISHED_BY = "finished_by"
    EQUALS = "equals"


@dataclass(frozen=True)
class TimeInterval:
    """A concrete tstzrange. `start == end` represents a point-in-time event
    (a carrier scan, an email timestamp) -- the common case in this domain."""

    start: datetime
    end: datetime
    label: str = ""

    def __post_init__(self):
        if self.end < self.start:
            raise ValueError(f"interval end {self.end} precedes start {self.start}")


def base_relation(a: TimeInterval, b: TimeInterval) -> AllenRelation:
    """
    Exact classification into one of Allen's 13 base relations.

    Checks are ordered by specificity, which matters for degenerate
    point-in-time events (start == end, the common case here: a carrier
    scan or an email timestamp). A point exactly at another interval's
    start satisfies both "a.start == b.start" (-> STARTS) and "a.end ==
    b.start" (-> MEETS) simultaneously; the standard convention -- and the
    one used here -- is that the more specific coincidence (equals, then
    starts/finishes) takes priority over the boundary-touch relations
    (meets/met_by), which are only classified once genuine start/end
    equality has been ruled out.
    """
    if a.end < b.start:
        return AllenRelation.BEFORE
    if a.start > b.end:
        return AllenRelation.AFTER
    if a.start == b.start and a.end == b.end:
        return AllenRelation.EQUALS
    if a.start == b.start:
        return AllenRelation.STARTS if a.end < b.end else AllenRelation.STARTED_BY
    if a.end == b.end:
        return AllenRelation.FINISHES if a.start > b.start else AllenRelation.FINISHED_BY
    if a.end == b.start:
        return AllenRelation.MEETS
    if a.start == b.end:
        return AllenRelation.MET_BY
    if a.start > b.start and a.end < b.end:
        return AllenRelation.DURING
    if a.start < b.start and a.end > b.end:
        return AllenRelation.CONTAINS
    if a.start < b.start and a.end < b.end:
        return AllenRelation.OVERLAPS
    if a.start > b.start and a.end > b.end:
        return AllenRelation.OVERLAPPED_BY
    raise AssertionError(f"unclassified interval pair: {a} vs {b}")


@dataclass(frozen=True)
class TemporalFact:
    """One dated assertion about a named real-world fact (e.g. 'the delivery
    of shipment ship-1'), sourced from one evidence node."""

    fact_key: str  # groups facts purporting to describe the SAME real-world event
    node_id: str
    interval: TimeInterval
    provenance_weight: float = 1.0


@dataclass(frozen=True)
class TemporalContradiction:
    kind: str
    severity: str  # LOW | MEDIUM | HIGH
    description: str
    node_ids: Tuple[str, ...]


# fact_key_a must (per the rulepack's domain semantics) occur no later than
# fact_key_b -- violations are a first-class contradiction signal (§A6).
DOMAIN_ORDERING_CONSTRAINTS: Tuple[Tuple[str, str, str], ...] = (
    ("cancellation", "shipment", "cancellation requested after the order had already shipped"),
    ("authorization", "settlement", "settlement recorded before the authorization that should precede it"),
    ("shipment", "delivery", "delivery recorded before the shipment that should precede it"),
    ("return_shipped", "return_delivered", "return marked delivered before it was shipped"),
    ("refund_promise", "refund_issued", "refund issued before it was ever promised (informational only)"),
)


def detect_same_fact_disagreement(
    facts: Sequence[TemporalFact], tolerance: timedelta = timedelta(hours=6)
) -> List[TemporalContradiction]:
    """
    Multiple evidence nodes purporting to date the SAME real-world event
    (grouped by fact_key) whose intervals don't agree within tolerance --
    e.g. "merchant asserts delivery 2026-03-05; carrier record shows
    2026-03-08" (§A6's worked example). This is the single most direct
    temporal contradiction signal in the domain.
    """
    by_key: Dict[str, List[TemporalFact]] = {}
    for f in facts:
        by_key.setdefault(f.fact_key, []).append(f)

    out: List[TemporalContradiction] = []
    for key, group in by_key.items():
        if len(group) < 2:
            continue
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                fi, fj = group[i], group[j]
                gap = max(
                    fi.interval.start - fj.interval.end,
                    fj.interval.start - fi.interval.end,
                    timedelta(0),
                )
                if gap > tolerance:
                    severity = "HIGH" if gap > tolerance * 4 else "MEDIUM"
                    out.append(
                        TemporalContradiction(
                            kind="SAME_FACT_DISAGREEMENT",
                            severity=severity,
                            description=(
                                f"conflicting dates for '{key}': "
                                f"{fi.node_id} asserts {fi.interval.start.isoformat()}, "
                                f"{fj.node_id} asserts {fj.interval.start.isoformat()} "
                                f"(gap {gap})"
                            ),
                            node_ids=(fi.node_id, fj.node_id),
                        )
                    )
    return out


def _representative_interval(group: Sequence[TemporalFact]) -> Tuple[TemporalFact, TimeInterval]:
    """When several nodes date the same fact_key, prefer the highest
    provenance-weighted one as the representative for ordering checks."""
    best = max(group, key=lambda f: f.provenance_weight)
    return best, best.interval


def detect_ordering_violations(facts: Sequence[TemporalFact]) -> List[TemporalContradiction]:
    """
    O(n^2) check of declared domain-ordering constraints (a restricted,
    dispute-specific instance of path consistency, see module docstring):
    for each (earlier_key, later_key) pair with facts present for both,
    verify the Allen relation is one that respects "earlier before-or-meets
    later". Any violation is an unsatisfiable-network signal.
    """
    by_key: Dict[str, List[TemporalFact]] = {}
    for f in facts:
        by_key.setdefault(f.fact_key, []).append(f)

    out: List[TemporalContradiction] = []
    for earlier_key, later_key, description in DOMAIN_ORDERING_CONSTRAINTS:
        if earlier_key not in by_key or later_key not in by_key:
            continue
        e_fact, e_interval = _representative_interval(by_key[earlier_key])
        l_fact, l_interval = _representative_interval(by_key[later_key])
        rel = base_relation(e_interval, l_interval)
        if rel not in (AllenRelation.BEFORE, AllenRelation.MEETS, AllenRelation.EQUALS):
            out.append(
                TemporalContradiction(
                    kind="ORDERING_VIOLATION",
                    severity="HIGH",
                    description=f"{description}: {earlier_key}={e_interval.start.isoformat()}, "
                    f"{later_key}={l_interval.start.isoformat()} (relation={rel.value})",
                    node_ids=(e_fact.node_id, l_fact.node_id),
                )
            )
    return out


def detect_temporal_contradictions(facts: Sequence[TemporalFact]) -> List[TemporalContradiction]:
    return detect_same_fact_disagreement(facts) + detect_ordering_violations(facts)
