import { useState } from "react";
import { Link } from "react-router-dom";
import {
  Async, Badge, Button, Card, CardBody, CardHeader, EmptyState, ErrorState,
  Field, Mono, PageHeader, Stat, Table, TableWrap, Td, Th, TextInput,
} from "@/components/ui";
import { api } from "@/lib/api";
import { STATE_STYLE, cx, dateTime, relativeDays } from "@/lib/format";
import { getSession } from "@/lib/session";
import type {
  AtRiskResponse, HealthResponse, QueueHealth, ReadyResponse, SweepReport,
} from "@/lib/types";
import { useAction, useApi, useInterval } from "@/lib/useApi";

/**
 * Operations — the regulatory clock and compliance actions.
 *
 * The deadline columns existed from the first migration and nothing ever
 * read them: no scheduler, no cron, no queue. A regulatory clock nobody
 * runs is three unused timestamp columns, and the obligation it was meant
 * to satisfy silently goes unmet.
 */
export default function OperationsPage() {
  const session = getSession();
  const [days, setDays] = useState(14);

  // /health is liveness and deliberately does NOT touch the database — a
  // liveness probe that fails on a transient DB blip makes the orchestrator
  // kill healthy pods. /ready reports whether this instance can actually
  // adjudicate. Both are shown because they answer different questions.
  const health = useApi<HealthResponse>(() => api.health(), []);
  const ready = useApi<ReadyResponse>(() => api.ready(), []);
  const atRisk = useApi<AtRiskResponse>(() => api.casesAtRisk(days), [days]);
  // Depth and oldest-queued age are the two numbers that say whether the
  // adjudication workers are keeping up. A permanently failed job is a case
  // that is NOT being adjudicated while its regulatory clock runs, which is
  // exactly the thing that must not be silent.
  const queue = useApi<QueueHealth>(() => api.queueHealth(), []);
  useInterval(queue.reload, 10_000);

  const sweep = useAction(async () => {
    const report = await api.sweepDeadlines();
    atRisk.reload();
    return report;
  });

  return (
    <>
      <PageHeader
        eyebrow="Operations"
        title="Regulatory clock"
        description="Reg Z 12 CFR 1026.13: 30-day acknowledgment, 90-day resolution. Reg E 12 CFR 1005.11: determination within 10 business days, with a provisional-credit obligation Reg Z has no analogue for."
        actions={
          <Button variant="primary" pending={sweep.pending} onClick={() => sweep.run()}>
            Run deadline sweep
          </Button>
        }
      />

      {sweep.error && <div className="mb-5"><ErrorState error={sweep.error} /></div>}
      {sweep.result && <div className="mb-5"><SweepResult report={sweep.result} /></div>}

      <div className="grid gap-5 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader
            title="Cases approaching a statutory deadline"
            subtitle="Ordered by how close they are to breaching. This is the analyst queue's SLA-risk ordering."
            actions={
              <div className="w-28">
                <TextInput
                  ariaLabel="Days ahead to look"
                  type="number"
                  value={String(days)}
                  onChange={(v) => setDays(Math.max(1, Math.min(90, Number(v) || 1)))}
                />
              </div>
            }
          />
          <CardBody>
            <Async
              state={atRisk}
              label="cases at risk"
              isEmpty={(d) => d.cases.length === 0}
              empty={<EmptyState title={`No open case is within ${days} days of its resolution deadline`} />}
            >
              {(data) => (
                <TableWrap>
                  <Table ariaLabel="Cases at risk of a deadline breach">
                    <thead>
                      <tr><Th>Case</Th><Th>Reason</Th><Th>Regime</Th><Th>State</Th><Th>Resolve by</Th><Th>Remaining</Th></tr>
                    </thead>
                    <tbody>
                      {data.cases.map((c) => (
                        <tr key={c.case_id} className={cx(c.days_remaining < 0 && "bg-red-50 dark:bg-red-950/30")}>
                          <Td>
                            <Link to={`/cases/${c.case_id}`}
                                  className="font-mono text-xs text-brand-600 hover:underline dark:text-brand-400">
                              {c.case_id.slice(0, 8)}
                            </Link>
                          </Td>
                          <Td><Mono>{c.reason_code}</Mono></Td>
                          <Td>
                            <Badge className={c.reg_regime === "REG_E"
                              ? "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-300"
                              : ""}>
                              {c.reg_regime}
                            </Badge>
                          </Td>
                          <Td><Badge className={STATE_STYLE[c.state]}>{c.state}</Badge></Td>
                          <Td className="whitespace-nowrap text-xs tabular-nums text-neutral-600 dark:text-neutral-400">
                            {dateTime(c.resolve_deadline)}
                          </Td>
                          <Td>
                            <span className={cx(
                              "whitespace-nowrap text-xs font-medium tabular-nums",
                              c.days_remaining < 0 ? "text-red-700 dark:text-red-400"
                                : c.days_remaining <= 3 ? "text-amber-700 dark:text-amber-400"
                                : "text-neutral-600 dark:text-neutral-400",
                            )}>
                              {relativeDays(c.resolve_deadline)}
                            </span>
                          </Td>
                        </tr>
                      ))}
                    </tbody>
                  </Table>
                </TableWrap>
              )}
            </Async>
          </CardBody>
        </Card>

        <div className="space-y-5">
          <Card>
            <CardHeader
              title="Adjudication queue"
              subtitle="Adjudication runs out of the request path; these workers drain it."
              actions={<Button size="sm" onClick={queue.reload}>Refresh</Button>}
            />
            <CardBody>
              <Async state={queue} label="queue health">
                {(data) => (
                  <div className="space-y-3">
                    <dl className="grid grid-cols-2 gap-3">
                      <Stat label="Queued" value={data.depth.queued}
                            tone={data.depth.queued > 50 ? "warn" : undefined} />
                      <Stat label="Running" value={data.depth.running} />
                      <Stat label="Succeeded" value={data.depth.succeeded} tone="good" />
                      <Stat label="Failed" value={data.depth.failed}
                            tone={data.depth.failed > 0 ? "bad" : undefined} />
                    </dl>

                    {data.depth.oldest_queued_age_seconds !== null && (
                      <p className={cx(
                        "text-2xs",
                        data.depth.oldest_queued_age_seconds > 300
                          ? "text-amber-700 dark:text-amber-400"
                          : "text-neutral-500 dark:text-neutral-400",
                      )}>
                        Oldest queued job has waited{" "}
                        {Math.round(data.depth.oldest_queued_age_seconds)}s
                        {data.depth.oldest_queued_age_seconds > 300 &&
                          " — workers are not keeping up."}
                      </p>
                    )}

                    {data.recent_failures.length > 0 && (
                      <div className="border-t border-neutral-200 pt-3 dark:border-neutral-800">
                        <p className="text-2xs font-semibold text-red-800 dark:text-red-300">
                          Permanently failed — these cases are not adjudicated
                        </p>
                        <ul className="mt-1.5 space-y-1.5">
                          {data.recent_failures.slice(0, 5).map((failure) => (
                            <li key={failure.job_id} className="text-2xs">
                              <Link to={`/cases/${failure.case_id}`}
                                    className="font-mono text-brand-600 hover:underline dark:text-brand-400">
                                {failure.case_id.slice(0, 8)}
                              </Link>
                              <span className="ml-2 text-neutral-600 dark:text-neutral-400">
                                {failure.error}
                              </span>
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </div>
                )}
              </Async>
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Service" subtitle="Liveness and loaded rulepacks." />
            <CardBody>
              <Async state={health} label="service health">
                {(data) => (
                  <div className="space-y-2">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs text-neutral-600 dark:text-neutral-400">Liveness</span>
                      <Badge className="bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300">
                        {data.status}
                      </Badge>
                    </div>
                    <div>
                      <p className="text-xs text-neutral-600 dark:text-neutral-400">
                        Rulepacks loaded ({data.reason_codes.length})
                      </p>
                      <div className="mt-1 flex flex-wrap gap-1">
                        {data.reason_codes.map((code) => (
                          <Badge key={code}><Mono>{code}</Mono></Badge>
                        ))}
                      </div>
                    </div>
                  </div>
                )}
              </Async>
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Conformal calibration" subtitle="Per reason code, from calibration_sample." />
            <CardBody>
              <Async state={ready} label="calibration">
                {(data) => (
                  <div className="space-y-3">
                    {Object.entries(data.calibration).map(([code, info]) => (
                      <div key={code}>
                        <div className="flex items-center justify-between gap-2">
                          <Mono className="font-semibold">{code}</Mono>
                          <span className="text-2xs tabular-nums text-neutral-500">
                            n={info.n} · eff {info.effective_n}
                          </span>
                        </div>
                        {/* Progress toward the SERVED threshold. The width
                            was `min(100, effective_n)` — a raw count treated
                            as a percentage, which only coincidentally read
                            correctly because the threshold happened to be
                            100. Tuning it left the bar meaningless. */}
                        <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-neutral-200 dark:bg-neutral-800">
                          <div
                            className={cx("h-full rounded-full",
                              info.calibrated ? "bg-emerald-500" : "bg-amber-500")}
                            style={{
                              width: `${Math.min(100, (info.effective_n / Math.max(1, data.min_calibration_n)) * 100)}%`,
                            }}
                          />
                        </div>
                        {/* The threshold is server configuration
                            (ARBITER_CONFORMAL_MIN_N), and this used to state
                            "n ≥ 100" as a fact — so a deployment that tuned
                            it displayed a number untrue of itself. */}
                        <p className="mt-1 text-2xs text-neutral-500 dark:text-neutral-400">
                          {info.calibrated
                            ? "auto-resolution enabled"
                            : `escalates every case until effective n ≥ ${data.min_calibration_n}`}
                        </p>
                      </div>
                    ))}
                    <p className="border-t border-neutral-200 pt-3 text-2xs leading-relaxed text-neutral-500 dark:border-neutral-800 dark:text-neutral-400">
                      Gated on the EFFECTIVE sample size after inverse-probability weighting,
                      not the raw count: a pool dominated by a few heavily-weighted audit
                      samples carries less information than its count suggests. An uncalibrated
                      gate escalates rather than auto-resolving against an unreliable threshold.
                      This deployment requires effective n ≥ {data.min_calibration_n} at
                      α = {data.conformal_alpha}. Populate with{" "}
                      <Mono>scripts/seed_calibration.py</Mono> or accumulated review.
                    </p>
                  </div>
                )}
              </Async>
            </CardBody>
          </Card>

          {session?.role === "ADMIN" && <ErasureCard />}
        </div>
      </div>
    </>
  );
}

function SweepResult({ report }: { report: SweepReport }) {
  const groups: [string, string[], string][] = [
    ["Acknowledged", report.acknowledged, "Reg Z 1026.13(b)(1) / Reg E 1005.11(c)(1)"],
    ["Acknowledgment breached", report.ack_breached, "past the statutory window"],
    ["Merchant window expired", report.merchant_windows_expired, "adjudicated on the merits — NOT conceded"],
    ["Provisional credit due", report.provisional_credit_due, "Reg E 1005.11(c)(2)"],
    ["Resolution breached", report.resolve_breached, "escalated to a senior analyst"],
  ];

  return (
    <Card>
      <CardHeader
        title="Sweep complete"
        subtitle="Every action taken is written to the append-only case_event log. A regulatory clock whose firings are not auditable is not evidence of compliance."
        actions={<Badge>{report.total_actions} action{report.total_actions === 1 ? "" : "s"}</Badge>}
      />
      <CardBody>
        {report.total_actions === 0 ? (
          <p className="text-xs text-neutral-600 dark:text-neutral-400">
            No deadline had elapsed. The sweep is idempotent — every branch is guarded by the
            column it sets, so re-running takes no action twice.
          </p>
        ) : (
          <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3">
            {groups.filter(([, ids]) => ids.length > 0).map(([label, ids, hint]) => (
              <Stat key={label} label={label} value={ids.length} hint={hint} />
            ))}
          </dl>
        )}
        {report.merchant_windows_expired.length > 0 && (
          <p className="mt-4 rounded-lg border border-emerald-300 bg-emerald-50 p-3 text-xs leading-relaxed text-emerald-900 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-200">
            <strong className="font-semibold">
              {report.merchant_windows_expired.length} merchant window(s) expired and none was conceded.
            </strong>{" "}
            Advocate-M builds each merchant's case from Amex-held data and the referee decides on the
            merits. This is the direct fix for R03/R13 — cases lost on a mailbox rather than on facts.
          </p>
        )}
      </CardBody>
    </Card>
  );
}

function ErasureCard() {
  const [subjectId, setSubjectId] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const erase = useAction((id: string) => api.eraseSubject(id));

  return (
    <Card>
      <CardHeader
        title="GDPR Article 17 erasure"
        subtitle="Crypto-shredding: the subject's key is destroyed, not the ciphertext."
      />
      <CardBody className="space-y-3">
        <Field
          label="Subject id"
          htmlFor="subject-id"
          hint="A card_member_id or merchant_id. Every case they are a party to is annotated with a SUBJECT_ERASURE_EXECUTED event."
        >
          <TextInput id="subject-id" mono value={subjectId} onChange={setSubjectId}
                     placeholder="00000000-0000-0000-0000-000000000000" />
        </Field>

        <div className="rounded-lg border border-amber-300 bg-amber-50 p-3 dark:border-amber-900 dark:bg-amber-950/40">
          <p className="text-2xs leading-relaxed text-amber-900 dark:text-amber-200">
            <strong className="font-semibold">Irreversible.</strong> The append-only case_event
            chain and every Merkle commitment over it stay byte-identical and verifiable — only the
            plaintext becomes permanently unrecoverable. That is the standard resolution to
            "immutable audit log" versus "right to erasure": erasure is a key-destruction
            operation, never a data mutation.
          </p>
        </div>

        <label className="flex items-start gap-2 text-2xs text-neutral-700 dark:text-neutral-300">
          <input
            type="checkbox"
            checked={confirmed}
            onChange={(e) => setConfirmed(e.target.checked)}
            className="mt-0.5 rounded border-neutral-300 text-brand-600 focus:ring-brand-500"
          />
          I have a verified erasure request for this subject.
        </label>

        {erase.error && <ErrorState error={erase.error} />}
        {erase.result && (
          <div className="rounded-lg border border-emerald-300 bg-emerald-50 p-3 text-2xs text-emerald-900 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-200">
            {erase.result.erased
              ? `Key destroyed. ${erase.result.cases_annotated} case(s) annotated in the audit chain.`
              : "No key existed for that subject — nothing to erase. Erasure is idempotent and never fails loudly."}
          </div>
        )}

        <Button
          variant="danger"
          className="w-full"
          pending={erase.pending}
          disabled={!confirmed || !subjectId.trim()}
          onClick={() => erase.run(subjectId.trim())}
        >
          Destroy subject key
        </Button>
      </CardBody>
    </Card>
  );
}
