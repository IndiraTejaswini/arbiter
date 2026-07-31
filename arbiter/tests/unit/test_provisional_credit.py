"""
Unit coverage for arbiter.decision.provisional_credit: the Reg E (12 CFR
1005.11) axis, independent of the win/lose verdict. The property that
matters most here is the one a pure latency optimization would miss --
provisional credit is due specifically WHEN the case is unresolved
(abstained), not only when the card member wins outright.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from arbiter.decision.provisional_credit import compute_provisional_credit


def test_reg_z_never_triggers_this_axis():
    result = compute_provisional_credit("REG_Z", decision="CARD_MEMBER_WINS", abstained=False, conflicting=False)
    assert result.due is False


def test_reg_e_abstained_case_triggers_provisional_credit():
    """The core regulatory point: an unresolved investigation is exactly
    when Reg E requires provisional credit, not a decided-in-favor case."""
    result = compute_provisional_credit("REG_E", decision=None, abstained=True, conflicting=False)
    assert result.due is True
    assert "1005.11" in result.reason


def test_reg_e_conflicting_outcomes_triggers_provisional_credit():
    result = compute_provisional_credit("REG_E", decision=None, abstained=False, conflicting=True)
    assert result.due is True


def test_reg_e_card_member_wins_outright_triggers_credit():
    result = compute_provisional_credit("REG_E", decision="CARD_MEMBER_WINS", abstained=False, conflicting=False)
    assert result.due is True


def test_reg_e_split_outcome_triggers_credit():
    result = compute_provisional_credit("REG_E", decision="SPLIT", abstained=False, conflicting=False)
    assert result.due is True


def test_reg_e_merchant_wins_outright_no_credit():
    result = compute_provisional_credit("REG_E", decision="MERCHANT_WINS", abstained=False, conflicting=False)
    assert result.due is False
