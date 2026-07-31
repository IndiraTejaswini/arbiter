# Rulepacks

Rulepacks are **data, not code** (CLAUDE.md invariant #7). Each file here is a
content-addressed YAML document describing one Amex reason code's decision
logic as propositional Horn clauses. `arbiter.rulepack.loader` is the only
module that ever interprets this YAML; `arbiter.horn` never sees a `.yaml`
file, only the parsed `RulePack` dataclass.

See [`predicates.schema.json`](predicates.schema.json) for the structural
schema.

## Loaded rulepacks

| File | Reason code | Amex network code | Rules | What it adjudicates |
|---|---|---|---|---|
| [`amex/F29.card-not-present.yaml`](amex/F29.card-not-present.yaml) | F29 | 4540 | 10 | Card-not-present fraud. Encodes Visa's Compelling Evidence 3.0 matching rule faithfully (`F29_CE3_*`), plus ARBITER's own generalisation that drops CE3.0's 120-365-day floor when ADEC proves non-backdating directly (`F29_R_ADEC_GENERALIZED`) — see that rule's `description` for why. |
| [`amex/C08.goods-not-received.yaml`](amex/C08.goods-not-received.yaml) | C08 | 4554 | 6 | Goods/services not received. Merchant's defense is proof of delivery, digital-goods access logs, or a card member's own too-late cancellation. |
| [`amex/C02.credit-not-processed.yaml`](amex/C02.credit-not-processed.yaml) | C02 | 4513 | 8 | Credit not processed. Mostly numeric reconciliation: was a refund issued, for the right amount, before the window closed. |

The **network code** column is the four-digit code the published Amex
merchant chargeback guide uses, and the one a merchant reads off their own
"Resolve Disputes" screen. `RulepackRegistry.resolve()` accepts either
dialect and fails closed on anything else, so a caller holding a real code
from a real Amex screen can file with it.

## Content addressing

`RulePack.content_hash()` (`arbiter.horn.clause.RulePack`) hashes the
*parsed* rule structure — `rulepack_id`, `reason_code`, `version`, every
rule's `rule_id`/`head`/`body`, `decision_predicates`, and the
`chargeback_right` block's operative fields — not the YAML text. Formatting
changes (whitespace, comments, key reordering) never change a rulepack's
hash or invalidate a decision that pinned it (`decision.rulepack_hash` in
the DB schema). Changing a rule's logic always does, and so does changing an
exclusion or a filing window — those can end a case, so a decision has to be
replayable against the exact text that produced it. Prose (`description`,
`legal_basis`, `source`) is excluded throughout: prose about a check is not
the check. A rulepack that declares no `chargeback_right` keeps the hash it
had before the block existed.

## The chargeback right (`chargeback_right:`)

Every reason code in the Amex guide carries two fields that are **not**
about the evidence:

    Maximum time a dispute can be raised   120 days from the date American
                                           Express Network processed the
                                           Transaction (4554 adds an
                                           alternate clock capped at 540)
    Excluded Transactions                  "Card Present Transactions",
                                           "Transactions that qualify for
                                           American Express SafeKey Fraud
                                           Liability Shift", ...

Both remove the chargeback right outright. `arbiter.eligibility` evaluates
them **before** the referee; when the gate closes, no evidence is loaded, no
advocate runs, no rule is evaluated, and the outcome is
`CHARGEBACK_INELIGIBLE` — deliberately not `MERCHANT_PREVAILS`, because no
evidence was weighed and counting it as a merchant win would corrupt win
rates, the fairness layer's per-rule disparate-impact analysis, and the
conformal calibration pool alike.

Three things about authoring one:

- **Conditions come from a closed vocabulary.** Every `attribute` must be a
  name in `arbiter.eligibility.models.ATTRIBUTE_VOCABULARY`, with a declared
  type and a stated source. There is no expression language, and that is a
  security property rather than a limitation: an evaluator here would be a
  code path from a YAML file to the interpreter. A name outside the
  vocabulary fails the API's boot, so a typo can never become an exclusion
  that quietly never fires.
- **Unknown fails OPEN, uniquely in this codebase.** An attribute the ledger
  did not supply cannot fire an exclusion and cannot breach a window. Every
  other gate here fails closed for the card member's protection; an
  exclusion firing *removes* their dispute right, so the conservative
  direction reverses. The unknown is recorded in
  `EligibilityResult.undetermined` and emitted as a
  `CHARGEBACK_RIGHT_UNDETERMINED` case event instead.
- **`exclusions: []` is a statement.** It means the guide was read and says
  "Excluded Transactions: None" (as 4513 does). Omitting the key entirely
  means nobody has transcribed the reason code yet — and
  `tests/property/test_rulepacks.py::test_every_rulepack_declares_a_chargeback_right`
  will say so.

## Threshold rule bodies (`at_least:`)

The guide writes compelling evidence as N-of-M throughout — RC 4540 asks for
"three (3) or more of the following" (p.19) and "at least two (2) of the
following items" (p.20), and Visa CE3.0 is "2 of 4, one of which must be
device_id or ip_address". Authoring those by hand is where a rulepack drops
a combination, and a dropped combination is a merchant losing a case the
rules say they win.

```yaml
body:
  - device_id_match
  - at_least:
      n: 1
      of: [ip_address_match, shipping_address_match, user_id_match]
```

`arbiter.rulepack.loader` expands this at **load time** into one ordinary
conjunctive Horn clause per combination (`R1#1`, `R1#2`, …), so
`arbiter.horn` never learns the concept exists — prime-implicant enumeration
stays enumeration over a finite literal set, and the counterfactual ledger
can still say "make exactly these literals true". Expansion is capped at 128
clauses per rule; past that, the rulepack should say what it means as
separate rules.

## Adding a new rulepack

1. Write the YAML (see `predicates.schema.json` for the shape, and any
   existing file in `amex/` for the actual authoring style — in particular,
   read `C08.goods-not-received.yaml`'s trailing comment on why there is
   deliberately no "merchant produced nothing" rule keyed on absence of
   evidence, and its `chargeback_right` comment on the three ways a filing
   window transcription goes wrong).
1a. Transcribe the reason code's `chargeback_right` block from the guide in
   the same change — its filing window and its "Excluded Transactions" list.
   A rulepack with rules but no gate silently passes every dispute under
   that code, including ones the network gives no chargeback right for; the
   property tests fail the rulepack rather than let that ship.
2. Run `python -m pytest tests/property/ -v` — the property tests
   (`PT-1..PT-8` from the build spec) run against every rulepack in this
   directory automatically; a new file gets covered for free.
3. `arbiter.rulepack.registry.RulepackRegistry.load_dir` validates
   (`arbiter.rulepack.validate.validate_rulepack`) every rulepack at
   startup — a malformed one fails the API's boot, not a random request
   later.

## What "negation" means here

`not <predicate>` in a rule body means "the graph does not currently
establish `<predicate>` as TRUE" — closed-world assumption, restricted to
EDB (evidence-derived) predicates only. A rule can never negate another
rule's head; `arbiter.rulepack.validate` (via `arbiter.horn.chain._stratify`)
rejects that at load time as a `StratificationError`. See
`arbiter.horn.clause`'s module docstring for the full reasoning.
