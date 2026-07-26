"""Shared exception types raised across service boundaries."""

from __future__ import annotations


class ArbiterError(Exception):
    """Base class for all ARBITER domain errors."""


class ValidationError(ArbiterError):
    """A request or artifact failed a boundary check (size, type, schema)."""


class QuarantineViolation(ArbiterError):
    """Something tried to cross the ingest quarantine boundary illegally."""


class RulepackError(ArbiterError):
    """A rulepack failed to load or validate."""


class NotFoundError(ArbiterError):
    """A referenced entity (case, artifact, commitment) does not exist."""
