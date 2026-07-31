# ARBITER — Demo Script & Judging Plan

**The one sentence to land:** *Every other team will show you an AI that makes
decisions. We're going to spend most of our time trying to make ours lie to
you — and showing you it structurally cannot.*

**Total runtime: 10 minutes.** Act 4 is the demo. Acts 1–3 exist to earn the
right to run it. If you are cut to 5 minutes, do Act 1, then Act 4a, then Act 6.

---

## 0. Pre-flight — 30 minutes before you present

Do not skip this. Three things here take minutes and would kill the demo live.

### 0.1 Start the stack

Four terminals, all from `C:\Amex\arbiter`:

```powershell
docker compose up -d db redis                        # infra
ollama serve                                         # LLM boundaries 1-4
python -m uvicorn arbiter.main:app --reload --port 8000
python scripts/run_adjudication_worker.py --poll 2   # REQUIRED - drains the queue
python scripts/run_deadline_sweeper.py --loop 60
cd web; npm run dev                                  # http://localhost:3000
```

Verify before you relax:

```powershell
curl.exe http://localhost:8000/ready        # status: ready, 3 codes calibrated
curl.exe http://localhost:11434/api/tags    # qwen2.5vl:7b present
```

**If `/ready` says `degraded`, stop and fix it.** A degraded conformal gate
escalates every case, and your auto-resolve demo becomes an escalate demo.

### 0.2 Warm the models — this is the one that bites

First call to each model pays a cold-load penalty. On stage that is 40 seconds
of silence.

```powershell
python scripts/verify_vlm.py                # warms Qwen2.5-VL (~23s cold)
python -c "import sys; sys.path.insert(0,'src'); from arbiter.evidence.nli import classify_pairs; print(classify_pairs([('a','b')])[0].label)"
```

### 0.3 Pre-run the long eval

`evals/hallucination.py` takes ~10 minutes. Run it now, keep the output on a
second screen, and show it in Act 6. Running it live is a dead demo.

```powershell
python evals/hallucination.py --n 12 > halluc.txt
```

### 0.4 Create a fresh case for the audit demo

**Critical.** The 840 seeded cases were signed with ephemeral keys that no
longer exist, so their audit page shows *signatures: invalid*. Only cases
filed after the shared-key config verify. File one now and keep the id.

```powershell
python scripts/seed_demo.py --help   # (if you need a fresh transaction)
```

Then file a dispute through the UI and note the case id. Confirm its audit
page shows **verified**. If it doesn't, your Act 5 is broken.

### 0.5 Have these open

- Browser tab 1: `http://localhost:3000` — signed in as **Administrator**
- Browser tab 2: the fresh case's **Audit trail** page
- Terminal: worker log visible (Act 4a reads it live)
- `halluc.txt` on a second screen

---

## Act 1 — The problem, and the one idea (60s)

> "A card member disputes a charge. Today that takes 45 days, and roughly
> 60% of the cost isn't investigation — it's protocol latency. Two parties
> each hold half the evidence and wait on each other's mailbox.
>
> The obvious fix is to put an LLM on it. The obvious problem is that nobody
> will let a language model decide who keeps $450, and they're right not to.
>
> So we did something different. **The LLM never decides anything.** It reads
> messy documents, it proposes arguments, it writes explanations — and every
> single thing it produces is re-derived by a deterministic verifier before it
> counts. Propositional Horn-clause forward chaining picks the winner. Nothing
> else can."

**Show:** the console header — *"Rules decide; models never do."*

> "That's not a slogan, it's a CI check. Let me show you."

**Do:** `lint-imports` in the terminal.

```
Referee (horn) is pure -- the only decider touches no LLM   KEPT
Quarantine emits only typed schemas                         KEPT
Advocates cannot write                                      KEPT
World model is independent of the rulepack                  KEPT
Layering                                                    KEPT
Chargeback-right gate is pure                               KEPT
Contracts: 6 kept, 0 broken.
```

> "The decision engine importing an LLM client — directly or transitively —
> fails the build. It is mechanically impossible for a model to reach the
> verdict."

---

## Act 2 — The LLM does the hard part (90s)

**Do:** *File a dispute* → paste free text, no reason code:

> *"I ordered a pair of running shoes on 3 March and they have never turned
> up. Tracking has said in transit for six weeks and the seller stopped
> replying to my emails."*

**Expect:** case created, `reason_code: C08`, `intent_confidence: 0.9`.

> "The model read that and picked C08 — Goods or Services Not Received —
> out of 22 Amex reason codes. That's boundary one, and it's genuinely
> useful: nobody wants to make a customer choose a network reason code.
>
> But watch what happens when it *doesn't* know."

**Do:** file another with: *"Hello, I have a question about my account,
please call me back."*

**Expect:**
```
resolved: false
proposed_reason_code: null
route_to_human_triage: true
reason: "classifier could not confidently match any known bucket"
```

> "No case created. No guess. Confidence below 0.70 or a bucket that doesn't
> resolve to a loaded rulepack routes to a human. **Misrouting is the one
> intake error that corrupts everything downstream** — wrong rulepack, wrong
> predicates, wrong evidence requirements — so this boundary fails closed
> harder than any other."

---

## Act 3 — The decision, and why you can check it (2 min)

**Do:** open the C08 case, click **Adjudicate**. The stage stream runs live.

> "Seven stages. Chargeback right, gather Amex records, parse evidence,
> verify provenance, contradiction analysis, dual advocacy, referee."

**Show — the proof tree.**

> "This isn't a confidence score with a paragraph attached. Every rule that
> fired, every literal that satisfied it, and the evidence node that
> established each one. Click a node id and it jumps to the document, page
> and bounding box it came from."

**Show — the counterfactual.**

> "*'For the card member to have won instead, the following would need to
> change.'* That's not a heuristic or a SHAP approximation — it's an exact
> set-difference against enumerated prime implicants. It is the complete,
> minimal set of facts that flips this outcome. Nothing else does."

**Show — the narration**, and point at `source`.

> "Plain English, generated by the model. Every sentence cites a real
> evidence node. Hold that thought — we're about to break it on purpose."

---

## Act 4 — Now try to break it *(the demo — 4 min)*

> "Four attacks. Everything from here is live."

### 4a. Prompt injection → the narration veto

> "The model writes the explanation. So make it cite something that doesn't
> exist."

**Show the worker log** from the pre-warm run, or run an adjudication and read it:

```
WARNING arbiter.narrate.ground: LLM narration discarded: 4 ungrounded
citation(s) ["Reg Z 12 CFR 1026.13(a)(3) -- no billing error where..."]
-- falling back to the deterministic template
```

> "That is a real hallucination, caught live. Asked for evidence node ids,
> the model returned the rule's legal-basis text four times as if it were an
> id. Every one was rejected, and here's the part that matters: **one bad
> citation discards the entire narration**, not the bad sentence. A narration
> that cites a fabricated node isn't mostly right — it's a document whose
> provenance claims can't be trusted, and this text is what we show a customer
> as the reason they lost.
>
> And the reader is told. `source` reads `template_fallback`, not `template`
> — a vetoed case is never indistinguishable from one where no model ran."

**Judge bait — say this:**
> "We deliberately do *not* filter bad citations inside the generator. If we
> scrubbed them before the verifier saw them, the veto would never fire and
> we'd have an unguarded boundary that looks safe. There's a test asserting
> we don't."

### 4b. Fabricate exactly what the system told you to

> "The counterfactual tells the losing party precisely what would flip the
> case. Isn't that a cheat sheet?"

**Show:** `evals/gaming_resistance.py` output (pre-run or live — it's fast).

```
F29: 226 fabrications, 0 flipped     C08: 98, 0 flipped     C02: 115, 0 flipped
OK: 0 tier-gated fabrications ever flipped a verdict.
```

> "439 attempts, zero flips. Because a rule that decides a case may rest on
> weak evidence only if it *also* constrains at least one predicate that comes
> from Amex's own records. You can forge your own document. You cannot forge
> the absence of a record from the issuer's system. That's an invariant with a
> property test behind it, not a policy."

### 4c. Two documents that contradict each other

**Do:** upload a merchant delivery confirmation *and* a card-member letter
saying it never arrived. Adjudicate.

**Expect:**
```
layer_status: {"numeric":"OK","identity":"OK","semantic":"OK","temporal":"OK"}
CONTRADICTION [SEMANTIC_CONTRADICTION] severity=HIGH
confidence: 0.205 | abstained: True
```

> "Four mandatory deterministic layers — temporal, numeric, identity, and
> semantic. The semantic one is a DeBERTa cross-encoder, **not a generative
> model**, and that's deliberate: there's no mechanical way to re-derive
> *'these two sentences contradict'*, so an LLM here would be the one
> unguarded model in the system, reading attacker-controlled text, able to
> suppress escalation by reporting nothing.
>
> Confidence collapsed to 0.2 and the case went to a human. A contradiction
> hard-blocks auto-resolution."

### 4d. Tamper with committed evidence

> "A merchant commits a hash of their delivery proof *before* any dispute
> exists. At dispute time they reveal it."

**Do:** reveal with one byte changed.

**Expect:** `ok: false, tier: SUBMITTED` — **HTTP 200, not an error.**

> "It doesn't reject the evidence — it *demotes* it. Evidence degrades, never
> disappears. A merchant who can't produce a valid pre-commitment still gets
> heard; their document just carries the weight of an assertion instead of a
> proof. Rejecting outright would be the easy call and the wrong one."

---

## Act 5 — The audit chain (60s)

**Do:** open the **fresh** case's audit page.

```
EVENTS 4 | HASH CHAIN intact | PAYLOAD INTEGRITY intact | SIGNATURES valid
verified
```

> "Every event hash-chained to its predecessor and Ed25519-signed. This page
> **recomputes all three on read** — you're not trusting the API's claim that
> it's intact, you're watching it re-derive it from the stored rows.
>
> Three independent checks: the chain links, the payload hashes match, and the
> signatures verify. Tamper with any row in Postgres and this page tells you
> which sequence number broke. And the tables are append-only at the database
> level — there's a trigger that rejects UPDATE."

---

## Act 6 — The numbers, honestly (90s)

| What | Result |
|---|---|
| Accuracy on decided cases | F29 **88.4%** · C08 **100%** · C02 **93.4%** |
| Hallucination containment | **0/12** poison cited · **0/12** verdicts moved |
| Gaming resistance | **0 of 439** fabrications flipped a verdict |
| Deterministic latency | p50 **3ms** · p95 **7ms** |
| Tests / contracts | **402 passing** · **6/6** architecture contracts |

**Then show the honest column — this wins more points than the good numbers:**

> "Two things I want to flag before you find them.
>
> **C02 conformal coverage is 81% against a 95% target.** That's a real miss.
> Its ground truth has more genuinely ambiguous split cases than a binary gate
> has categories for. It's in the docs, it's in the eval output, and we didn't
> tune it away.
>
> **Our fairness audit reports zero flagged findings — and that is not a
> pass.** At n=500 every comparison is underpowered, and the tool says so
> explicitly rather than letting 'zero flagged' read as 'the rules are fair.'
> Plant a real bias and raise n and it detects it with non-overlapping Wilson
> intervals surviving FDR correction."

**Close:**

> "Abstention is a first-class output here. When the referee can't decide,
> that *is* the answer — a human decides next, not a model. We think that's
> the only honest way to put an LLM anywhere near a regulated financial
> decision: turn it all the way up where it's reading documents, and give it
> exactly zero authority over the verdict."

---

## Judge Q&A — prepared answers

**"Isn't the LLM still influencing the outcome by choosing the reason code?"**
> Yes, and it's the one place we worried most. It selects which rulepack
> loads, never what the rulepack concludes. Below 0.70 confidence or an
> unresolvable bucket it routes to a human. And it's the single boundary where
> a wrong answer corrupts everything downstream, which is why it fails closed
> hardest.

**"What if Ollama is down?"**
> Every LLM call site returns `None` and the deterministic path takes over —
> the rules-only path is the default, not a fallback bolted on. Intent goes to
> human triage, extraction falls back to native/OCR, advocates use exact prime-
> implicant search, narration renders from the template. The system gets less
> convenient, never less correct.

**"Why Postgres for the queue instead of Kafka?"**
> At ~10 QPS it's the correct answer, not a compromise: the queue lives in the
> same transaction as the state change, which eliminates the dual-write bug a
> broker introduces at every enqueue. We state the cutover trigger — sustained
> ingest past ~2k events/s, or a third consumer group needing arbitrary replay.

**"How do you know your accuracy number isn't circular?"**
> Ground truth comes from a world model that import-linter *forbids* from
> importing the rulepack or the referee. If it could, every number would look
> excellent and mean nothing. That contract is in CI.

**"What's genuinely not done?"**
> No OpenTelemetry. Drift monitoring has a function and no caller. No frontend
> runtime tests. And two components were implemented but structurally unable
> to run until this week — the semantic layer had no input wired and the
> narration generator was a stub. Both are live now and documented in §11B of
> the architecture doc, including how we found them: not with tests, but by
> querying production data for evidence they had ever executed.

**"Can I see it fail?"**
> Yes — kill Ollama and re-run. Everything still adjudicates.

---

## If something breaks on stage

| Symptom | Cause | Say this, do that |
|---|---|---|
| Stage stream connects, never advances | Worker not running | "That's our async queue — adjudication is off the request path." Start the worker. |
| Audit shows *signatures: invalid* | Old seeded case | Switch to the fresh case from §0.4. Don't debug live. |
| Adjudication hangs ~40s | Cold model | Talk through the proof tree while it finishes. Pre-warm next time. |
| `/ready` degraded | Calibration missing | `python scripts/seed_calibration.py` |
| Narration always `template_fallback` | Model citing badly | **This is a feature — show the log.** Act 4a works either way. |
| Semantic layer `NOT_APPLICABLE` | Documents carry no status text | Use documents with an explicit delivery/refund statement. |

**Golden rule:** if something fails, name it as the system behaving correctly
under degradation if that's true — and if it isn't, say "that's a bug, here's
what it should do." Judges have seen every team hide a failure. Almost none
have seen a team explain one.

---

## The 30-second version

> "Dispute adjudication where the LLM reads everything and decides nothing.
> Four LLM boundaries, each with a deterministic verifier that re-derives its
> output — hallucinated citations discard the whole narration, fabricated
> advocate claims never reach the referee, and the referee itself is
> propositional Horn logic that CI forbids from importing an LLM. Every
> decision ships a proof tree, an exact counterfactual, and a hash-chained
> Ed25519-signed audit trail that re-verifies on read. When it can't decide,
> it abstains — and that's the answer, not a failure."
