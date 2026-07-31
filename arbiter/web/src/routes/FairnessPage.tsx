import { useState } from "react";
import {
  Async, Badge, Card, CardBody, CardHeader, EmptyState, Mono, PageHeader,
  Stat, Table, TableWrap, Td, Th,
} from "@/components/ui";
import { api } from "@/lib/api";
import { cx, percent } from "@/lib/format";
import type { FairnessFinding, FairnessRule, FairnessRuleReport } from "@/lib/types";
import { useApi } from "@/lib/useApi";

/**
 * A7 — rule-level disparate impact.
 *
 * Model-level fairness metrics cannot say WHICH mechanism discriminates.
 * This can: for each rule and stratum pair, the firing rate is compared
 * within the same evidence-strength bucket, so "this stratum's cases have
 * weaker evidence" is never confused with "this rule discriminates."
 *
 * A finding is flagged only when it is BOTH practically significant
 * (≥15pp) and statistically significant after Benjamini–Hochberg FDR
 * control across the whole comparison family. Underpowered comparisons are
 * shown separately and deliberately: "we could not tell" is a different
 * claim from "we checked and found nothing", and a dashboard that only
 * reports "0 flagged" invites a reviewer to conclude the rules are fair.
 */
export default function FairnessPage() {
  const [selected, setSelected] = useState<string | null>(null);
  const rules = useApi<FairnessRule[]>(() => api.listFairnessRules(), []);
  const report = useApi<FairnessRuleReport>(
    () => api.getRuleFairness(selected!),
    [selected],
    { enabled: Boolean(selected) },
  );

  return (
    <>
      <PageHeader
        eyebrow="Compliance"
        title="Rule-level fairness audit"
        description="Firing rates by merchant tier, conditioned on evidence strength. A flagged rule is a discovered defect with a line number — something no model-level equalised-odds metric can give you."
      />

      <div className="grid gap-5 lg:grid-cols-3">
        <Card>
          <CardHeader title="Rules" subtitle="Every rule across every loaded rulepack." />
          <CardBody>
            <Async
              state={rules}
              label="rules"
              isEmpty={(r) => r.length === 0}
              empty={<EmptyState title="No rulepacks loaded" />}
            >
              {(data) => (
                <ul className="max-h-[32rem] space-y-1 overflow-y-auto pr-1">
                  {data.map((rule) => (
                    <li key={rule.rule_id}>
                      <button
                        type="button"
                        onClick={() => setSelected(rule.rule_id)}
                        className={cx(
                          "w-full rounded-lg px-2.5 py-2 text-left transition-colors",
                          "focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500",
                          selected === rule.rule_id
                            ? "bg-brand-50 ring-1 ring-brand-300 dark:bg-brand-950 dark:ring-brand-700"
                            : "hover:bg-neutral-100 dark:hover:bg-neutral-800",
                        )}
                      >
                        <div className="flex items-center gap-2">
                          <Mono className="font-semibold">{rule.rule_id}</Mono>
                          <Badge>{rule.reason_code}</Badge>
                        </div>
                        <p className="mt-0.5 line-clamp-2 text-2xs leading-relaxed text-neutral-500 dark:text-neutral-400">
                          {rule.description || rule.head}
                        </p>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </Async>
          </CardBody>
        </Card>

        <div className="lg:col-span-2">
          {!selected ? (
            <Card>
              <CardBody>
                <EmptyState
                  title="Select a rule"
                  description="Each rule is audited independently across merchant-size strata, within matched evidence-strength buckets."
                />
              </CardBody>
            </Card>
          ) : (
            <Async state={report} label="fairness report">
              {(data) => <RuleReport report={data} />}
            </Async>
          )}
        </div>
      </div>
    </>
  );
}

function RuleReport({ report }: { report: FairnessRuleReport }) {
  const flagged = report.findings.filter((f) => f.flagged);
  const inconclusive = report.findings.filter((f) => !f.flagged && f.power && !f.power.adequately_powered);
  const clean = report.findings.length - flagged.length - inconclusive.length;

  return (
    <Card>
      <CardHeader
        title={<><Mono className="text-sm font-semibold">{report.rule_id}</Mono> disparate-impact report</>}
        subtitle="Wilson score intervals, pooled two-proportion z-test, Benjamini–Hochberg FDR at q ≤ 0.05, minimum 30 cases per cell."
        actions={
          <Badge className={flagged.length > 0
            ? "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300"
            : "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"}>
            {flagged.length > 0 ? `${flagged.length} flagged` : "none flagged"}
          </Badge>
        }
      />
      <CardBody className="space-y-5">
        <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <Stat label="Cases analysed" value={report.n_cases} />
          <Stat label="Comparisons" value={report.findings.length} />
          <Stat label="Flagged" value={flagged.length} tone={flagged.length > 0 ? "bad" : "good"} />
          <Stat
            label="Inconclusive"
            value={inconclusive.length}
            tone={inconclusive.length > 0 ? "warn" : undefined}
            hint="too small to resolve 15pp"
          />
        </dl>

        {report.findings.length === 0 ? (
          <EmptyState
            title="No comparison had enough data"
            description="Every stratum pair fell below the 30-case minimum. That is not evidence of fairness — it is an absence of evidence either way."
          />
        ) : (
          <>
            {flagged.length > 0 && (
              <section>
                <h3 className="mb-2 text-xs font-semibold text-red-800 dark:text-red-300">
                  Flagged — practically and statistically significant
                </h3>
                <FindingsTable findings={flagged} />
              </section>
            )}

            {clean > 0 && (
              <section>
                <h3 className="mb-2 text-xs font-semibold text-neutral-700 dark:text-neutral-300">
                  Adequately powered, not flagged ({clean})
                </h3>
                <FindingsTable
                  findings={report.findings.filter(
                    (f) => !f.flagged && f.power?.adequately_powered,
                  )}
                />
              </section>
            )}

            {inconclusive.length > 0 && (
              <section>
                <h3 className="mb-2 text-xs font-semibold text-amber-800 dark:text-amber-300">
                  Inconclusive — underpowered ({inconclusive.length})
                </h3>
                <p className="mb-2 text-2xs leading-relaxed text-neutral-600 dark:text-neutral-400">
                  These cells are too small to resolve a 15-point effect. The absence of a flag
                  here means the audit could not tell, not that the rule is fair.
                </p>
                <FindingsTable findings={inconclusive} />
              </section>
            )}
          </>
        )}
      </CardBody>
    </Card>
  );
}

function FindingsTable({ findings }: { findings: FairnessFinding[] }) {
  if (findings.length === 0) return null;
  return (
    <TableWrap>
      <Table ariaLabel="Disparate impact findings">
        <thead>
          <tr>
            <Th>Strata</Th><Th>Bucket</Th><Th>Rate A (95% CI)</Th>
            <Th>Rate B (95% CI)</Th><Th>Δ</Th><Th>q</Th><Th>Power</Th>
          </tr>
        </thead>
        <tbody>
          {findings
            .slice()
            .sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta))
            .map((f, i) => (
              <tr key={`${f.rule_id}-${f.stratum_a}-${f.stratum_b}-${f.evidence_strength_bucket}-${i}`}
                  className={cx(f.flagged && "bg-red-50 dark:bg-red-950/30")}>
                <Td className="whitespace-nowrap text-xs">
                  <span className="font-medium">{f.stratum_a}</span>
                  <span className="mx-1 text-neutral-400">vs</span>
                  <span className="font-medium">{f.stratum_b}</span>
                </Td>
                <Td className="text-xs tabular-nums">{f.evidence_strength_bucket}</Td>
                <Td className="whitespace-nowrap text-xs tabular-nums">
                  {percent(f.firing_rate_a, 0)}
                  {f.ci_a && (
                    <span className="ml-1 text-neutral-400">
                      [{percent(f.ci_a.ci_low, 0)}–{percent(f.ci_a.ci_high, 0)}] n={f.ci_a.n}
                    </span>
                  )}
                </Td>
                <Td className="whitespace-nowrap text-xs tabular-nums">
                  {percent(f.firing_rate_b, 0)}
                  {f.ci_b && (
                    <span className="ml-1 text-neutral-400">
                      [{percent(f.ci_b.ci_low, 0)}–{percent(f.ci_b.ci_high, 0)}] n={f.ci_b.n}
                    </span>
                  )}
                </Td>
                <Td className={cx(
                  "whitespace-nowrap text-xs font-semibold tabular-nums",
                  Math.abs(f.delta) >= 0.15
                    ? "text-red-700 dark:text-red-400"
                    : "text-neutral-600 dark:text-neutral-400",
                )}>
                  {f.delta > 0 ? "+" : ""}{(f.delta * 100).toFixed(1)}pp
                </Td>
                <Td className="whitespace-nowrap text-xs tabular-nums">
                  <span className={f.q_value <= 0.05 ? "font-semibold text-red-700 dark:text-red-400" : "text-neutral-500"}>
                    {f.q_value < 0.0001 ? "<0.0001" : f.q_value.toFixed(4)}
                  </span>
                </Td>
                <Td>
                  {f.power && (
                    <Badge
                      className={f.power.adequately_powered
                        ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"
                        : "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-300"}
                      title={`Minimum detectable effect at 80% power: ${percent(f.power.min_detectable_effect, 1)}`}
                    >
                      {f.power.adequately_powered ? "adequate" : "under"}
                    </Badge>
                  )}
                </Td>
              </tr>
            ))}
        </tbody>
      </Table>
    </TableWrap>
  );
}
