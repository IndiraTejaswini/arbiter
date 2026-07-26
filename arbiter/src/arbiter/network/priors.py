"""
Prior undisputed transaction matching -- CE3.0's own matching rule
(>=2 of {device_id, ip_address, shipping_address, user_id}, at least one of
which is device_id or ip_address, against transactions 120-365 days old and
never disputed), computed directly from raw prior-transaction records rather
than pre-computed booleans. `arbiter.network.loader.NetworkFacts` carries the
*result* of this matching; this module is what produces that result from
Amex's actual transaction history for a card member/merchant pair.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence


@dataclass(frozen=True)
class PriorTransactionRecord:
    transaction_id: str
    age_days: int
    disputed: bool
    device_id: Optional[str] = None
    ip_address: Optional[str] = None
    shipping_address: Optional[str] = None
    user_id: Optional[str] = None


@dataclass(frozen=True)
class PriorMatchResult:
    undisputed_count: int
    matched_in_window: List[PriorTransactionRecord]
    device_id_match: bool
    ip_address_match: bool
    shipping_address_match: bool
    user_id_match: bool
    ce3_qualifies: bool  # >=2 matches, >=1 of which is device_id/ip_address, within the 120-365d window


def _normalize(value: Optional[str]) -> Optional[str]:
    return value.strip().lower() if value else None


def match_priors(
    priors: Sequence[PriorTransactionRecord],
    current_device_id: Optional[str],
    current_ip_address: Optional[str],
    current_shipping_address: Optional[str],
    current_user_id: Optional[str],
    window_min_days: int = 120,
    window_max_days: int = 365,
) -> PriorMatchResult:
    undisputed = [p for p in priors if not p.disputed]
    in_window = [p for p in undisputed if window_min_days <= p.age_days <= window_max_days]

    def any_match(attr: str, current: Optional[str]) -> bool:
        current_n = _normalize(current)
        if current_n is None:
            return False
        return any(_normalize(getattr(p, attr)) == current_n for p in in_window)

    device_match = any_match("device_id", current_device_id)
    ip_match = any_match("ip_address", current_ip_address)
    shipping_match = any_match("shipping_address", current_shipping_address)
    user_match = any_match("user_id", current_user_id)

    match_count = sum([device_match, ip_match, shipping_match, user_match])
    ce3_qualifies = match_count >= 2 and (device_match or ip_match)

    return PriorMatchResult(
        undisputed_count=len(undisputed),
        matched_in_window=in_window,
        device_id_match=device_match,
        ip_address_match=ip_match,
        shipping_address_match=shipping_match,
        user_id_match=user_match,
        ce3_qualifies=ce3_qualifies,
    )
