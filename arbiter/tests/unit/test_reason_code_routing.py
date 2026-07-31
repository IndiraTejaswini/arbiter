"""Reason-code routing across the two dialects the same dispute has.

ARBITER's rulepacks are keyed by Amex's US-style codes (F29, C08, C02). The
published Amex chargeback guide -- and an Australian merchant's own "Resolve
Disputes" screen -- use four-digit network codes (4540, 4554, 4513) for the
same disputes. Accepting only one dialect meant the one code a real caller
actually holds could not be used to file.

Routing fails closed, for the reason CLAUDE.md invariant #6 gives: the wrong
rulepack means the wrong predicates and the wrong evidence requirements
entirely, so an unrecognised code raises rather than defaulting.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from arbiter.core.errors import RulepackError
from arbiter.rulepack import RulepackRegistry

RULEPACK_DIR = Path(__file__).resolve().parent.parent.parent / "rulepacks" / "amex"


@pytest.fixture(scope="module")
def registry() -> RulepackRegistry:
    reg = RulepackRegistry()
    reg.load_dir(RULEPACK_DIR)
    return reg


@pytest.mark.parametrize(
    "network_code,reason_code",
    [
        # Amex guide, contents page: 4540 Card Not Present (p.17), 4554 Goods
        # And Services Not Received (p.23), 4513 Credit Not Presented (p.6).
        ("4540", "F29"),
        ("4554", "C08"),
        ("4513", "C02"),
    ],
)
def test_network_code_resolves_to_the_internal_reason_code(registry, network_code, reason_code):
    assert registry.resolve(network_code) == reason_code
    assert registry.latest(network_code).reason_code == reason_code


@pytest.mark.parametrize("reason_code", ["F29", "C08", "C02"])
def test_internal_reason_code_still_resolves_to_itself(registry, reason_code):
    assert registry.resolve(reason_code) == reason_code


def test_unknown_code_fails_closed_and_says_what_it_knows(registry):
    """The error is the routing surface's documentation: a caller holding a
    code from a page of the guide ARBITER has not transcribed yet needs to
    see that, not a generic 404."""
    with pytest.raises(RulepackError) as raised:
        registry.resolve("4507")  # Incorrect Transaction Amount -- real code, no rulepack
    message = str(raised.value)
    assert "4507" in message
    assert "4540" in message and "F29" in message


def test_registry_exposes_the_network_code_mapping(registry):
    assert registry.network_codes() == {"4540": "F29", "4554": "C08", "4513": "C02"}
