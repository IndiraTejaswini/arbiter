"""
The console must not know which reason codes exist.

The defect: `F29` / `C08` / `C02` and their human descriptions were hardcoded
in three separate TypeScript files — the filing form, the case filter, and
the case-detail header, the last of which fell back to the literal word
"Dispute" for anything it did not recognise. Dropping a fourth rulepack into
`rulepacks/amex/` made it loadable, validatable, routable, and adjudicable by
the backend, and completely invisible in the product: no card member could
file under it, no reviewer could filter to it, and any case carrying it was
captioned with a generic noun.

That made "adding a reason code is a YAML file" — a claim this repository
makes in its README, its CLAUDE.md, and its architecture document — true of
the engine and false of the thing anyone actually uses.

There was also no route that enumerated loaded rulepacks at all. The only
read was `GET /v1/rulepacks/{content_hash}`, which needs a hash a caller can
only obtain from a decision that already pinned it, and which is
reviewer-only because it returns every rule body. `RulepackRegistry.
network_codes()` even carried a docstring saying it existed "for the API's
rulepack listing". There was no listing.

These tests pin the fix from both ends: the catalogue exists and is correct,
and the console does not re-acquire a hardcoded copy of it.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest

from arbiter.config import get_settings
from arbiter.rulepack.loader import load_rulepack_dir
from arbiter.rulepack.registry import RulepackRegistry

_WEB_SRC = Path(__file__).resolve().parents[2] / "web" / "src"


@pytest.fixture(scope="module")
def registry() -> RulepackRegistry:
    reg = RulepackRegistry()
    reg.load_dir(get_settings().rulepack_dir)
    return reg


# -- Catalogue metadata is data, and does not disturb the hash ------------


def test_every_shipped_rulepack_has_catalogue_metadata(registry):
    """A rulepack with no title renders as a bare code in the one screen
    where a card member is being asked what happened to them."""
    for code in registry.reason_codes():
        pack = registry.latest(code)
        assert pack.title, f"{code} has no title; the console would show only the code"
        assert pack.description, f"{code} has no card-member-facing description"
        # The description is what a card member picks from. A code in it means
        # the copy was written for the wrong reader.
        assert code not in pack.description, (
            f"{code}'s description repeats the reason code; it should read as a plain "
            f"sentence about what happened, since the code is already shown beside it"
        )


def test_catalogue_metadata_is_excluded_from_the_content_hash():
    """THE safety property. `title` and `description` are prose ABOUT the
    rules, exactly like `Rule.description` and `ChargebackRight.source`. If
    they entered `content_hash()`, editing a typo in a user-facing sentence
    would mint a new rulepack identity and orphan every decision pinned
    against the old one — and `decision` is append-only by trigger, so there
    is no correction path that could rewrite those rows."""
    from dataclasses import replace

    pack = load_rulepack_dir(get_settings().rulepack_dir)["C08"]
    original = pack.content_hash()

    # `replace` on a frozen dataclass builds a fresh instance, so the hash
    # memo does not carry over -- this genuinely recomputes.
    reworded = replace(pack, title="Something else entirely", description="Reworded copy.")
    assert reworded.content_hash() == original, (
        "editing user-facing prose changed the rulepack's content hash, which would "
        "orphan every decision pinned against it"
    )


def test_a_rulepack_without_metadata_still_loads():
    """Absence is not an error: a rulepack without these fields is still a
    complete decision function, and one the engine can adjudicate must never
    be one the catalogue refuses to list."""
    from arbiter.rulepack.loader import parse_rulepack

    pack = parse_rulepack({
        "rulepack_id": "test-X01-v1",
        "reason_code": "X01",
        "version": "1.0.0",
        "decision_predicates": {"MERCHANT_WINS": "merchant_wins"},
        "rules": [{"rule_id": "X01_R1", "head": "merchant_wins", "body": ["proof_supplied"]}],
    })
    assert pack.title == "" and pack.description == ""


# -- The catalogue endpoint ------------------------------------------------


@pytest.fixture
def catalogue():
    """Fetched as a CARD_MEMBER on purpose. A party choosing what to file
    needs this list, and the whole point of splitting it from the
    rule-bodies route is that this one is safe to give them."""
    from fastapi.testclient import TestClient

    from arbiter.api.deps import registry as app_registry
    from arbiter.auth import Actor, Role
    from arbiter.auth.deps import get_current_actor
    from arbiter.main import app

    app_registry.load_dir(get_settings().rulepack_dir)
    app.dependency_overrides[get_current_actor] = lambda: Actor(
        actor_id="cm", role=Role.CARD_MEMBER, bound_id=str(uuid.uuid4())
    )
    try:
        yield TestClient(app).get("/v1/rulepacks").json()
    finally:
        app.dependency_overrides.clear()


def test_catalogue_lists_every_loaded_reason_code(catalogue, registry):
    listed = {r["reason_code"] for r in catalogue["rulepacks"]}
    assert listed == set(registry.reason_codes())


def test_catalogue_carries_what_the_console_needs(catalogue):
    for entry in catalogue["rulepacks"]:
        assert entry["title"], "title falls back to the reason code server-side; never empty"
        assert entry["content_hash"], "needed to deep-link the full rulepack view"
        assert entry["rule_count"] > 0
        # Both dialects, so a client can file with whichever code its user
        # actually holds -- the four-digit form is what the published guide
        # and a merchant's own screen use.
        assert entry["network_code"], "the Amex four-digit code is what a merchant reads"


def test_catalogue_does_not_leak_the_decision_function(catalogue):
    """The reason this is a separate route from `GET /v1/rulepacks/{hash}`.
    That one is reviewer-only because rule bodies plus the counterfactual
    ledger are the complete toolkit for targeting a decision path with
    fabricated evidence. This one must stay safe to hand a party."""
    blob = repr(catalogue)
    for leaked in ("rules", "body", "predicate_schema", "exclusions", "when", "conditions"):
        assert f"'{leaked}'" not in blob, (
            f"the catalogue exposes {leaked!r}; it must carry counts and names only, never "
            f"the decision logic -- that stays behind the reviewer-only route"
        )
    for entry in catalogue["rulepacks"]:
        assert isinstance(entry["exclusion_count"], int)
        assert "exclusions" not in entry


def test_card_member_may_read_the_catalogue_but_not_a_rulepack(catalogue, registry):
    """Both halves of the split, asserted together — the catalogue is only
    defensible if the rule bodies stay closed."""
    from fastapi.testclient import TestClient

    from arbiter.auth import Actor, Role
    from arbiter.auth.deps import get_current_actor
    from arbiter.main import app

    assert catalogue["rulepacks"], "the card member could read the catalogue"

    content_hash = registry.latest(registry.reason_codes()[0]).content_hash()
    app.dependency_overrides[get_current_actor] = lambda: Actor(
        actor_id="cm", role=Role.CARD_MEMBER, bound_id=str(uuid.uuid4())
    )
    try:
        response = TestClient(app).get(f"/v1/rulepacks/{content_hash}")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 403, (
        "a party to a live dispute must not be able to read the rule bodies"
    )


# -- The console does not keep its own copy -------------------------------


def _console_sources() -> list[Path]:
    return [
        p for p in _WEB_SRC.rglob("*.ts*")
        if p.is_file() and "node_modules" not in p.parts
    ]


def _code_only(path: Path) -> str:
    """A console source with its comments removed.

    Every assertion below is about what the CODE does, and naming the old
    hardcoded value while explaining why it is gone is documentation, not a
    regression. Matching on raw text would make these tests fail on their own
    explanations and push those explanations out of the source — the opposite
    of what this repository wants.
    """
    source = path.read_text(encoding="utf-8")
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return re.sub(r"//.*", "", source)


def test_the_console_does_not_hardcode_reason_codes(registry):
    """The regression guard. Any internal reason code appearing as a string
    literal in the console means a list that will silently omit the next
    rulepack somebody adds.

    Comments and doc-blocks are stripped first: naming a code while
    *explaining* the mapping (`F29→4540`) is documentation, not a hardcoded
    menu, and forbidding that would push the explanation out of the code
    rather than fix anything.
    """
    codes = registry.reason_codes()
    offenders: list[str] = []

    for path in _console_sources():
        stripped = _code_only(path)
        for code in codes:
            if re.search(rf'["\']{re.escape(code)}["\']', stripped):
                offenders.append(f"{path.relative_to(_WEB_SRC)} hardcodes {code!r}")

    assert not offenders, (
        "the console hardcodes reason codes:\n  " + "\n  ".join(offenders)
        + "\n\nDrive them from GET /v1/rulepacks (web/src/lib/useRulepacks.ts) instead. "
        "A hardcoded list makes a rulepack the backend can adjudicate one the product "
        "cannot file, filter, or name."
    )


def test_the_console_does_not_hardcode_server_limits():
    """`max_artifact_bytes` and `conformal_min_n` are configuration. The
    console asserted 25 MB and "n ≥ 100" as facts, so a deployment that tuned
    either displayed a number untrue of itself — and in the upload case
    rejected files the API would have accepted."""
    upload = _code_only(_WEB_SRC / "components" / "EvidenceUpload.tsx")
    assert "limits?.max_artifact_bytes" in upload or "limits.max_artifact_bytes" in upload, (
        "the upload cap must come from /health, not a constant"
    )

    operations = _code_only(_WEB_SRC / "routes" / "OperationsPage.tsx")
    assert "min_calibration_n" in operations
    assert "n ≥ 100" not in operations, (
        "the calibration threshold is configurable and must not be stated as a literal"
    )
    # The progress bar too: `min(100, effective_n)` treated a raw count as a
    # percentage and only read correctly while the threshold happened to be
    # 100. A tuned deployment left the bar meaningless.
    assert "min_calibration_n)) * 100" in operations, (
        "calibration progress must be measured against the served threshold, not against 100"
    )


def test_health_and_ready_serve_those_limits():
    """The other end of the same contract: the console can only stop
    guessing if the server actually says."""
    from fastapi.testclient import TestClient

    from arbiter.api.deps import registry as app_registry
    from arbiter.main import app

    app_registry.load_dir(get_settings().rulepack_dir)
    client = TestClient(app)

    health = client.get("/health").json()
    assert health["limits"]["max_artifact_bytes"] == get_settings().max_artifact_bytes
    assert health["limits"]["max_artifacts_per_case"] > 0

    ready = client.get("/ready").json()
    assert ready["min_calibration_n"] == get_settings().conformal_min_n
    assert ready["conformal_alpha"] == get_settings().conformal_alpha
