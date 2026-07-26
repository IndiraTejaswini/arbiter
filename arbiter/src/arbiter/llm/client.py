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
        return json.loads(resp.json()["response"])
    except (httpx.HTTPError, httpx.TimeoutException, KeyError, ValueError, json.JSONDecodeError):
        return None


def is_available(model: Optional[str] = None) -> bool:
    settings = get_settings()
    target = (model or settings.vlm_model).split(":")[0]
    try:
        resp = httpx.get(f"{settings.ollama_base_url}/api/tags", timeout=2.0)
        if resp.status_code != 200:
            return False
        names = [m["name"] for m in resp.json().get("models", [])]
        return any(target in name for name in names)
    except (httpx.HTTPError, httpx.TimeoutException, KeyError, ValueError):
        return False
