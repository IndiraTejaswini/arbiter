"""
Route-level authorization tests.

These exist because their absence is *why* the audited defects shipped.
There were no API tests at all -- no `TestClient` anywhere in the suite --
so four routers reached a build with no authentication on them and nobody
noticed. Every test here fails loudly against the pre-fix code.

The suite deliberately asserts on the DEFAULT configuration, because each
defect was a default: the dev-token route was on by default, and the
commitment/fairness/rulepack routers had no dependency at all.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from arbiter.auth import Actor, Role
from arbiter.auth.tokens import issue_token
from arbiter.config import ConfigurationError, Settings, validate_for_environment


class _NoRowsSession:
    """Stand-in Session that finds nothing.

    These tests assert on the AUTHORIZATION boundary, which must reject
    before any handler reads a row -- so the session only needs to exist,
    not to work. Stubbing it also keeps the suite runnable without
    Postgres, which matters: a security test that only runs when
    infrastructure is up is a security test that stops running.
    """

    def get(self, *_args, **_kwargs):
        return None

    def execute(self, *_args, **_kwargs):
        raise AssertionError(
            "a handler queried the database on a request that should have been "
            "rejected by the authorization layer first"
        )

    def close(self):
        pass


@pytest.fixture
def client():
    """A client that does NOT run lifespan: these tests exercise the
    authorization boundary, which must reject before any handler touches a
    database or a rulepack."""
    from arbiter.db.session import get_session
    from arbiter.main import app

    app.dependency_overrides[get_session] = lambda: _NoRowsSession()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _token(role: Role, bound_id: str | None = None) -> str:
    from arbiter.config import get_settings

    return issue_token(
        Actor(actor_id="test", role=role, bound_id=bound_id), get_settings().auth_secret
    )


def _auth(role: Role, bound_id: str | None = None) -> dict:
    return {"Authorization": f"Bearer {_token(role, bound_id)}"}


# -- The dev-token route --------------------------------------------------


def test_dev_token_route_is_off_by_default(client):
    """The single worst finding: an unauthenticated caller could POST
    {"role": "ADMIN"} and receive full administrative authority over every
    case. It is now gated behind ARBITER_ENABLE_DEV_AUTH, which defaults to
    false, and returns 404 (not 403) so a disabled route is indistinguishable
    from one that does not exist."""
    resp = client.post("/v1/auth/dev-token", json={"role": "ADMIN"})
    assert resp.status_code == 404, (
        "POST /v1/auth/dev-token must be disabled by default -- it mints ADMIN "
        "tokens with no authentication behind them"
    )


def test_dev_token_route_works_when_explicitly_enabled(client, monkeypatch):
    """The gate must be a real switch, not a permanent disablement -- the
    frontend and demo scripts depend on it in development."""
    from arbiter.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("ARBITER_ENABLE_DEV_AUTH", "true")
    try:
        resp = client.post("/v1/auth/dev-token", json={"role": "REVIEWER"})
        assert resp.status_code == 200
        assert resp.json()["token"]
    finally:
        get_settings.cache_clear()


# -- Startup configuration guards -----------------------------------------


def test_startup_refuses_default_auth_secret_outside_dev():
    """The documented docker-compose path set no ARBITER_AUTH_SECRET, so the
    'full stack' ran with a publicly known HMAC key -- every bearer token
    forgeable by anyone who had read the source."""
    settings = Settings(env="prod")
    with pytest.raises(ConfigurationError) as exc:
        validate_for_environment(settings)
    assert "ARBITER_AUTH_SECRET" in str(exc.value)


def test_startup_refuses_dev_auth_outside_dev():
    settings = Settings(
        env="prod", auth_secret="x" * 40, enable_dev_auth=True, signing_key_seed="ab" * 32
    )
    with pytest.raises(ConfigurationError) as exc:
        validate_for_environment(settings)
    assert "ARBITER_ENABLE_DEV_AUTH" in str(exc.value)


def test_startup_refuses_ephemeral_signing_key_outside_dev():
    settings = Settings(env="prod", auth_secret="x" * 40)
    with pytest.raises(ConfigurationError) as exc:
        validate_for_environment(settings)
    assert "SIGNING_KEY" in str(exc.value)


def test_startup_allows_a_fully_configured_deployment():
    validate_for_environment(
        Settings(env="prod", auth_secret="x" * 40, signing_key_seed="ab" * 32)
    )


def test_dev_environment_is_not_gated():
    """Guards must not make local development impossible -- that is how they
    end up disabled wholesale."""
    validate_for_environment(Settings(env="dev"))


# -- ADEC commitment routes -----------------------------------------------

_MERCHANT_A = str(uuid.uuid4())
_MERCHANT_B = str(uuid.uuid4())


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("post", "/v1/commitments", {"artifact_type": "shipment", "commitment_hash": "00" * 32,
                                     "event_time": "2026-01-01T00:00:00Z"}),
        ("post", "/v1/commitments/abc/reveal", {"artifact_hex": "00", "salt_hex": "00"}),
        ("get", "/v1/commitments/abc/proof", None),
        ("get", "/v1/log/sth", None),
    ],
)
def test_commitment_routes_require_authentication(client, method, path, body):
    """Every route in this module was unauthenticated, and `merchant_id`
    came from the request body -- so anyone could forge ADEC commitments for
    any merchant, which voids the one property the scheme exists to provide."""
    resp = getattr(client, method)(path, json=body) if body else getattr(client, method)(path)
    assert resp.status_code == 401, f"{method.upper()} {path} must require authentication"


def test_card_member_cannot_create_commitments(client):
    """ADEC is a merchant-side integration; a card member has no business
    writing to the log at all."""
    resp = client.post(
        "/v1/commitments",
        json={"merchant_id": _MERCHANT_A, "artifact_type": "shipment",
              "commitment_hash": "00" * 32, "event_time": "2026-01-01T00:00:00Z"},
        headers=_auth(Role.CARD_MEMBER, str(uuid.uuid4())),
    )
    assert resp.status_code == 403


def test_merchant_cannot_commit_for_another_merchant(client):
    """The core forgery: merchant A claiming evidence on behalf of B."""
    resp = client.post(
        "/v1/commitments",
        json={"merchant_id": _MERCHANT_B, "artifact_type": "shipment",
              "commitment_hash": "00" * 32, "event_time": "2026-01-01T00:00:00Z"},
        headers=_auth(Role.MERCHANT, _MERCHANT_A),
    )
    assert resp.status_code == 403
    assert "own merchant_id" in resp.json()["detail"]


def test_commitment_hash_must_be_32_bytes(client):
    """A non-sha256 leaf in an append-only log is unverifiable forever."""
    resp = client.post(
        "/v1/commitments",
        json={"artifact_type": "shipment", "commitment_hash": "abcd",
              "event_time": "2026-01-01T00:00:00Z"},
        headers=_auth(Role.MERCHANT, _MERCHANT_A),
    )
    assert resp.status_code == 400
    assert "32 bytes" in resp.json()["detail"]


# -- Fairness and rulepack disclosure -------------------------------------


@pytest.mark.parametrize("path", ["/v1/fairness/rules", "/v1/fairness/rules/C08_R1"])
def test_fairness_routes_require_authentication(client, path):
    """Unauthenticated + unbounded three-way join: a trivial DoS and a leak
    of the whole decision corpus' fairness profile."""
    assert client.get(path).status_code == 401


@pytest.mark.parametrize("path", ["/v1/fairness/rules", "/v1/fairness/rules/C08_R1"])
def test_fairness_routes_reject_disputing_parties(client, path):
    resp = client.get(path, headers=_auth(Role.MERCHANT, _MERCHANT_A))
    assert resp.status_code == 403


def test_rulepack_disclosure_requires_reviewer(client):
    """Anonymous access to the complete decision function, combined with the
    counterfactual ledger, is the full toolkit for targeting a decision path
    with fabricated evidence."""
    assert client.get("/v1/rulepacks/deadbeef").status_code == 401
    resp = client.get("/v1/rulepacks/deadbeef", headers=_auth(Role.MERCHANT, _MERCHANT_A))
    assert resp.status_code == 403


# -- Case-scoped routes ----------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/v1/cases/00000000-0000-0000-0000-000000000001",
        "/v1/cases/00000000-0000-0000-0000-000000000001/decision",
        "/v1/cases/00000000-0000-0000-0000-000000000001/graph",
        "/v1/cases/00000000-0000-0000-0000-000000000001/timeline",
        "/v1/cases/00000000-0000-0000-0000-000000000001/artifacts",
        "/v1/cases/00000000-0000-0000-0000-000000000001/stream",
        "/v1/audit/00000000-0000-0000-0000-000000000001",
    ],
)
def test_case_scoped_routes_require_authentication(client, path):
    assert client.get(path).status_code == 401


def test_stream_accepts_a_query_parameter_token(client):
    """EventSource cannot set headers, so the stream route accepts
    ?access_token=. Without this, real-time tracking is unreachable from a
    browser -- adding auth to the route silently broke a headline feature.
    A 401 here would mean the query-token path was not wired; anything else
    means the token was accepted and the request proceeded to the handler."""
    token = _token(Role.REVIEWER)
    resp = client.get(
        f"/v1/cases/00000000-0000-0000-0000-000000000001/stream?access_token={token}"
    )
    assert resp.status_code != 401


def test_stream_rejects_a_bad_query_parameter_token(client):
    resp = client.get(
        "/v1/cases/00000000-0000-0000-0000-000000000001/stream?access_token=not-a-token"
    )
    assert resp.status_code == 401
