"""
Numeric reconciliation layer (§A6, layer 2): order total -> authorization ->
settlement -> refund, with tolerances for tip adjustment, FX, and partial
capture.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Basis points of tolerance before a same-currency mismatch is flagged
# (covers tip adjustment / rounding).
DEFAULT_TOLERANCE_BPS = 300  # 3%


@dataclass(frozen=True)
class NumericContradiction:
    kind: str
    severity: str
    description: str
    node_ids: tuple


@dataclass(frozen=True)
class MoneyAmount:
    minor_units: int
    currency: str
    node_id: str
    label: str


def reconcile_pair(a: MoneyAmount, b: MoneyAmount, allow_partial: bool = False) -> Optional[NumericContradiction]:
    """
    Compare two amounts meant to correspond (e.g. order_total vs
    authorization, or authorization vs settlement). `allow_partial` permits
    `b` to be less than `a` (partial capture / partial refund) without
    flagging -- only an *excess* or a same-currency mismatch beyond
    tolerance is a contradiction.

    **Cross-currency pairs are not reconciled at all.** Stated plainly here
    because the code used to say otherwise: a comment claimed cross-currency
    pairs were flagged past a wider "FX tolerance", and an `FX_TOLERANCE_BPS
    = 800` constant existed with a `_tolerance_bps` helper that selected it
    -- but the currency check returned before either could ever be reached.
    The branch was unreachable, the constant dead, and the stated behaviour
    had never once executed.

    Removing them rather than implementing the check is the honest fix at
    this layer: comparing amounts across currencies needs a rate lookup at
    the transaction's value date, not a wider tolerance. Widening a
    tolerance to 8% and calling it FX handling would flag nothing real while
    claiming a check exists, which is worse than the documented gap. Until a
    rate source is wired in, a cross-currency mismatch is invisible to
    numeric reconciliation -- that is a known limitation, now written where
    someone will read it instead of implied by dead code.
    """
    if a.currency != b.currency:
        return None

    tolerance = a.minor_units * DEFAULT_TOLERANCE_BPS // 10_000

    if allow_partial and b.minor_units <= a.minor_units + tolerance:
        return None

    diff = abs(a.minor_units - b.minor_units)
    if diff > tolerance:
        return NumericContradiction(
            kind="AMOUNT_MISMATCH",
            severity="HIGH" if diff > tolerance * 3 else "MEDIUM",
            description=(
                f"{a.label} ({a.minor_units} {a.currency}) vs {b.label} "
                f"({b.minor_units} {b.currency}) differ by {diff} minor units, "
                f"exceeding tolerance of {tolerance}"
            ),
            node_ids=(a.node_id, b.node_id),
        )
    return None


def reconcile_chain(
    order_total: Optional[MoneyAmount] = None,
    authorization: Optional[MoneyAmount] = None,
    settlement: Optional[MoneyAmount] = None,
    refund: Optional[MoneyAmount] = None,
) -> list:
    """Walk order_total -> authorization -> settlement, and separately check
    refund against order_total (refunds may legitimately be partial)."""
    out = []
    if order_total and authorization:
        c = reconcile_pair(order_total, authorization)
        if c:
            out.append(c)
    if authorization and settlement:
        c = reconcile_pair(authorization, settlement, allow_partial=True)
        if c:
            out.append(c)
    if refund and order_total:
        c = reconcile_pair(order_total, refund, allow_partial=True)
        if c:
            out.append(c)
    return out
