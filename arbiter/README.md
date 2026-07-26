# ARBITER

Auditable dispute adjudication for Amex card-member vs. merchant chargebacks.
Rules decide; models never do. See [`CLAUDE.md`](CLAUDE.md) for the twelve
invariants every module here obeys, and the build spec (§ headers referenced
below) for the full design.

**Thesis:** disputes don't need a smarter predictor. They need an auditable
adjudicator that knows when to stop.

This repo went through two stages, both real:

1. An in-memory, zero-dependency decision core (`arbiter.horn`,
   `arbiter.evidence`, `arbiter.provenance`, `arbiter.advocate`,
   `arbiter.decision`, `arbiter.narrate`, `arbiter.audit`, `arbiter.fairness`)
   proven correct against its own property tests and a hand-built 8-scenario
   demo (`demo.py`) — no LLM, no database, runs anywhere Python does.
2. A production shell around that same core: Postgres persistence, a FastAPI
   service, a Next.js frontend, a quarantined extraction pipeline running a
   real local VLM, and a generative synthetic-world evaluation harness
   producing the accuracy/coverage/fairness numbers below.

Nothing in stage 2 changed stage 1's logic — same bug fixes, same property
tests, same rulepacks, just wrapped in the infrastructure the build spec
calls for.

## Run it

### Fastest path — no infrastructure at all

```bash
pip install -e ".[dev]"
python demo.py                       # end-to-end run over 8 hand-built scenarios
python -m pytest tests/ -v           # property tests (PT-1..PT-8) + adversarial suite
python evals/accuracy.py --n 340     # real accuracy vs. the generative world model
python evals/calibration.py --n 400  # real conformal coverage
python evals/fairness.py --n 500     # real A7 disparate-impact findings
```

Everything above runs in-process, no Docker, no GPU, no LLM.

### Full stack

```bash
docker compose up -d db redis minio
alembic upgrade head
python scripts/seed_demo.py          # populates seed_transaction with 120 synthetic cases
uvicorn arbiter.main:app --reload    # http://localhost:8000/docs
cd web && npm install && npm run dev # http://localhost:3000
```

Create and adjudicate a case:

```bash
curl -s -X POST http://localhost:8000/v1/disputes \
  -H "Idempotency-Key: demo-1" -H "Content-Type: application/json" \
  -d '{"transaction_id": "<a transaction_id from seed_transaction>", "reason_code": "F29"}'
curl -s -X POST http://localhost:8000/v1/cases/<case_id>/adjudicate
open http://localhost:3000/merchant/<case_id>
```

### Qwen2.5-VL extraction (optional, but real)

`arbiter.ingest.extract_vlm` calls a local Qwen2.5-VL model via
[Ollama](https://ollama.com), not a cloud API:

```bash
ollama pull qwen2.5vl:7b
ollama serve   # if not already running
python scripts/verify_vlm.py   # builds a synthetic document, runs the real model, prints extracted fields
```

Verified on this build (RTX 4060 Laptop, 8GB VRAM, 100% GPU offload):
~50s cold start (model load + first prompt eval), **~10s per page once warm**,
correctly extracting order number / address / date / tracking / amount
through the typed `ExtractionResult` schema. A disguised prompt-injection
payload embedded in the test document (near-white text reading "ignore all
previous instructions...") did not leak into the extracted fields — see
`tests/integration/test_adversarial.py::test_prompt_injected_document_never_reaches_a_predicate_the_engine_trusts`
for the structural version of that guarantee that doesn't require a live
model to run in CI.

If Ollama isn't running, extraction degrades to the native-PDF-text path
(`extract_native.py`, PyMuPDF) or PaddleOCR if installed — never crashes
(CLAUDE.md #9).

## Real measured numbers

All of these come from `evals/*.py`, run against the generative world model
(`datagen.world` → `datagen.observe`), not the hand-built demo scenarios.
Ground truth (`datagen.outcome.true_outcome`) is verified independent of the
rulepack by import-linter — see CLAUDE.md #5, the single most consequential
invariant in this repo.

**Accuracy** (`python evals/accuracy.py --n 340`, n≈1020 total):

| Reason code | Auto-resolve rate | Accuracy on decided | R13-equivalent recovery |
|---|---|---|---|
| F29 | 54.1% | 89.7% | 13 silent-merchant cases still won on the merits |
| C08 | 69.4% | 99.6% | 63 |
| C02 | 87.9% | 89.6% | 46 |

(C08's near-ceiling accuracy is a property of the domain, not a modeling
artifact left unexamined: real carrier delivery scans genuinely are a
near-authoritative signal. F29's number moved from a tautological ~100% to
89.7% after fixing a real bug — see "What this build found," below.)

**Conformal coverage** (`python evals/calibration.py --n 400`, target 95%,
α=0.05): F29 93.3%, C08 95.3%, C02 85.9%. C02 sits furthest from target,
honestly — its true_outcome function has more genuinely ambiguous
(`SPLIT`/`INSUFFICIENT_EVIDENCE`) ground truth than the binary conformal
gate has a category for, which the report calls out rather than hides.

**A7 fairness audit** (`python evals/fairness.py --n 500`), against the
generative model's planted, known-magnitude bias (merchant record-keeping
and ADEC adoption both correlate with `merchant_size_tier` —
`datagen.world._sample_merchant`): the audit finds real disparate impact in
C02 (e.g. `C02_R7` firing 69% for ENTERPRISE vs. 43-47% for
MICRO/SMALL/MID at equal evidence strength) and none in C08 at this sample
size — see the eval's stdout for the full table.

## What this build found and fixed

Three defects were found and fixed while the original in-memory core was
being property-tested (still true, still fixed — see `tests/property/`):

1. A trivial empty-set prime implicant in an early C08 rule, which fired on
   pure absence of evidence and revived the R03/R13 default-to-cardmember
   failure mode. Fixed by removing the rule.
2. The engine silently picking a winner (by dict-iteration order) when two
   outcomes' rules both fired on genuinely independent evidence. Fixed:
   `Engine.evaluate` now surfaces `conflicting_outcomes` and refuses to
   decide.
3. The referee being exploitable by omission — evaluating only
   advocate-cited facts let a true, rule-blocking fact silently drop out if
   no advocate happened to cite it. Fixed: the decision runs over the
   complete objective fact set; triple verification is an independent audit
   check, not a gate on what the decision sees.

A fourth was found while building the *generative* eval harness for this
production pass: `datagen.observe`'s F29 branch was asserting
`account_takeover_signal` and `cardholder_reported_lost_stolen` as **direct
copies** of the World's ground-truth boolean, rather than noisy detectors of
it — making `F29_R_ATO`'s accuracy against `true_outcome` tautologically
~100% by construction, not because the rule is good. Fixed in
`datagen/observe.py` and `datagen/world.py` (see git history) to model them
as imperfect proxies (high recall, small false-positive rate) — F29's
measured accuracy dropped from 100% to a much more credible 89.7% as a
direct result. Documented here rather than quietly re-tuned away, per the
same "state the generative assumptions explicitly" ethos this repo already
holds itself to.

## Architecture, mapped to the build spec

| Spec component | Module | Status |
|---|---|---|
| C1-C5 (five hard rules) | throughout, see `CLAUDE.md` | enforced by import-linter + code |
| A1 ADEC | `arbiter.provenance` (+ `sdk/arbiter_commit.py`) | real RFC 6962 Merkle log, Ed25519-signed STHs, RFC-3161-style TSA, commit/reveal/verify HTTP routes |
| A2 Dual-Advocate | `arbiter.advocate` | deterministic prime-implicant search (the mandatory rules-only fallback *is* the default runner — no LLM advocate wired in this pass, by design; see CLAUDE.md "prefer") |
| A3 Referee | `arbiter.horn`, `arbiter.decision.adjudicate` | pure propositional Horn forward chaining; stdlib-only, verified by import-linter |
| A4 Counterfactual Ledger | `arbiter.horn.counterfactual` | one `minimal_delta` function, five product surfaces |
| A5 Conformal Abstention | `arbiter.decision.conformal` | Mondrian split-conformal, per-reason-code, pooled fallback under n<100 |
| A6 Contradiction detection | `arbiter.evidence.{temporal,numeric,identity,semantic}` | Allen interval algebra, numeric reconciliation, identity coherence, semantic-claim polarity |
| A7 Fairness audit | `arbiter.fairness` | propensity-stratified firing-rate deltas |
| A8 Quarantined extraction | `arbiter.ingest` | scan → forensics → native(PyMuPDF)/OCR(optional PaddleOCR)/VLM(Qwen2.5-VL via Ollama, verified running) |
| Task 1 — auto-collect evidence | `arbiter.network` | reads a synthetic ledger stand-in (`SeedTransaction`); swapping in a real ledger is a boundary change at `network.loader` only |
| Task 3 — real-time tracking | `arbiter.realtime`, `web/components/StatusStream.tsx` | SSE over Redis pub/sub |
| Task 4 — transparent reasoning | `arbiter.narrate`, `web/app/merchant/[caseId]` | proof tree + grounded narration + counterfactuals, all clickable to evidence |
| Task 5 — eval harness | `evals/{accuracy,calibration,fairness}.py` | real numbers, see above |

## What's deliberately not (fully) built here

Stated honestly, matching this repo's own established practice:

- **No LLM advocate.** `arbiter.advocate.runner` is the deterministic
  prime-implicant search — which is also the required rules-only fallback,
  so keeping it as the default is a correctness choice, not a shortcut.
  Wiring a real LLM advocate behind the same `ArgumentGraph` contract
  (`arbiter.advocate.contract`) is a bounded follow-up, not a redesign.
- **No LLM narration.** `arbiter.narrate.llm.render_llm_narration` always
  returns `None` (falls back to the template renderer) — a deliberate stub,
  not a broken one; see that module's docstring.
- **PaddleOCR is optional**, not installed by default (heavy dependency);
  `extract_ocr.is_available()` gates it and the router falls through to VLM.
- **ClamAV is stubbed** in `arbiter.ingest.scan` — the one boundary check
  this build does not perform for real, documented in that module.
- **No real RFC 3161 TSA** — `arbiter.provenance.tsa` is a documented,
  interface-compatible Ed25519 stand-in (see that module's docstring for
  exactly what property it preserves and what it doesn't bother
  DER-encoding).
- **Full Error-Level-Analysis (ELA)** for spliced-image detection is not
  implemented — `datagen.adversarial.spliced_receipt`'s docstring says so
  explicitly; only perceptual-hash template-reuse detection is wired.
- **The `docker-compose.yml` stack was written and its migration verified
  offline** (`alembic upgrade head --sql` renders correct Postgres DDL) but
  not run live end-to-end in this pass — left for you to bring up when
  ready; see "Full stack," above.

## Repository layout

See [`CLAUDE.md`](CLAUDE.md) for the invariants, and
[`rulepacks/README.md`](rulepacks/README.md) for the rulepack format.

```
src/arbiter/     the application: horn (pure) -> evidence -> decision -> api,
                 plus provenance/advocate/narrate/audit/fairness/network/ingest/realtime/db
rulepacks/amex/  F29, C08, C02 -- data, not code
datagen/         world.py/outcome.py (rulepack-independent, linter-enforced) + observe.py/documents.py/adversarial.py
evals/           accuracy.py, calibration.py, fairness.py -- real numbers against datagen
tests/           property/ (PT-1..PT-8), integration/ (adversarial suite)
sdk/             arbiter_commit.py -- the entire merchant-side ADEC integration surface
web/             Next.js merchant console, card member portal, review page, fairness dashboard
alembic/         schema migration (verified via offline SQL render)
scripts/         seed_demo.py, verify_vlm.py
```
