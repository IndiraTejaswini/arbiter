# ARBITER — invariants

## Never violate

1. `arbiter.horn` imports nothing outside the standard library. No SQLAlchemy, HTTP
   clients, or LLM SDKs. Enforced by import-linter (`pyproject.toml`,
   contract "Referee (horn) is pure"). Violation = CI failure.
2. No ML inference in the adjudication path. The referee is propositional Horn
   forward chaining (`arbiter.horn.chain.Engine`).
3. `arbiter.ingest` emits only typed Pydantic schemas (`arbiter.ingest.schemas`).
   Raw document text never crosses the boundary. There is no `raw_text` field on
   `ExtractionResult`, and there never will be.
4. Advocate assertions are re-derived from the graph before use
   (`arbiter.advocate.verify.verify_assertions`). An advocate can find evidence;
   it can never assert a predicate into truth.
5. `datagen.outcome` / `datagen.world` must not import `arbiter.horn`,
   `arbiter.rulepack`, or `arbiter.evidence.derive`. If they do, every accuracy
   number is circular — and it will look excellent, so you won't notice. This is
   the single most consequential invariant in this repository. Enforced by
   import-linter (contract "World model is independent of the rulepack").
6. `case_event` and `decision` are append-only (Postgres trigger
   `forbid_mutation()`, `alembic/versions/0001_initial_schema.py`). Corrections
   are new rows, never UPDATEs.
7. Every decision pins `rulepack_hash`. Rulepacks are content-addressed data
   (`rulepacks/amex/*.yaml`), never code, never edited in place.
8. Confidence comes from deterministic features (`arbiter.decision.confidence`),
   never LLM self-report.
9. Evidence degrades, never rejected. Failed ADEC verification demotes a claim to
   `SUBMITTED` tier; it does not reject it.
10. `source_ref` (page + bbox) is populated on every extracted field
    (`arbiter.ingest.schemas.ExtractedField`).
11. Merkle leaf/node hashes use `0x00` / `0x01` domain separation
    (`arbiter.provenance.rfc6962.LEAF_PREFIX` / `NODE_PREFIX`).
12. ADEC `committed_at` is server-observed (`ProvenanceService.commit()` stamps
    `datetime.now()`). The merchant's claimed `event_time` is stored separately
    and is never authoritative for tier gating or the predates-deadline check.

## Scope boundary

ARBITER never predicts whether a transaction was fraudulent. It adjudicates a
claim already filed. No model scores transaction risk anywhere in this codebase.
Card-member dispute frequency is context for a human reviewer, never a
predicate.

## Prefer

- Simplicity explainable in one sentence over cleverness.
- Deterministic over probabilistic anywhere a decision is affected.
- Failing closed (abstain) over guessing.
- Property tests over example tests in `horn/` and `rulepacks/`
  (`tests/property/`).
- The rules-only path (`arbiter.advocate.runner`, deterministic prime-implicant
  search) must keep working with every LLM disabled — it is the default, not a
  fallback bolted on afterward.

## When stuck

Be right or silent, never confidently wrong. If a choice trades correctness for
coverage, take correctness. See `arbiter.narrate.llm` for what "silent" looks
like in code: a stub that returns `None` and lets the template renderer take
over, not a stub that fabricates prose.

## Repository-specific notes for future work here

- The decision core (`src/arbiter/{horn,evidence,provenance,advocate,decision,
  narrate,audit,fairness}`) started as a hand-verified, in-memory-only
  prototype (see git history) and was restructured into this production layout
  without changing its logic — the same bug fixes and property tests carried
  over. Prefer extending it over rewriting it.
- `arbiter.network` reads `arbiter.db.models.SeedTransaction`, a synthetic
  stand-in for a real Amex ledger (see that model's docstring). Swapping in a
  real ledger client is a boundary change at `arbiter.network.loader` only.
- `arbiter.ingest.extract_vlm` calls a local Qwen2.5-VL model via Ollama
  (`http://localhost:11434`), not a cloud API. It degrades to `None` (never
  crashes) if Ollama isn't running or the model isn't pulled — the same
  contract `extract_ocr.py` uses for PaddleOCR's optional dependency.
