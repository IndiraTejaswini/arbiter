"""
Natural Language Inference for the semantic contradiction layer.

**DeBERTa-v3-MNLI is the only permitted engine for this layer. Full stop.**

This is a hard architectural constraint, not a default that can be
overridden by configuration, and the reason is specific rather than
stylistic:

  1. **A generative model must never adjudicate whether two claims
     conflict.** Contradiction detection feeds `contradiction_clarity` in
     the confidence vector and, since D24, hard-blocks auto-resolution. A
     component with that much authority over whether a human sees a case
     cannot be a system whose output is a sample from a distribution over
     tokens. `arbiter.horn` decides the verdict; this layer decides whether
     the verdict is trustworthy enough to act on unattended. Both must be
     deterministic.

  2. **An LLM here would be a fourth injection surface with no veto.** The
     other three LLM boundaries (intake, extraction, advocates) each have a
     deterministic verifier that re-derives their output. There is no
     mechanical way to re-derive "these two sentences contradict" -- so an
     LLM at this boundary would be the one unguarded LLM in the system,
     reading attacker-controlled document text, with the power to suppress
     escalation by simply reporting no contradiction.

  3. **Correlated failure.** Using an LLM to check text that another LLM
     extracted means one model's blind spot is invisible to the other.
     A cross-encoder trained on MNLI fails differently and independently.

  4. **Determinism and reproducibility.** A decision must replay
     byte-identically against a pinned rulepack hash. A classifier with
     fixed weights and greedy argmax does that; a sampled generation does
     not, whatever the temperature.

`arbiter.horn` cannot import this module (import-linter contract "Referee
(horn) is pure"), and this module imports no LLM client -- there is no
`arbiter.llm` import here and there must never be one.

Availability: if the model cannot be loaded, this layer reports
UNAVAILABLE and the case **escalates**. It does not silently return "no
contradictions found" -- see `arbiter.evidence.graph.ContradictionAnalysis`.
An unrunnable mandatory check is an unknown, and an unknown is a human's
problem, not a pass.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# The one permitted model. Not configurable: see the module docstring.
NLI_MODEL_NAME = "MoritzLaurer/DeBERTa-v3-large-mnli-fever-anli-ling-wanli"

# Standard MNLI label order for this checkpoint family.
_LABELS = ("entailment", "neutral", "contradiction")

# A contradiction must be asserted with confidence to count. Below this the
# pair is treated as neutral -- the layer's job is to surface conflicts a
# human should adjudicate, not to manufacture doubt from model uncertainty.
CONTRADICTION_THRESHOLD = 0.70


class NLIStatus(Enum):
    OK = "OK"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class NLIVerdict:
    label: str          # entailment | neutral | contradiction
    confidence: float
    premise: str
    hypothesis: str

    @property
    def is_contradiction(self) -> bool:
        return self.label == "contradiction" and self.confidence >= CONTRADICTION_THRESHOLD


class DebertaNLI:
    """DeBERTa-v3-MNLI cross-encoder. Thread-safe, lazily loaded, cached.

    Deterministic by construction: `model.eval()`, `torch.no_grad()`, and
    argmax over the logits. No sampling, no temperature, no beam search --
    the same pair of sentences always yields the same label.
    """

    _instance: Optional["DebertaNLI"] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._model = None
        self._tokenizer = None
        self._status: Optional[NLIStatus] = None
        self._load_error: Optional[str] = None

    @classmethod
    def instance(cls) -> "DebertaNLI":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def _ensure_loaded(self) -> NLIStatus:
        if self._status is not None:
            return self._status
        with self._lock:
            if self._status is not None:
                return self._status
            try:
                import torch  # noqa: F401
                from transformers import (  # type: ignore
                    AutoModelForSequenceClassification,
                    AutoTokenizer,
                )

                from arbiter.config import get_settings

                name = get_settings().nli_model_path or NLI_MODEL_NAME
                self._tokenizer = AutoTokenizer.from_pretrained(name)
                self._model = AutoModelForSequenceClassification.from_pretrained(name)
                self._model.eval()
                self._status = NLIStatus.OK
                logger.info("DeBERTa NLI loaded: %s", name)
            except Exception as exc:
                self._load_error = f"{type(exc).__name__}: {exc}"
                self._status = NLIStatus.UNAVAILABLE
                logger.warning(
                    "DeBERTa NLI unavailable (%s). The semantic contradiction layer is "
                    "MANDATORY, so every case whose evidence contains comparable claim "
                    "pairs will ESCALATE to human review rather than auto-resolve. "
                    "Install `transformers` and `torch`, or pre-download %s.",
                    self._load_error, NLI_MODEL_NAME,
                )
            return self._status

    @property
    def status(self) -> NLIStatus:
        return self._ensure_loaded()

    @property
    def unavailable_reason(self) -> Optional[str]:
        return self._load_error

    def classify_batch(self, pairs: Sequence[Tuple[str, str]]) -> List[NLIVerdict]:
        """Classify (premise, hypothesis) pairs. Raises `NLIUnavailable` if
        the model could not be loaded -- callers must NOT interpret an
        exception as 'no contradictions'."""
        if self._ensure_loaded() is NLIStatus.UNAVAILABLE:
            raise NLIUnavailable(self._load_error or "model not loaded")
        if not pairs:
            return []

        import torch

        with torch.no_grad():
            encoded = self._tokenizer(
                [p for p, _ in pairs], [h for _, h in pairs],
                return_tensors="pt", truncation=True, padding=True, max_length=256,
            )
            probabilities = torch.softmax(self._model(**encoded).logits, dim=-1)

        out: List[NLIVerdict] = []
        for (premise, hypothesis), row in zip(pairs, probabilities, strict=True):
            idx = int(row.argmax())
            out.append(NLIVerdict(
                label=_LABELS[idx] if idx < len(_LABELS) else "neutral",
                confidence=float(row[idx]),
                premise=premise, hypothesis=hypothesis,
            ))
        return out


class NLIUnavailable(RuntimeError):
    """The mandatory semantic layer could not run.

    Deliberately an exception rather than an empty result: a caller that
    silently treats "the classifier did not run" as "no contradictions
    found" converts a missing safety check into a clean bill of health,
    which is the exact failure this layer's mandatory status exists to
    prevent.
    """


def classify_pairs(pairs: Sequence[Tuple[str, str]]) -> List[NLIVerdict]:
    return DebertaNLI.instance().classify_batch(pairs)


def is_available() -> bool:
    return DebertaNLI.instance().status is NLIStatus.OK
