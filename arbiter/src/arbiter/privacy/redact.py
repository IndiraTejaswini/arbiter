"""
PII redaction at the LLM-prompt boundary.

Every text string this build ever puts in front of a model -- a card
member's free-text complaint (arbiter.intake.classify), an evidence node's
extracted-field values summarized for an advocate (arbiter.advocate.
llm_runner) -- can carry structured PII: card numbers, SSNs, emails, phone
numbers. None of that is needed for intent classification or predicate
matching, and none of it should leave the process boundary in a prompt sent
to a model, local or hosted.

Stated honestly: this is a self-contained, dependency-free stand-in for
Presidio+spaCy (the build spec's own choice for this job), covering the
structured, regex-detectable PII classes that matter most for a payments
dispute. What it does NOT do is Presidio's real value-add -- spaCy NER for
free-text names, organizations, and locations embedded in prose. Swapping
in real Presidio is a drop-in replacement of `redact_text`'s body; nothing
that calls it (classify.py, llm_runner.py) needs to change. Consistent with
this build's other honestly-scoped stand-ins (arbiter.provenance.tsa's
"Simplified RFC 3161... stand-in", arbiter.evidence.semantic's NLI stub):
partial coverage that's true to its own limits beats a dependency this
environment may not have the models/network access to actually pull.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Tuple


@dataclass(frozen=True)
class RedactionMatch:
    kind: str  # CREDIT_CARD | SSN | EMAIL | PHONE
    span: Tuple[int, int]  # offsets into the ORIGINAL text


def _luhn_valid(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


# The trailing digit is unseparated on purpose: `(?:\d[ -]?)` alone would
# happily consume a separator *after* the last digit too (its class
# includes the space that follows), so a card number immediately before a
# word ("...1111 and") would swallow that space into the match. Ending on
# a bare `\d` keeps the match's right edge pinned to the last digit.
_CARD_CANDIDATE = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PHONE = re.compile(r"\b(?:\+?1[ -]?)?\(?\d{3}\)?[ -]?\d{3}[ -]?\d{4}\b")

_PLACEHOLDER = {"CREDIT_CARD": "[REDACTED_CARD]", "SSN": "[REDACTED_SSN]", "EMAIL": "[REDACTED_EMAIL]", "PHONE": "[REDACTED_PHONE]"}


def _find_card_numbers(text: str) -> List[Tuple[int, int]]:
    spans = []
    for m in _CARD_CANDIDATE.finditer(text):
        digits = re.sub(r"[ -]", "", m.group())
        if 13 <= len(digits) <= 19 and _luhn_valid(digits):
            spans.append(m.span())
    return spans


def redact_text(text: str) -> Tuple[str, List[RedactionMatch]]:
    """Returns (redacted_text, matches). Never raises; an empty/None-like
    input redacts to itself. Order of pattern application matters only in
    that a span already claimed by an earlier pattern isn't re-matched by
    a later, looser one (SSN/phone digit runs can otherwise overlap a
    Luhn-valid card-number candidate)."""
    if not text:
        return text, []

    claimed: List[Tuple[int, int, str]] = []
    for kind, spans in (
        ("CREDIT_CARD", _find_card_numbers(text)),
        ("SSN", [m.span() for m in _SSN.finditer(text)]),
        ("EMAIL", [m.span() for m in _EMAIL.finditer(text)]),
        ("PHONE", [m.span() for m in _PHONE.finditer(text)]),
    ):
        for start, end in spans:
            if any(not (end <= cs or start >= ce) for cs, ce, _ in claimed):
                continue  # overlaps a higher-priority match already claimed
            claimed.append((start, end, kind))

    if not claimed:
        return text, []

    claimed.sort(key=lambda t: t[0])
    out: List[str] = []
    cursor = 0
    matches: List[RedactionMatch] = []
    for start, end, kind in claimed:
        out.append(text[cursor:start])
        out.append(_PLACEHOLDER[kind])
        matches.append(RedactionMatch(kind=kind, span=(start, end)))
        cursor = end
    out.append(text[cursor:])
    return "".join(out), matches
