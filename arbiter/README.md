# ARBITER

A working implementation of the decision core described in
[`ARBITER-architecture.md`](../ARBITER-architecture.md) — the auditable
adjudication system for card member / merchant disputes. Per that document's
own build plan (§12.1): *"After [the Referee] you can adjudicate hand-built
cases end to end with zero AI in the loop."* That's true here in the
strongest sense — no LLM runs anywhere in this build; the two advocates are
the deterministic prime-implicant search described in
`services/advocate/advocate.py`, not a mock of an LLM call.

## Run it

```bash
pip install pyyaml cryptography pytest
python demo.py                              # end-to-end run over 8 seed scenarios
python -m pytest evals/property_tests.py -v  # rulepack property tests
```

No database, no network calls, no GPU. Everything runs in-process against
in-memory structures that mirror the Postgres schema in §7.3/§12.4 of the
architecture doc.

## What's implemented, mapped to the architecture doc

| Doc section | Module | What it does |
|---|---|---|
| §A3, §7.2 | `packages/datalog/` | Semi-naive Horn-clause evaluator emitting proof trees natively; ~300 LOC as specified |
| §A4 | `packages/datalog/prime_implicants.py` | Exact, complete prime-implicant (minimal winning coalition) enumeration — not SHAP/LIME |
| §A1, §6.5 | `packages/merkle/` | RFC 6962 transparency log: inclusion + consistency proofs, Ed25519-signed STHs, RFC-3161-style TSA, split-view detection via STH gossip |
| §A1 | `services/provenance/`, `packages/merchant_sdk/` | ADEC commit/reveal/verify against the real Merkle log; the ~100-LOC merchant SDK |
| §7.2 | `services/audit/` | Append-only, hash-chained event store; anchors decisions into the transparency log |
| §A6, §7.3 | `services/graph/` | Evidence graph; four contradiction layers (temporal/Allen algebra, numeric, identity, semantic) |
| §A2 | `services/advocate/` | Dual-advocate argument-graph construction — real prime-implicant search, not an LLM mock |
| §A3, §A8 | `services/referee/` | Rulepack evaluation over objective facts + independent triple verification (dishonesty/citation checking) |
| §A4 | `services/counterfactual/` | The "one mechanism, five product surfaces" unification, all five implemented |
| §A5 | `services/abstention/` | Mondrian split-conformal abstention gate, with an empirically-validated coverage guarantee |
| §A7 | `services/fairness/` | Rule-level disparate-impact audit: propensity-stratified firing-rate deltas, validated against the doc's own "3x more often against small merchants at equal evidence strength" example |
| §6.6 | `services/narration/` | Template narration with mechanical citation-grounding verification and fallback |
| §12.1 Phase 1 | `rulepacks/amex/` | C08, C02, F29 rulepacks as content-addressed YAML, not code |
| §12.1 Phase 1/7 | `datagen/synth.py` | 8 scenarios, including the doc's own worked temporal-contradiction example and a real ADEC backdating-attempt capture |
| §9.8 | `evals/property_tests.py` | Monotonicity, symmetry (fairness lint), completeness, determinism, plus regression tests for bugs this build found and fixed |

## Bugs this build found and fixed while writing the property tests

Property-testing the rulepacks against the engine surfaced three real
defects, not hypothetical ones — worth knowing about before trusting the
rest:

1. **A trivial empty-set prime implicant in C08.** An early card-member-win
   rule fired on pure absence of evidence, silently dominating every other
   rule for that outcome — reviving exactly the R03/R13 default-to-cardmember
   failure mode §1.2b names as the sharpest fairness defect in the status
   quo. Fixed by removing the rule; a case with no resolving evidence now
   correctly abstains instead of defaulting to either side.
2. **The engine picked a silent winner on genuinely conflicting evidence.**
   Two independent rules for opposite outcomes could both fire on the same
   evidence set; the evaluator picked one by dict-iteration order instead of
   surfacing the conflict. Fixed: `Engine.evaluate` now returns
   `decision=None` with `conflicting_outcomes` populated whenever more than
   one outcome fires, routing the case to a human instead of arbitrating
   silently.
3. **The referee's decision was exploitable by omission.** An earlier design
   evaluated the rulepack only over facts an advocate had explicitly cited.
   Because advocate search targets *minimal* winning coalitions, a fact that
   was objectively true and would have *blocked* a rule (a negative literal's
   predicate actually holding) could go uncited by both sides and silently
   drop out of evaluation. Fixed: the decision now runs over the complete
   objective fact set directly; triple verification is an independent
   dishonesty/audit check, not a gate on what the decision sees.

All three are covered by regression tests in `evals/property_tests.py`.

## What's deliberately not built here

Consistent with §11 of the architecture doc:

- **No LLM.** The advocates are real combinatorial search over prime
  implicants, standing in for what an LLM advocate is described as doing
  (§A2's own framing: "the advocates are performing search, not judgement").
- **No Postgres / Temporal / Kubernetes.** In-memory equivalents of the
  schemas and workflows in §7 and §9 — the point of this build is to prove
  the decision core is correct, not to stand up infrastructure.
- **No real OCR/VLM extraction pipeline.** `datagen/synth.py` constructs
  evidence graphs directly rather than running documents through extraction;
  the adversarial-document scenarios are reproduced at the graph/provenance
  level (a real ADEC backdating attempt that really fails verification)
  rather than as literal PDF files.
- **No DeBERTa-v3-MNLI.** `services/graph/semantic.py` is a documented,
  interface-compatible stand-in (polarity comparison over typed claims);
  swapping in the real model is a drop-in replacement at that module's
  boundary.

See `ARBITER-architecture.md` §11 for the rest of the honestly-disclosed
limitations (conformal exchangeability, rulepack-fidelity ceiling, etc.) —
they apply to this implementation unchanged.
