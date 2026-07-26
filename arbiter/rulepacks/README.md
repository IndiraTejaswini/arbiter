# Rulepacks

Rulepacks are **data, not code** (CLAUDE.md invariant #7). Each file here is a
content-addressed YAML document describing one Amex reason code's decision
logic as propositional Horn clauses. `arbiter.rulepack.loader` is the only
module that ever interprets this YAML; `arbiter.horn` never sees a `.yaml`
file, only the parsed `RulePack` dataclass.

See [`predicates.schema.json`](predicates.schema.json) for the structural
schema.

## Loaded rulepacks

| File | Reason code | Rules | What it adjudicates |
|---|---|---|---|
| [`amex/F29.card-not-present.yaml`](amex/F29.card-not-present.yaml) | F29 | 10 | Card-not-present fraud. Encodes Visa's Compelling Evidence 3.0 matching rule faithfully (`F29_CE3_*`), plus ARBITER's own generalisation that drops CE3.0's 120-365-day floor when ADEC proves non-backdating directly (`F29_R_ADEC_GENERALIZED`) — see that rule's `description` for why. |
| [`amex/C08.goods-not-received.yaml`](amex/C08.goods-not-received.yaml) | C08 | 6 | Goods/services not received. Merchant's defense is proof of delivery, digital-goods access logs, or a card member's own too-late cancellation. |
| [`amex/C02.credit-not-processed.yaml`](amex/C02.credit-not-processed.yaml) | C02 | 8 | Credit not processed. Mostly numeric reconciliation: was a refund issued, for the right amount, before the window closed. |

## Content addressing

`RulePack.content_hash()` (`arbiter.horn.clause.RulePack`) hashes the
*parsed* rule structure — `rulepack_id`, `reason_code`, `version`, every
rule's `rule_id`/`head`/`body`, and `decision_predicates` — not the YAML
text. Formatting changes (whitespace, comments, key reordering) never change
a rulepack's hash or invalidate a decision that pinned it
(`decision.rulepack_hash` in the DB schema). Changing a rule's logic always
does.

## Adding a new rulepack

1. Write the YAML (see `predicates.schema.json` for the shape, and any
   existing file in `amex/` for the actual authoring style — in particular,
   read `C08.goods-not-received.yaml`'s trailing comment on why there is
   deliberately no "merchant produced nothing" rule keyed on absence of
   evidence).
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
