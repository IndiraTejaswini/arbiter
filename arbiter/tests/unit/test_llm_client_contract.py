"""
The shared LLM chokepoint must never raise.

CLAUDE.md invariant #11 is explicit: every LLM call site returns None on any
failure and callers fall back to the deterministic default. Callers therefore
have NO branch for an exception -- `run_llm_advocate`, `classify_intent` and
`extract_vlm` all treat None as a routing signal. A raise from here does not
degrade the pipeline, it takes the adjudication down.

The handler used to enumerate exception types, which meant the contract held
only for the failures somebody had thought of. Two escaped:

  - `json.loads` raises TypeError, not ValueError, on a non-str argument, so
    a `{"response": null}` body propagated out of the "never raises" function
  - a malformed base URL raises `httpx.InvalidURL`, which is not an
    `httpx.HTTPError`
"""
import json

import httpx
import pytest

from arbiter.llm import client as llm


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


@pytest.mark.parametrize("payload", [
    {"response": None},                 # TypeError from json.loads -- the escapee
    {"response": 42},
    {"response": {"already": "parsed"}},
    {"response": "[1, 2, 3]"},          # valid JSON, wrong shape for a dict return
    {"response": '"a bare string"'},
    {"response": "not json at all"},
    {},                                 # missing key
    [],                                 # body is not an object
    "a string body",
    ValueError("body was not json"),
])
def test_complete_json_returns_none_never_raises(monkeypatch, payload):
    monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: _Resp(payload))
    assert llm.complete_json("prompt", {"type": "object"}) is None


def test_complete_json_returns_a_dict_on_the_happy_path(monkeypatch):
    monkeypatch.setattr(
        llm.httpx, "post", lambda *a, **k: _Resp({"response": json.dumps({"reason_code": "C08"})})
    )
    assert llm.complete_json("prompt", {"type": "object"}) == {"reason_code": "C08"}


@pytest.mark.parametrize("exc", [
    httpx.InvalidURL("bad url"),        # NOT an httpx.HTTPError -- the other escapee
    httpx.ConnectError("refused"),
    RuntimeError("something unforeseen"),
])
def test_transport_failures_return_none(monkeypatch, exc):
    def _raise(*a, **k):
        raise exc
    monkeypatch.setattr(llm.httpx, "post", _raise)
    assert llm.complete_json("prompt", {"type": "object"}) is None


@pytest.mark.parametrize("payload", [{"models": [{"name": "qwen2.5vl:7b"}]}])
def test_is_available_happy_path(monkeypatch, payload):
    monkeypatch.setattr(llm.httpx, "get", lambda *a, **k: _Resp(payload))
    assert llm.is_available("qwen2.5vl:7b") is True


@pytest.mark.parametrize("payload", [
    {"models": ["not-a-dict"]},
    {"models": [{}]},
    [],
    "string body",
    ValueError("nope"),
])
def test_is_available_never_raises(monkeypatch, payload):
    monkeypatch.setattr(llm.httpx, "get", lambda *a, **k: _Resp(payload))
    assert llm.is_available("qwen2.5vl:7b") is False
