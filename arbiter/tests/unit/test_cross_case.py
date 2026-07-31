"""
Unit coverage for arbiter.fairness.cross_case: population-level findings
that must never become predicates. The mechanical guarantee (arbiter.horn
cannot import this module -- pyproject.toml import-linter) is checked by
CI/`lint-imports`, not by a unit test; these tests cover the detection
logic itself.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from arbiter.fairness.cross_case import CaseFingerprint, find_device_rings, find_template_reuse, signals_for_case


def test_device_shared_across_enough_distinct_card_members_flagged():
    fingerprints = [
        CaseFingerprint(case_id=f"c{i}", card_member_id=f"cm{i}", merchant_id="m1", device_ids=("dev-X",))
        for i in range(4)
    ]
    signals = find_device_rings(fingerprints, min_distinct_card_members=3)

    assert len(signals) == 1
    assert signals[0].kind == "DEVICE_FINGERPRINT_RING"
    assert signals[0].key == "dev-X"
    assert signals[0].distinct_card_members == 4
    assert set(signals[0].case_ids) == {"c0", "c1", "c2", "c3"}


def test_device_shared_by_one_card_members_own_repeat_transactions_not_flagged():
    """The exact case this module must NOT report: the same card member's
    device recurring across their own prior transactions is a legitimate,
    single-case F29 signal already handled elsewhere -- not a ring."""
    fingerprints = [
        CaseFingerprint(case_id=f"c{i}", card_member_id="cm-same", merchant_id="m1", device_ids=("dev-X",))
        for i in range(5)
    ]
    signals = find_device_rings(fingerprints, min_distinct_card_members=3)
    assert signals == []


def test_device_below_threshold_not_flagged():
    fingerprints = [
        CaseFingerprint(case_id=f"c{i}", card_member_id=f"cm{i}", merchant_id="m1", device_ids=("dev-X",))
        for i in range(2)
    ]
    assert find_device_rings(fingerprints, min_distinct_card_members=3) == []


def test_template_reuse_scoped_to_merchant():
    fingerprints = [
        CaseFingerprint(case_id=f"a{i}", card_member_id=f"cm{i}", merchant_id="merchant-A", perceptual_hashes=("hash-1",))
        for i in range(3)
    ] + [
        CaseFingerprint(case_id="b0", card_member_id="cm9", merchant_id="merchant-B", perceptual_hashes=("hash-1",)),
    ]
    signals = find_template_reuse(fingerprints, min_cases=3)

    assert len(signals) == 1
    assert signals[0].key == "merchant-A:hash-1"
    assert set(signals[0].case_ids) == {"a0", "a1", "a2"}
    assert "b0" not in signals[0].case_ids  # different merchant, same hash -- not pooled together


def test_signals_for_case_filters_to_relevant_case_only():
    fingerprints = [
        CaseFingerprint(case_id=f"c{i}", card_member_id=f"cm{i}", merchant_id="m1", device_ids=("dev-X",))
        for i in range(4)
    ]
    all_signals = find_device_rings(fingerprints, min_distinct_card_members=3)

    assert len(signals_for_case("c0", all_signals)) == 1
    assert signals_for_case("not-a-case", all_signals) == []


def test_severity_scales_with_distinct_card_members():
    small = find_device_rings(
        [CaseFingerprint(f"c{i}", f"cm{i}", "m1", device_ids=("dev-Y",)) for i in range(3)],
        min_distinct_card_members=3,
    )
    large = find_device_rings(
        [CaseFingerprint(f"c{i}", f"cm{i}", "m1", device_ids=("dev-Z",)) for i in range(12)],
        min_distinct_card_members=3,
    )
    assert small[0].severity in ("LOW", "MEDIUM")
    assert large[0].severity == "HIGH"
