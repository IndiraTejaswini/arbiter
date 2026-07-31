"""
Regulatory clocks (Reg Z 12 CFR 1026.13, Reg E 12 CFR 1005.11).

`dispute_case` carried `ack_deadline`, `resolve_deadline`, and
`merchant_response_deadline` from the first migration and **nothing ever
read them** -- no Temporal workflow, no scheduler, no cron, no queue. On top
of that `ack_deadline` was hardcoded to `now + 3 days`, which matches
neither regulation nor the architecture document's own 30-day figure.

The behaviour these tests pin down hardest is the merchant window, because
the architecture document calls it "the single most legible fairness
improvement in the whole system": on expiry the case is adjudicated on the
merits from Amex-held data, NOT conceded. R03/R13 -- cases decided on
process rather than merits -- is the failure mode the whole system exists
to remove, and a deadline column nobody reads removes nothing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from arbiter.decision.deadlines import (
    MERCHANT_RESPONSE_DAYS,
    REG_E_DETERMINATION_BUSINESS_DAYS,
    REG_Z_ACK_DAYS,
    REG_Z_RESOLVE_DAYS,
    add_business_days,
    compute_deadlines,
)

# A Wednesday, so weekend crossings are exercised rather than dodged.
_WED = datetime(2026, 3, 4, 12, 0, tzinfo=timezone.utc)


def test_reg_z_acknowledgment_is_30_days_not_3():
    """The regression. `ack_deadline` was `now + timedelta(days=3)`."""
    d = compute_deadlines("REG_Z", _WED)
    assert d.ack_deadline == _WED + timedelta(days=REG_Z_ACK_DAYS)
    assert REG_Z_ACK_DAYS == 30, "12 CFR 1026.13(b)(1)"
    assert (d.ack_deadline - _WED).days == 30


def test_reg_z_resolution_is_90_days():
    d = compute_deadlines("REG_Z", _WED)
    assert (d.resolve_deadline - _WED).days == REG_Z_RESOLVE_DAYS == 90


def test_reg_z_has_no_provisional_credit_clock():
    """Reg Z's analogous protection is a payment-withholding right, not a
    credit-issuance one. Modelling one would misstate the regulation."""
    assert compute_deadlines("REG_Z", _WED).provisional_credit_deadline is None


def test_reg_e_determination_counts_business_days_not_calendar_days():
    """12 CFR 1005.11(c)(1) says *business* days. Ten calendar days from a
    Wednesday lands on a Saturday; ten business days does not."""
    d = compute_deadlines("REG_E", _WED)
    naive_calendar = _WED + timedelta(days=REG_E_DETERMINATION_BUSINESS_DAYS)
    assert d.ack_deadline != naive_calendar
    assert d.ack_deadline == add_business_days(_WED, 10)
    assert d.ack_deadline.weekday() < 5, "a statutory deadline cannot fall on a weekend"
    assert (d.ack_deadline - _WED).days == 14  # 10 business days spans two weekends


def test_reg_e_has_a_provisional_credit_clock_at_the_determination_deadline():
    d = compute_deadlines("REG_E", _WED)
    assert d.provisional_credit_deadline == d.ack_deadline


def test_add_business_days_skips_weekends():
    friday = datetime(2026, 3, 6, tzinfo=timezone.utc)
    assert add_business_days(friday, 1).weekday() == 0  # -> Monday
    assert add_business_days(friday, 5).weekday() == 4  # -> next Friday


def test_merchant_window_is_20_days_for_both_regimes():
    """Amex's Central Site Business Date window is a network rule, not a
    statutory one, so it does not vary by regulation."""
    for regime in ("REG_Z", "REG_E"):
        d = compute_deadlines(regime, _WED)
        assert (d.merchant_response_deadline - _WED).days == MERCHANT_RESPONSE_DAYS == 20


def test_unknown_regime_falls_back_to_reg_z():
    """Fail closed toward the longer, more protective clock rather than
    raising on a value the ledger might legitimately grow later."""
    d = compute_deadlines("SOMETHING_ELSE", _WED)
    assert d.reg_regime == "REG_Z"
    assert (d.ack_deadline - _WED).days == 30


def test_deadlines_serialise_for_the_api_response():
    d = compute_deadlines("REG_E", _WED)
    out = d.to_dict()
    assert set(out) == {"reg_regime", "ack_by", "resolve_by", "merchant_response_by", "provisional_credit_by"}
    assert out["provisional_credit_by"] is not None


@pytest.mark.parametrize("regime", ["REG_Z", "REG_E"])
def test_every_deadline_is_in_the_future_and_ordered(regime):
    d = compute_deadlines(regime, _WED)
    assert d.ack_deadline > _WED
    assert d.resolve_deadline > d.ack_deadline, (
        "resolution cannot be due before acknowledgment -- an ordering inversion "
        "here would make the sweeper fire the breach before the ack"
    )
