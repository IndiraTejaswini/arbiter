import { useState } from "react";
import {
  Async, Badge, Button, Card, CardBody, CardHeader, Disclosure, EmptyState,
  ErrorState, Mono, PageHeader, TextInput,
} from "@/components/ui";
import { api } from "@/lib/api";
import { cx } from "@/lib/format";
import type { RulepackResponse } from "@/lib/types";
import { useApi } from "@/lib/useApi";

/**
 * Rulepack inspector.
 *
 * Reviewer/admin only, and that restriction is deliberate rather than
 * incidental: this returns the complete decision function. Combined with
 * the counterfactual ledger — which tells a party the exact minimal fact
 * set that flips their outcome — anonymous access to the rulepack would be
 * the full toolkit for targeting a specific decision path with fabricated
 * evidence. Rulepack transparency is still real: it is disclosed to
 * compliance, auditors, and regulators, who hold this role. It is not
 * disclosed to the parties to a live dispute.
 *
 * Rulepacks are content-addressed. Every decision pins the hash of the
 * rulepack that produced it, so a decision made months ago can be replayed
 * against exactly the rules that produced it.
 */
export default function RulepacksPage() {
  const [hash, setHash] = useState("");
  const [submitted, setSubmitted] = useState<string | null>(null);

  const pack = useApi<RulepackResponse>(
    () => api.getRulepack(submitted!),
    [submitted],
    { enabled: Boolean(submitted) },
  );

  return (
    <>
      <PageHeader
        eyebrow="Compliance"
        title="Rulepack registry"
        description="Rulepacks are data, never code. Each is content-addressed, and every decision pins the hash that produced it — so any decision is replayable against exactly the rules in force when it was made."
      />

      <Card className="mb-5">
        <CardBody>
          <form
            className="flex flex-col space-y-4"
            onSubmit={(e) => { e.preventDefault(); setSubmitted(hash.trim().replace(/^sha256:/, "")); }}
          >
            <div className="w-full">
              <label htmlFor="hash" className="block text-[11px] font-bold uppercase tracking-[0.1em] text-foreground/80 mb-2">
                Rulepack content hash
              </label>
              
              <div className="flex flex-col sm:flex-row items-stretch gap-4">
                <div className="flex-1">
                  <TextInput
                    id="hash"
                    mono
                    value={hash}
                    onChange={setHash}
                    placeholder="sha256:a1b2c3…"
                  />
                </div>
                <Button type="submit" variant="primary" disabled={!hash.trim()} className="shrink-0 px-8">
                  Load rulepack
                </Button>
              </div>
              
              <p className="mt-2 text-[11px] font-medium leading-relaxed text-muted-foreground/90">
                Copy it from any decision — the case detail page shows it under 'Rulepack'. The sha256: prefix is optional.
              </p>
            </div>
          </form>
        </CardBody>
      </Card>

      {!submitted ? (
        <Card>
          <CardBody>
            <EmptyState
              title="No rulepack loaded"
              description="Paste a content hash above. The registry never mutates or replaces a hash's rules in place, so a hash always resolves to the same decision function."
            />
          </CardBody>
        </Card>
      ) : pack.error ? (
        <ErrorState error={pack.error} onRetry={pack.reload} />
      ) : (
        <Async state={pack} label="rulepack">
          {(data) => <RulepackDetail pack={data} />}
        </Async>
      )}
    </>
  );
}

function RulepackDetail({ pack }: { pack: RulepackResponse }) {
  const outcomeOf = new Map(Object.entries(pack.decision_predicates).map(([o, head]) => [head, o]));

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader
          title={
            <>
              {pack.rulepack_id} <Badge className="ml-1">v{pack.version}</Badge>
              {pack.network_code && (
                <Badge
                  className="ml-1"
                  title="The four-digit code the Amex chargeback guide and a merchant's own 'Resolve Disputes' screen use for this same dispute. The registry accepts either dialect."
                >
                  Amex {pack.network_code}
                </Badge>
              )}
            </>
          }
          subtitle={<>Reason code {pack.reason_code} · <Mono>{pack.content_hash.slice(0, 32)}…</Mono></>}
        />
        <CardBody>
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <h3 className="mb-1.5 text-2xs font-semibold uppercase tracking-wide text-neutral-500">
                Decision predicates
              </h3>
              <ul className="space-y-1">
                {Object.entries(pack.decision_predicates).map(([outcome, head]) => (
                  <li key={outcome} className="text-xs">
                    <span className="font-medium text-neutral-800 dark:text-neutral-200">{outcome}</span>
                    <span className="mx-1.5 text-neutral-400">→</span>
                    <Mono>{head}</Mono>
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <h3 className="mb-1.5 text-2xs font-semibold uppercase tracking-wide text-neutral-500">
                Predicate schema ({pack.predicate_schema.length})
              </h3>
              <div className="flex flex-wrap gap-1">
                {pack.predicate_schema.map((p) => (
                  <Badge key={p}><Mono>{p}</Mono></Badge>
                ))}
              </div>
            </div>
          </div>
        </CardBody>
      </Card>

      {pack.chargeback_right && <ChargebackRightSection right={pack.chargeback_right} />}

      <Card>
        <CardHeader
          title={`Rules (${pack.rules.length})`}
          subtitle="Each rule cites the regulatory provision it encodes — or states plainly where it is ARBITER's own evidentiary logic rather than a codified rule."
        />
        <CardBody>
          <ul className="space-y-3">
            {pack.rules.map((rule) => {
              const outcome = outcomeOf.get(rule.head);
              return (
                <li
                  key={rule.rule_id}
                  className="rounded-lg border border-neutral-200 p-3 dark:border-neutral-800"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <Mono className="font-semibold text-neutral-800 dark:text-neutral-200">
                      {rule.rule_id}
                    </Mono>
                    <span className="text-xs text-neutral-400">→</span>
                    <Mono>{rule.head}</Mono>
                    {outcome && (
                      <Badge className={cx(
                        outcome.includes("MERCHANT")
                          ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"
                          : outcome.includes("CARD_MEMBER")
                          ? "bg-rose-100 text-rose-800 dark:bg-rose-950 dark:text-rose-300"
                          : "bg-yellow-100 text-yellow-900 dark:bg-yellow-950 dark:text-yellow-300",
                      )}>
                        decides {outcome}
                      </Badge>
                    )}
                  </div>

                  <div className="mt-2 flex flex-wrap items-center gap-1.5">
                    {rule.body.map((literal, i) => (
                      <span key={i} className="flex items-center gap-1.5">
                        {i > 0 && <span className="text-2xs text-neutral-400">∧</span>}
                        <Badge className={literal.startsWith("not ")
                          ? "bg-orange-100 text-orange-900 dark:bg-orange-950 dark:text-orange-300"
                          : "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-300"}>
                          <Mono className="text-inherit">{literal}</Mono>
                        </Badge>
                      </span>
                    ))}
                  </div>

                  {rule.description && (
                    <p className="mt-2 text-xs leading-relaxed text-neutral-600 dark:text-neutral-400">
                      {rule.description}
                    </p>
                  )}

                  {rule.legal_basis && (
                    <div className="mt-2">
                      <Disclosure summary="Legal basis">
                        <p className="text-2xs leading-relaxed text-neutral-600 dark:text-neutral-400">
                          {rule.legal_basis}
                        </p>
                      </Disclosure>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        </CardBody>
      </Card>
    </div>
  );
}

/**
 * The pre-referee gate, disclosed under the same reasoning as the rules.
 *
 * The API has returned this block since the gate shipped and this page
 * rendered none of it, which left the inspector showing the less
 * consequential half of the decision function: an auditor could read every
 * rule body but not the conditions under which no rule runs at all. An
 * exclusion ends a dispute outright — it is strictly more decisive than any
 * rule below it.
 */
function ChargebackRightSection({ right }: { right: NonNullable<RulepackResponse["chargeback_right"]> }) {
  return (
    <Card>
      <CardHeader
        title="Chargeback right — the gate before the rules"
        subtitle="Amex's guide gives every reason code a filing window and an 'Excluded Transactions' list. Both remove the right to charge a merchant back, prior to the merits — when either fires, no evidence is loaded, no advocate runs, and the referee is never called."
        actions={
          <Badge title="How long the merchant has to challenge, from the Amex Central Site Business Date.">
            merchant challenge {right.merchant_challenge_days}d
          </Badge>
        }
      />
      <CardBody className="space-y-5">
        <section>
          <h3 className="mb-2 text-2xs font-semibold uppercase tracking-wide text-neutral-500">
            Maximum time a dispute can be raised ({right.filing_window.length})
          </h3>
          <ul className="space-y-2">
            {right.filing_window.map((branch) => (
              <li key={branch.branch_id} className="rounded-lg border border-neutral-200 p-3 dark:border-neutral-800">
                <div className="flex flex-wrap items-center gap-2">
                  <Mono className="font-semibold text-neutral-800 dark:text-neutral-200">
                    {branch.branch_id}
                  </Mono>
                  <Badge className="bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-300">
                    {branch.days} days
                  </Badge>
                  {branch.absolute_cap_days !== null && (
                    <Badge
                      className="bg-orange-100 text-orange-900 dark:bg-orange-950 dark:text-orange-300"
                      title="An absolute ceiling on this clock. Without it an awareness-anchored window is unbounded: a card member becoming 'aware' three years later would file inside a window that started three years after the charge."
                    >
                      capped at {branch.absolute_cap_days}d from {branch.cap_from}
                    </Badge>
                  )}
                </div>
                <p className="mt-1.5 text-2xs text-neutral-500 dark:text-neutral-400">
                  measured from{" "}
                  {branch.from.map((attribute, i) => (
                    <span key={attribute}>
                      {i > 0 && <span className="mx-1 text-neutral-400">or</span>}
                      <Mono>{attribute}</Mono>
                    </span>
                  ))}
                  {branch.from.length > 1 && (
                    <span className="ml-1 italic">
                      — whichever occurred first, so the EARLIEST known anchor governs
                    </span>
                  )}
                </p>
                {branch.description && (
                  <p className="mt-1.5 text-xs leading-relaxed text-neutral-600 dark:text-neutral-400">
                    {branch.description}
                  </p>
                )}
              </li>
            ))}
          </ul>
        </section>

        <section>
          <h3 className="mb-2 text-2xs font-semibold uppercase tracking-wide text-neutral-500">
            Excluded transactions ({right.exclusions.length})
          </h3>
          <ul className="space-y-2">
            {right.exclusions.map((exclusion) => (
              <li key={exclusion.id} className="rounded-lg border border-neutral-200 p-3 dark:border-neutral-800">
                <Mono className="font-semibold text-neutral-800 dark:text-neutral-200">
                  {exclusion.id}
                </Mono>
                <p className="mt-1 text-xs leading-relaxed text-neutral-700 dark:text-neutral-300">
                  {exclusion.description}
                </p>
                <div className="mt-2 flex flex-wrap items-center gap-1.5">
                  {exclusion.when.map((condition, i) => (
                    <span key={i} className="flex items-center gap-1.5">
                      {i > 0 && <span className="text-2xs text-neutral-400">∧</span>}
                      <Badge className="bg-slate-200 text-slate-800 dark:bg-slate-800 dark:text-slate-200">
                        <Mono className="text-inherit">{condition}</Mono>
                      </Badge>
                    </span>
                  ))}
                </div>
                {exclusion.legal_basis && (
                  <div className="mt-2">
                    <Disclosure summary="Basis">
                      <p className="text-2xs leading-relaxed text-neutral-600 dark:text-neutral-400">
                        {exclusion.legal_basis}
                      </p>
                    </Disclosure>
                  </div>
                )}
              </li>
            ))}
          </ul>
        </section>

        <p className="border-t border-neutral-200 pt-3 text-2xs leading-relaxed text-neutral-500 dark:border-neutral-800 dark:text-neutral-400">
          Conditions within one bullet are ANDed; bullets are ORed — each is an independent way
          the right disappears. The vocabulary is closed and the operators are fixed: no
          arithmetic, no nesting, no expression evaluator. A rulepack is data loaded from disk,
          and a language in it would be a code path from a YAML file to the interpreter, in the
          one component whose whole value is that its behaviour is inspectable.
          {right.source && <> Transcribed from: {right.source}.</>}
        </p>
      </CardBody>
    </Card>
  );
}