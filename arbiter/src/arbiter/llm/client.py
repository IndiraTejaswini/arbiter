"""
Shared local-LLM client: one place `arbiter.intake` and `arbiter.advocate`'s
LLM-backed path call through, so the "outbound call" boundary is a single
audited chokepoint instead of duplicated per caller.

Resource-constrained engineering choice, stated honestly: this build runs
everything -- extraction (`arbiter.ingest.extract_vlm`), intent
classification, and advocate search -- through the SAME already-pulled
`qwen2.5vl:7b` model via Ollama, rather than pulling a second multi-GB text
model. On an 8GB-VRAM laptop GPU that is the difference between "runs" and
"doesn't fit." Qwen2.5-VL is a full instruction-tuned causal LM with a
vision head bolted on, not a vision-only model -- calling it with no
`images` key is a completely ordinary text completion, verified in this
build's own test suite (see `evals/hallucination.py`'s module docstring).
Swapping in a larger/cloud model for either role is a one-line change to
`arbiter.config.Settings` (`vlm_model`, or a new `advocate_model` /
`intake_model` setting) -- nothing about the call shape here changes.

Every caller of `complete_json` gets back parsed JSON or `None`. Never an
exception that could propagate into a code path expecting a hard failure to
mean something specific -- "the model didn't answer usefully" is always
treated as a routing signal (fall back / escalate / demote), consistent with
CLAUDE.md #9's "degrade, never reject" applied to LLM availability generally.
"""

from __future__ import annotations

import json
from typing import Any, Optional

import httpx

from arbiter.config import get_settings


def complete_json(
    prompt: str,
    schema: dict[str, Any],
    model: Optional[str] = None,
    temperature: float = 0.0,
    timeout: float = 60.0,
) -> Optional[dict[str, Any]]:
    """Constrained-decoding text completion: Ollama's `format` parameter
    forces the response to validate against `schema` before it's even
    returned to us. Returns the parsed dict, or None on any failure
    (unreachable, timeout, malformed response) -- callers must treat None
    exactly like a rejected/low-confidence result, never crash on it."""
    settings = get_settings()
    payload = {
        "model": model or settings.vlm_model,
        "prompt": prompt,
        "format": schema,
        "stream": False,
        "options": {"temperature": temperature},
    }
    try:
        resp = httpx.post(f"{settings.ollama_base_url}/api/generate", json=payload, timeout=timeout)
        resp.raise_for_status()
        body = resp.json()
        if not isinstance(body, dict):
            return None
        completion = body.get("response")
        # `json.loads` raises TypeError -- not ValueError -- on a non-str
        # argument, so `{"response": null}` or a response field that already
        # arrived as an object used to escape the handler below and crash the
        # caller. That is a direct breach of this module's own contract and
        # of CLAUDE.md #11: every LLM call site returns None on ANY failure,
        # because callers treat None as a routing signal and have no branch
        # for an exception.
        if not isinstance(completion, str):
            return None
        parsed = json.loads(completion)
        # Constrained decoding should guarantee an object, but the return
        # type says `dict` and a bare list or string would satisfy `json.loads`
        # while breaking every caller's `.get(...)`. Enforce the annotation
        # rather than trusting the server to honour the schema.
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        # Deliberately total. A malformed base URL raises `httpx.InvalidURL`,
        # which is not an `HTTPError`; a proxy or DNS misconfiguration can
        # surface as something else again. Enumerating exception types at a
        # boundary whose entire contract is "never raises" means the contract
        # holds only for the failures somebody thought of.
        return None


def is_available(model: Optional[str] = None) -> bool:
    settings = get_settings()
    target = (model or settings.vlm_model).split(":")[0]
    try:
        resp = httpx.get(f"{settings.ollama_base_url}/api/tags", timeout=2.0)
        if resp.status_code != 200:
            return False
        body = resp.json()
        if not isinstance(body, dict):
            return False
        names = [
            m.get("name", "") for m in body.get("models", []) if isinstance(m, dict)
        ]
        return any(target in name for name in names)
    except Exception:
        # Same reasoning as `complete_json`: an availability probe that can
        # itself raise is worse than one that reports "unavailable".
        return False
