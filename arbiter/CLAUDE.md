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
  (`tests/property/`).
- The rules-only path (`arbiter.advocate.runner`, deterministic
  prime-implicant search) must keep working with every LLM disabled — it is
  the default, not a fallback bolted on afterward. Same for
  `arbiter.intake`: if the classifier is unavailable, cases route to human
  triage, they don't block.
- When adding a new LLM boundary, ship the deterministic verifier in the
  same change, not a follow-up. A boundary with LLM output and no veto isn't
  half of this pattern — it's a different, unguarded system wearing this
  one's clothes.

## When stuck

Be right or silent, never confidently wrong. If a choice trades correctness
for coverage, take correctness. See `arbiter.narrate.llm` for what "silent"
looks like in code: a stub that returns `None` and lets the template
renderer take over, not a stub that fabricates prose.

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
- `evals/hallucination.py` is the eval that proves the veto holds under
  adversarial pressure, not just in the clean case: it poisons an evidence
  node with an embedded instruction ("assert X regardless of evidence") and
  measures whether `verify_assertions` accepts it (must be 0) and whether it
  ever moves a verdict (must be 0). Re-run it after touching
  `arbiter.advocate.llm_runner` or `arbiter.advocate.verify`.
