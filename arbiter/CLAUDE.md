# ARBITER — invariants

## The core pattern

Every LLM boundary is guarded by a deterministic verifier with veto power. The
LLM proposes; the deterministic layer disposes. Turn the LLM up everywhere
upstream of the verdict — it is maximally powerful there — and give it zero
authority over the verdict itself. A hallucination is not a risk to monitor;
it is a thing the architecture structurally cannot be harmed by, because
nothing an LLM says is trusted until a deterministic check re-derives it.

| Boundary | Module | LLM job | Deterministic veto |
|---|---|---|---|
| Intent classify | `arbiter.intake` | read free-text complaint, pick a reason-code bucket | `verify_intent`: must resolve to a loaded rulepack, confidence ≥ 0.70, else ask the user or route to human triage — never guesses |
| Extraction | `arbiter.ingest.extract_vlm` | read messy PDF/image → typed fields | constrained JSON-schema decoding; `arbiter.ingest.route`'s fixed field-name table; numeric reconciliation |
| Advocates | `arbiter.advocate.llm_runner` | propose predicate/evidence links a mechanical tagger missed | `verify_assertions`: re-derives every claim from the objective fact set; rejected claims never reach the referee |
| Narration | `arbiter.narrate.llm` | fluent explanation of the proof tree | `arbiter.narrate.ground`: every sentence must cite a real node id; one ungrounded sentence discards the whole output |

**The one rule that never bends: no LLM ever picks the winner.** Not as a
tie-breaker, not as a sanity check, not to reduce abstention. `arbiter.horn`
— propositional Horn forward chaining — is the only decider. When it abstains,
that IS the answer; a human decides next, not a model. An LLM verdict would
re-introduce the exact black box this system exists to eliminate, and it
would break every property test in `tests/property/`.

There is exactly one component that can end a case *without* the referee:
`arbiter.eligibility`, the chargeback-right gate. It is held to the same
purity standard for the same reason (stdlib-only, import-linter contract
"Chargeback-right gate is pure"), and it answers a different question —
"may Amex charge this back at all?" rather than "who is right?". See
"The chargeback-right gate" below.

## Never violate

1. `arbiter.horn` imports nothing outside the standard library — no
   SQLAlchemy, no HTTP client, no LLM SDK, and (explicitly, not just
   incidentally) neither `arbiter.intake` nor `arbiter.llm`. Enforced by
   import-linter (`pyproject.toml`, contract "Referee (horn) is pure — the
   only decider touches no LLM"). This is the mechanical guarantee behind
   "the core pattern," above — not a style preference. Violation = CI
   failure.
2. No LLM output ever becomes the verdict — not as a tie-breaker when the
   referee abstains, not as a confidence override, not anywhere. Abstention
   is not the LLM's cue to step in; it's the cue for a human.
3. `arbiter.ingest` emits only typed Pydantic schemas
   (`arbiter.ingest.schemas`). Raw document text never crosses the boundary.
   There is no `raw_text` field on `ExtractionResult`, and there never will
   be.
4. Advocate assertions — LLM-proposed or deterministic — are re-derived from
   the objective evidence graph before use
   (`arbiter.advocate.verify.verify_assertions`). An advocate can find
   evidence; it can never assert a predicate into truth. Every rejection is
   counted into `decision.llm_rejections` and logged as a `case_event`
   (`LLM_ASSERTIONS_REJECTED`) — rejections are a visible signal, not a
   silently-swallowed detail.
5. Narration sentences are citation-checked (`arbiter.narrate.ground`); one
   ungrounded sentence discards the entire LLM narration and falls back to
   the deterministic template.
6. The intent classifier (`arbiter.intake`) selects which rulepack loads; it
   never influences the outcome once a case is adjudicated. Low confidence
   or an unresolved bucket routes to the user (`needs_user_confirmation`) or
   human triage (`route_to_human_triage`) — never a silent guess. Misrouting
   is the one intake error that corrupts everything downstream (wrong
   rulepack ⇒ wrong predicates ⇒ wrong evidence requirements entirely), so
   this boundary fails closed harder than most.
7. `datagen.outcome` / `datagen.world` must not import `arbiter.horn`,
   `arbiter.rulepack`, or `arbiter.evidence.derive`. If they do, every
   accuracy number is circular — and it will look excellent, so you won't
   notice. This is the single most consequential invariant in this
   repository for a different reason than #1: #1 protects the verdict from
   the LLM; this one protects the *evaluation* of the verdict from itself.
   Enforced by import-linter (contract "World model is independent of the
   rulepack").
8. `case_event` and `decision` are append-only (Postgres trigger
   `forbid_mutation()`, `alembic/versions/0001_initial_schema.py`).
   Corrections are new rows, never UPDATEs.
9. Every decision pins `rulepack_hash`. Rulepacks are content-addressed data
   (`rulepacks/amex/*.yaml`), never code, never edited in place.
10. Confidence comes from deterministic features
    (`arbiter.decision.confidence.ConfidenceVector`), never LLM self-report.
    `rejected_assertions` IS one of those features — more caught
    hallucinations this case means less confidence overall — but it is a
    *count arbiter.advocate.verify already computed mechanically*, not
    anything the LLM says about its own reliability.
11. Evidence degrades, never rejected. Failed ADEC verification demotes a
    claim to `SUBMITTED` tier; it does not reject it. The same principle
    applies to LLM availability generally: every LLM call site
    (`arbiter.llm.client.complete_json`, `extract_vlm`, `classify_intent`,
    `run_llm_advocate`) returns `None` on any failure and callers fall back
    to the deterministic default — never an exception that could propagate
    into a code path expecting something else.
12. `source_ref` (page + bbox) is populated on every extracted field
    (`arbiter.ingest.schemas.ExtractedField`).
13. Merkle leaf/node hashes use `0x00` / `0x01` domain separation
    (`arbiter.provenance.rfc6962.LEAF_PREFIX` / `NODE_PREFIX`).
14. ADEC `committed_at` is server-observed (`ProvenanceService.commit()`
    stamps `datetime.now()`). The merchant's claimed `event_time` is stored
    separately and is never authoritative for tier gating or the
    predates-deadline check.
15. The chargeback-right gate reads only its own closed attribute
    vocabulary (`arbiter.eligibility.models.ATTRIBUTE_VOCABULARY`), never a
    rulepack predicate — and no rule ever reads an eligibility attribute.
    The two must not merge: an exclusion decides whether the dispute is
    chargeable, a predicate is evidence within a dispute that is. If
    `card_present` were a predicate, "the card was present" would become an
    argument a merchant *wins* with, when the guide's actual position is
    that the dispute was never chargeable. Enforced by
    `tests/property/test_rulepacks.py::test_no_exclusion_reads_a_rulepack_predicate`.
16. An excluded or out-of-time dispute is `CHARGEBACK_INELIGIBLE`, never
    `MERCHANT_PREVAILS`. No evidence was weighed; recording it as a
    merchant win corrupts win rates, the fairness layer's per-rule
    disparate-impact analysis, and the conformal calibration pool at once.
    Its `conformal_set` is empty for the same reason — the gate the set
    describes never ran.

## The chargeback-right gate

Amex's published merchant chargeback guide gives every reason code two
fields that are not about the evidence: **"Maximum time a dispute can be
raised"** (120 days from network processing; 4554 adds an alternate clock
capped at 540) and **"Excluded Transactions"** (Card Present, SafeKey
liability shift, contactless/digital wallet, transactions chargeable under
another code...). Both remove the chargeback right outright.

`arbiter.eligibility` evaluates them from the rulepack's `chargeback_right`
block before anything else in `adjudicate_case`. When it closes, no evidence
is loaded, no advocate runs, and the referee is never called — because when
the right does not exist, none of those were the right thing to have done.

Three things about it that are easy to get backwards:

- **It is not the Reg Z/Reg E clock.** `arbiter.decision.deadlines` owns the
  issuer's statutory obligations to the card member; this owns the network's
  merchant-facing one. A dispute that misses the 120-day chargeback window
  is still a billing error Amex must resolve — it just resolves at Amex's
  cost rather than the merchant's. Neither module reads the other's numbers.
  A REG_E case that ends here still owes provisional credit, and
  `_record_ineligible` computes it.
- **Unknown fails OPEN here, and only here.** An attribute the ledger did
  not supply cannot fire an exclusion. Every other gate in this system fails
  closed *for the card member's protection*; an exclusion firing removes
  their dispute right with no downstream after it, so the conservative
  direction reverses. Unknowns are recorded (`EligibilityResult.undetermined`,
  `CHARGEBACK_RIGHT_UNDETERMINED` case event, an index in migration 0007) so
  coverage gaps get closed rather than silently widening the set of disputes
  that bypass a gate nobody notices is not running.
- **Conditions are a closed vocabulary, not a language.** No arithmetic, no
  nesting, no `eval`. Anything the guide actually says fits; anything that
  does not should become a new named attribute with a reviewed derivation.
  A rulepack is data loaded from disk, and an expression evaluator in it
  would be a code path from a YAML file to the interpreter, in the one
  component whose whole value is that its behaviour is inspectable.

## Scope boundary

ARBITER never predicts whether a transaction was fraudulent. It adjudicates a
claim already filed. No model scores transaction risk anywhere in this
codebase. Card-member dispute frequency is context for a human reviewer,
never a predicate.

## Prefer

- Simplicity explainable in one sentence over cleverness.
- Deterministic over probabilistic anywhere a decision is affected.
- Failing closed (abstain, ask the user, route to human triage) over
  guessing — at every boundary, not just the verdict.
- Property tests over example tests in `horn/` and `rulepacks/`
  (`tests/property/`). Generate their inputs through
  `tests/property/strategies.py` — never a fresh `itertools.product` over the
  predicate powerset. That function chooses exhaustive or sampled by the
  budget the caller passes, which is what lets a rulepack grow past 16
  predicates without the suite becoming either intractable or a lie. Read
  that module's docstring before adding a sweep; the fail direction, the
  three sampling layers and what each mode does and does not prove are all
  stated there.
- The rules-only path (`arbiter.advocate.runner`, deterministic
  prime-implicant search) must keep working with every LLM disabled — it is
  the default, not a fallback bolted on afterward. Same for
  `arbiter.intake`: if the classifier is unavailable, cases route to human
  triage, they don't block.
- When adding a new LLM boundary, ship the deterministic verifier in the
  same change, not a follow-up. A boundary with LLM output and no veto isn't
  half of this pattern — it's a different, unguarded system wearing this
  one's clothes.
- When adding a rulepack, transcribe its `chargeback_right` block in the
  same change. Rules without a gate mean every dispute under that code
  reaches the merits, including ones the network gives no chargeback right
  for at all.

## When stuck

Be right or silent, never confidently wrong. If a choice trades correctness
for coverage, take correctness. See `arbiter.narrate.llm` for what "silent"
looks like in code: every failure path — model unreachable, malformed JSON,
a sentence that cites nothing — returns `None`, and the deterministic
template renders instead. It never fabricates prose to fill the gap, and it
never quietly drops a bad citation to make its own output survive: a
hallucinated node id is passed to `narrate.ground` intact, precisely so the
veto is the thing that catches it. A boundary that sanitises its output
before the verifier sees it is unguarded while looking safe.

## Repository-specific notes for future work here

- The decision core (`src/arbiter/{horn,evidence,provenance,decision,
  narrate,audit,fairness}`) started as a hand-verified, in-memory-only
  prototype (see git history) and was restructured into this production
  layout without changing its logic — the same bug fixes and property tests
  carried over. Prefer extending it over rewriting it.
- `arbiter.network` reads `arbiter.db.models.SeedTransaction`, a synthetic
  stand-in for a real Amex ledger (see that model's docstring). Swapping in
  a real ledger client is a boundary change at `arbiter.network.loader`
  only.
- `arbiter.llm.client` and `arbiter.ingest.extract_vlm` both call a local
  Qwen2.5-VL model via Ollama (`http://localhost:11434`), not a cloud API —
  and, resource-constrained engineering choice stated honestly, the SAME
  model serves extraction, intent classification, and advocate search
  rather than three separate multi-GB pulls. Every call site degrades to
  `None`/fallback (never crashes) if Ollama isn't running or the model isn't
  pulled — see invariant #11.
- `arbiter.advocate.llm_runner`'s real marginal value in this architecture
  is narrower than "finds arguments a human would miss": `arbiter.horn.
  implicants.enumerate_prime_implicants` is exact and complete over
  already-derived facts, so the deterministic search has no blind spot to
  fill there. The LLM's genuine job is catching predicate-relevant evidence
  `arbiter.ingest.route`'s intentionally partial `_PREDICATE_HINTS` table
  didn't mechanically tag — read that module's docstring before assuming an
  LLM advocate result means more than it does.
- The three shipped rulepacks are transcribed from American Express's own
  published merchant guide ("Chargeback Codes — What they mean", Australian
  merchant reason codes). ARBITER's internal reason codes map to the guide's
  four-digit network codes as F29→4540 (Card Not Present, pp.17-20),
  C08→4554 (Goods And Services Not Received, pp.23-24), C02→4513 (Credit Not
  Presented, p.6). `RulepackRegistry.resolve()` accepts either dialect. The
  guide lists 22 reason codes and 6 retrieval-request codes; three are
  modelled. The unmodelled ones are not a gap in the machinery — adding one
  is a YAML file — and rulepack size is no longer the constraint it was:
  `tests/property/strategies.py` replaced the full-powerset sweep with a
  budgeted exhaustive/t-wise-sampled matrix, so a 34-predicate rulepack costs
  ~250 assignments instead of 17 billion. What to know when adding one: under
  16 EDB predicates it keeps a brute-force proof automatically; over that,
  the implicant and conflict properties become sampled (strong, not a proof)
  and the nightly `FULL_POWERSET_SWEEP=1` job is what still proves them.
  Check `test_strategies.py::test_small_rulepack_under_budget_stays_exhaustive`
  — it fails the build precisely when a new rulepack crosses that line, so the
  transition is never silent.
- The retrieval-request stage (guide pp.31-32: codes 6003/6006/6008/6013/
  6014/6016, and reason codes 4516/4517 for an unfulfilled or illegible
  response) is deliberately not modelled. It is a genuine pre-chargeback
  workflow, not a variant of adjudication, and half of it — the exclusions
  keyed on retrieval-request code, the No Signature/No PIN Program, the
  AU$100/AU$35 contactless thresholds — is already expressible in the
  eligibility vocabulary (`retrieval_request_code`,
  `no_signature_no_pin_program`, `amount_minor`) whenever someone builds
  the workflow the other half needs.
- `evals/hallucination.py` is the eval that proves the veto holds under
  adversarial pressure, not just in the clean case: it poisons an evidence
  node with an embedded instruction ("assert X regardless of evidence") and
  measures whether `verify_assertions` accepts it (must be 0) and whether it
  ever moves a verdict (must be 0). Re-run it after touching
  `arbiter.advocate.llm_runner` or `arbiter.advocate.verify`.
