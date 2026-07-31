"""
Suite-wide test isolation.

`arbiter.config.Settings` reads a `.env` file (see `.env.example`), which is
what lets a host-run stack -- `uvicorn arbiter.main:app` in one terminal and
`scripts/run_adjudication_worker.py` in another -- share the audit signing
key the way docker-compose.yml already makes the api/worker/clock services
share it. Without that, each process generates its own ephemeral Ed25519 key
and every event one process signs is unverifiable by the other.

That file must never reach the tests. Several of them -- notably
`tests/unit/test_api_security.py` -- assert on what the CODE defaults to:
that `enable_dev_auth` is off, that `validate_for_environment` refuses to
boot on the built-in HMAC secret, that an ephemeral signing key is rejected
outside `env=dev`. Every one of those defaults is a past security defect,
and a developer who copies `.env.example` to `.env` supplies exactly the
values that make the assertions vacuous. The suite would then pass or fail
on whether someone had set up local development, which is precisely the
"secured by a deletion reminder" failure mode those tests exist to prevent.

Environment VARIABLES are deliberately still honoured, so tests that opt in
with `monkeypatch.setenv` keep working. Only the on-disk file is ignored.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True, scope="session")
def _ignore_developer_dotenv() -> None:
    from arbiter.config import Settings, get_settings

    previous = Settings.model_config.get("env_file")
    Settings.model_config["env_file"] = None
    get_settings.cache_clear()
    try:
        yield
    finally:
        Settings.model_config["env_file"] = previous
        get_settings.cache_clear()


# Every module that calls out to a model, and the name each one holds the
# callable under. Patched at the CONSUMER, never at `arbiter.llm.client`
# itself, so `tests/unit/test_llm_client_contract.py` still exercises the
# real client.
_LLM_CALL_SITES = (
    "arbiter.narrate.llm",
    "arbiter.intake.classify",
    "arbiter.advocate.llm_runner",
)


@pytest.fixture(autouse=True)
def _no_live_model_calls(monkeypatch):
    """No test reaches a live model unless it explicitly asks to.

    Without this the suite silently depends on whether Ollama happens to be
    running on the machine executing it. Wiring up narration made that
    concrete and measurable: `test_narration_fallback_on_corruption` went
    from milliseconds to 25 SECONDS, and -- worse than slow -- its
    assertion that a clean case renders `source == "template"` became a
    statement about what a 7B model happened to return that afternoon,
    rather than about the code.

    Deterministic default: every call site behaves exactly as it does when
    the model is unreachable, which is the path CLAUDE.md invariant #11
    requires to work anyway. Tests that want model output monkeypatch these
    same names themselves; because this fixture is function-scoped and runs
    first, the test's own patch wins.
    """
    for module in _LLM_CALL_SITES:
        monkeypatch.setattr(f"{module}.complete_json", lambda *a, **k: None, raising=False)
        monkeypatch.setattr(f"{module}.is_available", lambda *a, **k: False, raising=False)
