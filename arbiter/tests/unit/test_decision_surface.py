"""
Everything the backend computes must be reachable from the API, and the
pipeline's stage vocabulary must match the one the console renders.

These are regression tests for a class of defect that unit tests structurally
miss: the code was correct, the tests passed, and the output went nowhere.

  1. `arbiter.narrate` ran on EVERY adjudication -- template rendering plus
     `arbiter.narrate.ground`'s citation verifier, an explicit CLAUDE.md LLM
     boundary -- and its output was returned to a worker that discarded it.
     No column, no API field, no UI. An entire guarded boundary produced text
     no human could read.

  2. `decision.eligibility` -- the chargeback-right gate's full finding, which
     `arbiter.api.orchestration` deliberately records on every decision so
     that "the gate ran and the right was available" is a positive claim the
     audit trail can make -- was persisted and never returned.

  3. `adjudicate_case` re-emitted `CASE_FILED` at the top of every run, so a
     re-adjudicated case showed three "filed" events, two of them after its
     evidence had been uploaded.

  4. The pipeline published `CHECKING_CHARGEBACK_RIGHT` as the first stage of
     every adjudication and neither `realtime.events.STAGES` nor the
     console's stage list contained it, so the most decisive gate in the
     system was invisible while it ran.

A test that asserts a value is computed is not the same as a test that
asserts it is delivered. These assert delivery.
"""

from __future__ import annotations

import ast
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from arbiter.audit import case_log
from arbiter.db import models as m
from arbiter.realtime.events import STAGES

_SRC = Path(__file__).resolve().parents[2] / "src" / "arbiter"


# -- 1. Narration is stored, and stored in the shape the client expects ----


def test_decision_row_has_a_narration_column():
    """The storage-level regression. Without this column the narration has
    nowhere to go, and every fix above it is decoration."""
    assert "narration" in m.DecisionRow.__table__.columns


def test_narration_to_dict_matches_the_wire_contract():
    """`Narration.to_dict()` is what gets persisted and what the frontend
    `Narration` interface is typed against. A field added on one side and
    not the other is exactly how this went unnoticed the first time."""
    from arbiter.narrate.template import Citation, Narration

    payload = Narration(
        text="Rule X is satisfied.", sentences=("Rule X is satisfied.",),
        citations=(Citation(sentence_idx=0, node_id="node-1"),), source="template",
    ).to_dict()

    assert set(payload) == {"text", "source", "sentences", "citations"}
    assert payload["citations"] == [{"sentence_idx": 0, "node_id": "node-1"}]
    # `sentences` is part of the contract because `sentence_idx` indexes it.
    # A client that re-derives the split from `text` gets a different array
    # the moment a sentence contains "12 CFR 1005.11" -- and then every
    # citation after that point attaches to the wrong claim.
    assert payload["sentences"] == ["Rule X is satisfied."]
    assert payload["citations"][0]["sentence_idx"] < len(payload["sentences"])


def test_citation_indices_address_the_sentences_that_ship_with_them():
    """The property that makes the pair usable: every citation index must
    resolve inside the sentence array in the same payload."""
    from arbiter.narrate.template import Citation, Narration

    payload = Narration(
        text="A. B. C.", sentences=("A.", "B.", "C."),
        citations=(Citation(0, "n1"), Citation(2, "n2")), source="template",
    ).to_dict()

    for citation in payload["citations"]:
        assert 0 <= citation["sentence_idx"] < len(payload["sentences"])


def test_grounding_failure_is_distinguishable_in_the_stored_record():
    """`source` is load-bearing, not metadata. "template_fallback" means a
    generated narration WAS produced and then discarded for citing a node
    that does not exist -- CLAUDE.md invariant #5's veto firing. If only the
    prose survived, that the veto fired would be unrecoverable, and a caught
    hallucination would look identical to a case with little to say."""
    from arbiter.narrate.ground import verify_citations
    from arbiter.narrate.template import Citation, Narration

    ungrounded = Narration(
        text="A claim.", sentences=("A claim.",),
        citations=(Citation(sentence_idx=0, node_id="does-not-exist"),), source="template",
    )
    ok, bad = verify_citations(ungrounded, {"a-real-node"})
    assert not ok and len(bad) == 1

    # And the fallback the renderer substitutes is LABELLED, not silent --
    # that label is the only surviving trace that a narration was rejected.
    fallback = Narration(text="x", sentences=("x",), citations=(), source="template_fallback")
    assert fallback.to_dict()["source"] == "template_fallback"


# -- 2. The decision route delivers narration and eligibility -------------


class _Decision:
    """Minimal stand-in for a persisted decision row."""

    def __init__(self, **overrides):
        self.decision_id = uuid.uuid4()
        self.outcome = m.OutcomeEnum.CARD_MEMBER_PREVAILS
        self.abstained = False
        self.confidence = 0.91
        self.conformal_set = ["CARD_MEMBER_WINS"]
        self.rulepack_hash = b"\xab" * 32
        self.merchant_silent = False
        self.provisional_credit_due = False
        self.llm_rejections = 0
        self.selected_for_audit = False
        self.review_selection_probability = None
        self.contradiction_analysis = {"complete": True}
        self.predicates = {"delivery_confirmed": "TRUE"}
        self.proof_tree = {"rule_id": "R1"}
        self.counterfactuals = {}
        self.escalation_reason = None
        self.narration = {"text": "Outcome: CARD_MEMBER_WINS.", "source": "template", "citations": []}
        self.eligibility = {
            "available": True, "network_code": "4554", "reason": "chargeback right available",
            "filing_window": {"timely": True, "branches": []},
            "exclusions_fired": [], "exclusions_evaluated": 3, "undetermined_attributes": [],
        }
        self.decided_at = datetime.now(timezone.utc)
        for key, value in overrides.items():
            setattr(self, key, value)


class _Case:
    def __init__(self):
        self.case_id = uuid.uuid4()
        self.card_member_id = uuid.uuid4()
        self.merchant_id = uuid.uuid4()


class _OneDecisionSession:
    """Returns one case and one decision; no Postgres required.

    The point of the test is the response BODY, not the query -- a suite that
    needs infrastructure to check whether a field is serialised is a suite
    that stops checking.
    """

    def __init__(self, case: _Case, decision: _Decision):
        self._case, self._decision = case, decision

    def get(self, _model, _pk):
        return self._case

    def execute(self, *_args, **_kwargs):
        decision = self._decision

        class _Result:
            def scalars(self):
                class _Scalars:
                    def first(self):
                        return decision
                return _Scalars()
        return _Result()

    def close(self):
        pass


@pytest.fixture
def decision_response():
    """Deliberately does NOT enter the TestClient context manager, so the
    app's lifespan never runs. Lifespan connects to Postgres and Redis and
    rehydrates the transparency log; a test of whether a field is serialised
    has no business requiring infrastructure, and a suite that needs it is a
    suite that stops running."""
    from fastapi.testclient import TestClient

    from arbiter.auth import Actor, Role
    from arbiter.auth.deps import get_current_actor
    from arbiter.db.session import get_session
    from arbiter.main import app

    case, decision = _Case(), _Decision()
    app.dependency_overrides[get_session] = lambda: _OneDecisionSession(case, decision)
    app.dependency_overrides[get_current_actor] = lambda: Actor(
        actor_id="reviewer", role=Role.REVIEWER, bound_id=None
    )
    try:
        client = TestClient(app)
        yield client.get(f"/v1/cases/{case.case_id}/decision").json()
    finally:
        app.dependency_overrides.clear()


def test_decision_response_carries_the_narration(decision_response):
    """THE regression. This route's docstring has always advertised
    "proof tree + counterfactuals + narration"; it returned the first two."""
    assert "narration" in decision_response, (
        "the decision route promised narration in its own docstring and returned none -- "
        "arbiter.narrate ran on every case and its output reached nobody"
    )
    assert decision_response["narration"]["text"]
    assert decision_response["narration"]["source"] == "template"


def test_decision_response_carries_the_chargeback_right_finding(decision_response):
    """Recorded on every decision precisely so "the gate ran and found the
    right available" is a claim the API can make. Unreachable, it was only a
    claim the database could make to itself."""
    assert "eligibility" in decision_response
    assert decision_response["eligibility"]["network_code"] == "4554"
    assert decision_response["eligibility"]["available"] is True


# -- 3. The audit taxonomy is closed, and CASE_FILED means filed ----------


def _orchestration_tree() -> ast.Module:
    return ast.parse((_SRC / "api" / "orchestration.py").read_text(encoding="utf-8"))


def _emitted_event_types(tree: ast.Module) -> list[str]:
    """Every event type appended in a module, as written -- either a
    `case_log.NAME` attribute or a bare string literal."""
    emitted: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
        if name not in {"_append_event", "append_case_event"} or len(node.args) < 3:
            continue
        event_type = node.args[2]
        if isinstance(event_type, ast.Constant):
            emitted.append(str(event_type.value))
        elif isinstance(event_type, ast.Attribute):
            emitted.append(event_type.attr)
    return emitted


def test_adjudication_does_not_re_emit_case_filed():
    """A case adjudicated twice used to show three CASE_FILED events: the
    real one from intake with the full payload, then two thinner ones
    claiming the case was filed after its evidence had already arrived. In a
    system whose storage model IS the audit log, that is not cosmetic.

    Asserted over the AST rather than the text, so the comment explaining
    this defect does not itself trip the test."""
    emitted = _emitted_event_types(_orchestration_tree())
    assert "CASE_FILED" not in emitted, (
        "adjudicate_case must not write a CASE_FILED event -- create_dispute owns that "
        "event, and re-emitting it makes 'filed' a thing that happens repeatedly"
    )
    assert "ADJUDICATION_STARTED" in emitted


def test_intake_is_the_only_writer_of_case_filed():
    """The other half of the same invariant: exactly one place in the system
    may say a case was filed, and it is the place that creates the row."""
    disputes = ast.parse((_SRC / "api" / "routes" / "disputes.py").read_text(encoding="utf-8"))
    assert _emitted_event_types(disputes).count("CASE_FILED") == 1


def test_every_audit_event_type_is_a_named_constant():
    """`case_log`'s module docstring says the taxonomy exists so the set is
    closed and greppable. Half the pipeline was passing bare string
    literals, which is how SELECTED_FOR_AUDIT_REVIEW ended up written by the
    orchestrator and absent from the taxonomy."""
    known = {
        value for name, value in vars(case_log).items()
        if name.isupper() and isinstance(value, str) and name not in {"GENESIS_HASH"}
    }
    for node in ast.walk(_orchestration_tree()):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
        if name not in {"_append_event", "append_case_event"}:
            continue
        # event_type is the third positional argument: (session, case_id, event_type, ...)
        if len(node.args) >= 3 and isinstance(node.args[2], ast.Constant):
            pytest.fail(
                f"orchestration passes the bare string {node.args[2].value!r} as an event "
                f"type; use the arbiter.audit.case_log constant so the taxonomy stays closed"
            )
    assert case_log.ADJUDICATION_STARTED in known
    assert case_log.SELECTED_FOR_AUDIT_REVIEW in known
    assert case_log.NARRATION_GROUNDING_FAILED in known


def test_no_unwritable_event_type_survives_in_the_taxonomy():
    """`INTENT_UNRESOLVED` was defined and written by nothing -- and could
    never be written, because `case_event` is keyed by case_id and an
    unresolved intent is the outcome in which no case is created. A constant
    for an event that cannot exist reads as an unfinished feature."""
    assert not hasattr(case_log, "INTENT_UNRESOLVED")


def test_evidence_upload_writes_an_audit_event():
    """`EVIDENCE_UPLOADED` was in the taxonomy and written by nothing, so a
    decision cited documents whose arrival was invisible -- a reader could
    not tell whether an artifact predated the adjudication or was slipped in
    after it, which is the timing question an audit trail on a dispute
    exists to answer."""
    source = (_SRC / "api" / "routes" / "evidence.py").read_text(encoding="utf-8")
    assert "case_log.EVIDENCE_UPLOADED" in source


# -- 4. The stage vocabulary is one vocabulary ----------------------------


def _published_stages() -> set[str]:
    """Every literal stage `publish_stage(...)` is called with in the
    pipeline."""
    stages: set[str] = set()
    for path in (_SRC / "api" / "orchestration.py", _SRC / "api" / "routes" / "disputes.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "id", "") == "publish_stage"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
            ):
                stages.add(node.args[1].value)
    return stages


def test_every_published_stage_is_declared():
    """The exact drift that broke the console: the pipeline published
    CHECKING_CHARGEBACK_RIGHT, STAGES did not list it, and the frontend --
    which mirrors STAGES -- rendered a progress bar that moved with no step
    highlighted. Undeclared stages are silent by construction, so only a
    test like this catches the next one."""
    undeclared = _published_stages() - set(STAGES)
    assert not undeclared, (
        f"the pipeline publishes {sorted(undeclared)}, which arbiter.realtime.events.STAGES "
        f"does not declare. The console renders against STAGES, so an undeclared stage is "
        f"an invisible one -- add it to STAGES and to web/src/components/StatusStream.tsx"
    )


def test_the_console_stage_list_matches_the_backend():
    """The frontend list is a copy of the backend's by necessity (no shared
    codegen here), so the copy is asserted rather than hoped for."""
    tsx = (
        Path(__file__).resolve().parents[2] / "web" / "src" / "components" / "StatusStream.tsx"
    ).read_text(encoding="utf-8")
    non_terminal = [s for s in STAGES if s not in {"DECIDED", "ESCALATED"}]
    for stage in non_terminal:
        assert f'id: "{stage}"' in tsx, (
            f"{stage} is published by the pipeline and declared in STAGES but the console's "
            f"progress indicator does not list it, so it will never light up"
        )
    # And nothing in the console that the backend cannot emit. CLASSIFYING was
    # listed there for an entire build: intent classification happens before a
    # case exists, so it has no `case:{id}` channel and could never arrive.
    assert 'id: "CLASSIFYING"' not in tsx


def test_intake_does_not_publish_to_a_channel_with_no_subscriber():
    """`create_dispute` published a stage keyed by transaction_id. The SSE
    route subscribes to `case:{case_id}`, and at intake there is no case --
    so the event went to a channel nobody could be listening on."""
    source = (_SRC / "api" / "routes" / "disputes.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "publish_stage":
            pytest.fail(
                "create_dispute must not publish stage events: no case exists yet, so there "
                "is no case:{id} channel for a browser to be subscribed to"
            )


# -- 5. Intake: status codes and a bounded cache --------------------------


class _SeedOnlySession:
    """A session that resolves the seed transaction and nothing else.

    The unresolved-intent branch returns before any write, so this is the
    complete set of database interaction that path performs.
    """

    def __init__(self, seed):
        self._seed = seed

    def execute(self, *_args, **_kwargs):
        seed = self._seed

        class _Result:
            def scalar_one_or_none(self):
                return seed
        return _Result()

    def close(self):
        pass


def test_unresolved_intent_does_not_report_created(monkeypatch):
    """201 Created for a request that created nothing.

    Asserted end-to-end through the app rather than by reading the source,
    because the thing under test is what FastAPI actually puts on the status
    line. A client trusting that line over the body -- a proxy, a retry
    policy, a generated SDK -- records a dispute that does not exist, and the
    card member's statutory clock never starts.
    """
    from fastapi.testclient import TestClient

    from arbiter.api.routes import disputes as disputes_route
    from arbiter.auth import Actor, Role
    from arbiter.auth.deps import get_current_actor
    from arbiter.db.session import get_session
    from arbiter.main import app

    card_member_id = uuid.uuid4()

    class _Seed:
        transaction_id = uuid.uuid4()
        card_member_id_ = card_member_id
        merchant_id = uuid.uuid4()
        amount_minor = 8999
        currency = "USD"
        transaction_at = datetime.now(timezone.utc)
        reg_regime = "REG_Z"

    seed = _Seed()
    seed.card_member_id = card_member_id

    # The classifier is unavailable -> verify_intent routes to human triage.
    # Patched rather than reached, so the test never depends on Ollama and
    # never waits on a socket timeout (CLAUDE.md #11: every LLM call site
    # degrades to None, which is exactly the path being exercised).
    monkeypatch.setattr(disputes_route, "classify_intent", lambda *a, **k: None)

    app.dependency_overrides[get_session] = lambda: _SeedOnlySession(seed)
    app.dependency_overrides[get_current_actor] = lambda: Actor(
        actor_id="cm", role=Role.CARD_MEMBER, bound_id=str(card_member_id)
    )
    try:
        client = TestClient(app)
        response = client.post(
            "/v1/disputes",
            headers={"Idempotency-Key": f"test-{uuid.uuid4()}"},
            json={"transaction_id": str(seed.transaction_id), "complaint_text": "something went wrong"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, (
        "the unresolved-intent branch creates no case and must not answer 201 Created"
    )
    body = response.json()
    assert body["resolved"] is False
    assert body["route_to_human_triage"] is True
    # And it really did decline to guess -- the whole point of the branch.
    assert body["proposed_reason_code"] is None


def test_idempotency_cache_is_bounded():
    """It was an unbounded dict holding one entry per dispute ever filed by
    the process, never evicted, on a service meant to run for weeks."""
    from arbiter.api.routes.disputes import (
        _IDEMPOTENCY_CACHE_MAX,
        _idempotency_cache,
        _remember_idempotency,
    )

    _idempotency_cache.clear()
    first_key = "key-0"
    for i in range(_IDEMPOTENCY_CACHE_MAX + 50):
        _remember_idempotency(f"key-{i}", uuid.uuid4())

    assert len(_idempotency_cache) == _IDEMPOTENCY_CACHE_MAX
    # Oldest evicted, newest retained: a retry arrives seconds after the
    # original, so recency is exactly the right thing to keep.
    assert first_key not in _idempotency_cache
    assert f"key-{_IDEMPOTENCY_CACHE_MAX + 49}" in _idempotency_cache
    _idempotency_cache.clear()


# -- 6. The ineligible path records the same things ----------------------


def test_ineligible_decisions_are_still_explained():
    """A case ended by the gate gets deterministic prose too. It is the ONE
    outcome with no proof tree, no predicates and no counterfactuals, so if
    it had no narration either, the party would receive a verdict with no
    explanation attached to it at all."""
    source = (_SRC / "api" / "orchestration.py").read_text(encoding="utf-8")
    ineligible = source.split("def _record_ineligible")[1].split("def adjudicate_case")[0]
    assert "narration=" in ineligible, (
        "_record_ineligible must persist its narration; it is the only explanation a "
        "chargeback-ineligible case has"
    )
    assert '"source": "eligibility_gate"' in ineligible


def test_ineligible_narration_states_the_regulatory_carve_out():
    """The single most important sentence in the product: being told the
    merchant cannot be charged back must not read as being told your rights
    against the issuer are decided."""
    from arbiter.api.orchestration import _ineligible_narration
    from arbiter.eligibility.evaluate import (
        EligibilityResult,
        FilingWindowFinding,
    )

    result = EligibilityResult(
        available=False, network_code="4540", reason="excluded transaction",
        exclusions=(), filing_window=FilingWindowFinding(timely=True, branches=()),
        undetermined=(),
    )
    text = _ineligible_narration(result, "F29", "4540")
    assert "1026.13" in text and "1005.11" in text
    assert "does not" in text.lower()


# -- 7. Deadlines still describe a real clock -----------------------------


def test_reg_e_provisional_credit_clock_is_business_days():
    """Guard on a number the console renders verbatim as a legal claim."""
    from arbiter.decision.deadlines import compute_deadlines

    # A Monday, so ten business days lands two calendar weeks later.
    filed = datetime(2026, 3, 2, 12, 0, tzinfo=timezone.utc)
    deadlines = compute_deadlines("REG_E", filed)
    assert deadlines.provisional_credit_deadline == deadlines.ack_deadline
    assert deadlines.provisional_credit_deadline == filed + timedelta(days=14)

    # Reg Z has no provisional-credit clock; modelling one would misstate the
    # regulation, and the UI keys its Reg E row off exactly this being None.
    assert compute_deadlines("REG_Z", filed).provisional_credit_deadline is None
