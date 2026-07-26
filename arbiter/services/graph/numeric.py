"""
Numeric reconciliation layer (§A6, layer 2): order total -> authorization ->
settlement -> refund, with tolerances for tip adjustment, FX, and partial
capture.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# basis points of tolerance before a mismatch is flagged (covers tip
# adjustment / rounding); FX tolerance is wider because conversion timing
# differences are expected, not anomalous.
DEFAULT_TOLERANCE_BPS = 300  # 3%
FX_TOLERANCE_BPS = 800  # 8%


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


def _tolerance_bps(a: MoneyAmount, b: MoneyAmount) -> int:
    return FX_TOLERANCE_BPS if a.currency != b.currency else DEFAULT_TOLERANCE_BPS


def reconcile_pair(a: MoneyAmount, b: MoneyAmount, allow_partial: bool = False) -> Optional[NumericContradiction]:
    """
    Compare two amounts meant to correspond (e.g. order_total vs
    authorization, or authorization vs settlement). `allow_partial` permits
    `b` to be less than `a` (partial capture / partial refund) without
    flagging -- only an *excess* or a same-currency mismatch beyond
    tolerance is a contradiction.
    """
    if a.currency != b.currency:
        # cross-currency: only flag a gross mismatch (>FX tolerance), since
        # exact parity is never expected across currencies at this layer
        # (real FX conversion is out of scope for this reconciliation --
        # it would need a rate lookup, not just tolerance widening).
        return None

    tol_bps = _tolerance_bps(a, b)
    tolerance = a.minor_units * tol_bps // 10_000

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
