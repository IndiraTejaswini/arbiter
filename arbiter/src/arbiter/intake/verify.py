"""
Deterministic veto over the intent classifier (CLAUDE.md: intent classifier
selects the rulepack; it never influences the outcome, and never on a
guess). Misrouting is the one intake error that corrupts everything
downstream -- the wrong rulepack means the wrong predicates, the wrong
rules, the wrong evidence requirements entirely -- so this boundary fails
closed harder than most.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .classify import IntentResult

CONFIDENCE_THRESHOLD = 0.70


@dataclass(frozen=True)
class IntentDecision:
    resolved: bool
    reason_code: Optional[str]  # set only if resolved
    needs_user_confirmation: bool
    route_to_human_triage: bool
    reason: str


def verify_intent(result: Optional[IntentResult], available_rulepacks: set[str]) -> IntentDecision:
    """
    1. reason_code must map to a loaded rulepack        else route to human triage
    2. confidence < CONFIDENCE_THRESHOLD                else ask the user to confirm
    3. reason_code == "OTHER"                            route to human triage
    4. classifier unavailable / unparseable (result=None) route to human triage

    Never silently guesses a rulepack -- every non-resolved path is either a
    one-tap user confirmation or a human-triage queue entry, logged either
    way (the API layer writes an INTENT_CLASSIFIED case_event regardless of
    outcome).
    """
    if result is None:
        return IntentDecision(
            resolved=False, reason_code=None, needs_user_confirmation=False, route_to_human_triage=True,
            reason="intent classifier unavailable or returned unparseable output",
        )

    if result.reason_code == "OTHER":
        return IntentDecision(
            resolved=False, reason_code=None, needs_user_confirmation=False, route_to_human_triage=True,
            reason="classifier could not confidently match any known bucket",
        )

    if result.reason_code not in available_rulepacks:
        return IntentDecision(
            resolved=False, reason_code=None, needs_user_confirmation=False, route_to_human_triage=True,
            reason=f"classifier proposed {result.reason_code!r}, which has no loaded rulepack",
        )

    if result.confidence < CONFIDENCE_THRESHOLD:
        return IntentDecision(
            resolved=False, reason_code=result.reason_code, needs_user_confirmation=True,
            route_to_human_triage=False,
            reason=f"confidence {result.confidence:.2f} below threshold {CONFIDENCE_THRESHOLD} -- ask the user to confirm {result.reason_code}",
        )

    return IntentDecision(
        resolved=True, reason_code=result.reason_code, needs_user_confirmation=False,
        route_to_human_triage=False,
        reason=f"confidence {result.confidence:.2f} >= threshold, signals={list(result.signals)}",
    )
