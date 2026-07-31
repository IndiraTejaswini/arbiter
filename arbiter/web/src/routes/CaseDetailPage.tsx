import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import CaseTimeline from "@/components/CaseTimeline";
import ChargebackRightPanel from "@/components/ChargebackRightPanel";
import ContradictionStatus from "@/components/ContradictionStatus";
import EvidenceUpload from "@/components/EvidenceUpload";
import EvidenceViewer from "@/components/EvidenceViewer";
import NarrationPanel from "@/components/NarrationPanel";
import ProofTree from "@/components/ProofTree";
import RegulatoryClock from "@/components/RegulatoryClock";
import StatusStream from "@/components/StatusStream";
import {
  Async, Badge, Button, Card, CardBody, CardHeader, CopyButton, Disclosure,
  EmptyState, ErrorState, Mono, Skeleton, Stat, Table, TableWrap, Td, Th,
} from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import {
  OUTCOME_LABEL, OUTCOME_MEANING, OUTCOME_STYLE, SEVERITY_STYLE, STATE_STYLE,
  bytes, cx, dateTime, humanize, money, percent, relativeDays,
} from "@/lib/format";
import { getSession, isPrivileged } from "@/lib/session";
import type {
  AdjudicationJob, ArtifactListResponse, DecisionResponse, DisputeCase,
  EligibilityRecord, GraphResponse, Outcome, TimelineResponse,
} from "@/lib/types";
import { useAction, useApi } from "@/lib/useApi";
import { useRulepacks } from "@/lib/useRulepacks";

/**
 * Case detail — the centrepiece of the console.
 *
 * Layout follows the order a reviewer actually reads a case in:
 *   1. what was decided, and how confident the system is
 *   2. WHY — the proof tree, clickable through to the evidence node that
 *      established each literal
 *   3. what the evidence actually is, ordered by provenance tier
 *   4. what would have changed the outcome (counterfactual ledger)
 *   5. what the system found wrong with the evidence (contradictions)
 *   6. the documents relied on (Reg E 1005.11(d))
 */
export default function CaseDetailPage() {
  const { caseId = "" } = useParams();
  const session = getSession();
  const privileged = isPrivileged(session);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const { rulepacks } = useRulepacks();

  const reasonCodeLabel = useCallback(
    (code: string) => rulepacks.find((p) => p.reason_code === code)?.title ?? code,
    [rulepacks],
  );

  const caseState = useApi<DisputeCase>(() => api.getCase(caseId), [caseId]);
  const decision = useApi<DecisionResponse>(() => api.getDecision(caseId), [caseId]);
  const graph = useApi<GraphResponse>(() => api.getGraph(caseId), [caseId]);
  const timeline = useApi<TimelineResponse>(() => api.getTimeline(caseId), [caseId]);
  const artifacts = useApi<ArtifactListResponse>(() => api.listArtifacts(caseId), [caseId]);

  const reloadAll = useCallback(() => {
    caseState.reload(); decision.reload(); graph.reload();
    timeline.reload(); artifacts.reload();
  }, [caseState, decision, graph, timeline, artifacts]);

  // Adjudication is asynchronous: the API enqueues and returns 202, and
  // this polls the job until it reaches a terminal state. Progress itself
  // streams over SSE; this exists so a FAILED job is visible rather than
  // looking like a case that simply never finished.
  const [job, setJob] = useState<AdjudicationJob | null>(null);
  const adjudicate = useAction(async () => {
    const enqueued = await api.adjudicate(caseId);
    setJob(enqueued.job);
    return enqueued;
  });

  // Pick up work already in flight: a reload mid-adjudication, or a job
  // another reviewer started, would otherwise show no progress at all.
  useEffect(() => {
    let cancelled = false;
    api.getLatestJob(caseId)
      .then(({ job: existing }) => {
        if (!cancelled && existing && !existing.terminal) setJob(existing);
      })
      .catch(() => { /* no job yet, or not visible to this actor */ });
    return () => { cancelled = true; };
  }, [caseId]);

  useEffect(() => {
    if (!job || job.terminal) return;
    const timer = window.setInterval(async () => {
      try {
        const latest = await api.getJob(job.job_id);
        setJob(latest);
        if (latest.terminal) {
          window.clearInterval(timer);
          if (latest.state === "SUCCEEDED") reloadAll();
        }
      } catch {
        window.clearInterval(timer);
      }
    }, 1500);
    return () => window.clearInterval(timer);
  }, [job, reloadAll]);

  const selectEvidence = useCallback((nodeId: string) => {
    setSelectedNodeId(nodeId);
    document.getElementById(`evidence-${nodeId}`)
      ?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, []);

  if (caseState.error) {
    return <ErrorState error={caseState.error} onRetry={caseState.reload} />;
  }

  return (
    <>
      <Async
        state={caseState}
        label="case"
        skeleton={<div className="space-y-2"><Skeleton className="h-8 w-64" /><Skeleton className="h-4 w-96" /></div>}
      >
        {(c) => (
          <header className="mb-5">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                {/* The label comes from the server's rulepack catalogue and
                    falls back to the bare code. It used to be a hardcoded
                    three-entry map whose default was the word "Dispute" — so
                    a case under any newly added reason code was captioned
                    with a generic noun rather than what it actually is. */}
                <p className="text-2xs font-semibold uppercase tracking-widest text-brand-600 dark:text-brand-400">
                  {c.reason_code} · {reasonCodeLabel(c.reason_code)}
                </p>
                <h1 className="mt-1 flex flex-wrap items-center gap-2.5 text-2xl font-bold tracking-tight text-neutral-900 dark:text-neutral-50">
                  <span className="font-mono">{c.case_id.slice(0, 8)}</span>
                  <CopyButton value={c.case_id} label="case id" />
                  <Badge className={STATE_STYLE[c.state]}>{c.state}</Badge>
                </h1>
                <p className="mt-1.5 text-sm text-neutral-600 dark:text-neutral-400">
                  {money(c.amount_minor, c.currency)} · filed {dateTime(c.filed_at)} ·
                  resolve {relativeDays(c.resolve_deadline)}
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                {privileged && (
                  <>
                    <Link to={`/cases/${caseId}/audit`}><Button>Audit trail</Button></Link>
                    {decision.data?.abstained && (
                      <Link to={`/cases/${caseId}/review`}>
                        <Button variant="primary">Review this case</Button>
                      </Link>
                    )}
                    <Button
                      pending={adjudicate.pending || Boolean(job && !job.terminal)}
                      onClick={() => adjudicate.run()}
                      title="Queues an adjudication and returns immediately. Evidence derivation is idempotent, so re-running does not duplicate nodes."
                    >
                      {job && !job.terminal
                        ? job.state === "QUEUED" ? "Queued…" : "Adjudicating…"
                        : decision.data ? "Re-adjudicate" : "Adjudicate"}
                    </Button>
                  </>
                )}
              </div>
            </div>

            {adjudicate.error && (
              <div className="mt-3"><ErrorState error={adjudicate.error} /></div>
            )}

            {job && (
              <div className={cx(
                "mt-3 flex flex-wrap items-center gap-2 rounded-lg border px-3 py-2 text-xs",
                job.state === "FAILED"
                  ? "border-red-300 bg-red-50 text-red-900 dark:border-red-900 dark:bg-red-950/40 dark:text-red-200"
                  : "border-neutral-200 bg-neutral-50 text-neutral-700 dark:border-neutral-800 dark:bg-neutral-900 dark:text-neutral-300",
              )}>
                <Badge>{job.state}</Badge>
                <Mono>{job.job_id.slice(0, 8)}</Mono>
                {job.attempts > 1 && (
                  <span>attempt {job.attempts} of {job.max_attempts}</span>
                )}
                {job.error && <span className="font-medium">{job.error}</span>}
                {job.state === "FAILED" && (
                  <span>
                    This case was not adjudicated. Its regulatory clock is still running.
                  </span>
                )}
              </div>
            )}

            {!c.merchant_responded && (
              <div className="mt-3 rounded-lg border border-amber-300 bg-amber-50 p-3 text-xs leading-relaxed text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
                <strong className="font-semibold">The merchant has not responded.</strong>{" "}
                ARBITER does not treat silence as concession. Advocate-M builds the merchant's
                case from Amex-held authorization, settlement, AVS/CVV, device, and carrier
                data, and the referee adjudicates on the merits. Under the standard flow this
                would have been an automatic loss on process rather than facts.
              </div>
            )}
          </header>
        )}
      </Async>

      <div className="mb-5 grid gap-5 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <StatusStream caseId={caseId} onTerminal={reloadAll} />
        </div>
        <Card>
          <CardHeader
            title="Regulatory clock"
            subtitle="Driven by a scheduled sweeper, not by page loads — deadlines accrue whether or not anyone is looking."
          />
          <CardBody>
            <Async state={caseState} label="regulatory clock">
              {(c) => <RegulatoryClock dispute={c} />}
            </Async>
          </CardBody>
        </Card>
      </div>

      {/* --- Decision ---------------------------------------------------- */}
      <Card className="mb-5">
        <CardHeader
          title="Decision"
          subtitle="Produced by propositional Horn forward chaining. No model participates in this step."
        />
        <CardBody>
          {decision.error instanceof ApiError && decision.error.isNotFound ? (
            <EmptyState
              title="Not adjudicated yet"
              description={privileged
                ? "Run the pipeline to produce a signed decision and proof tree."
                : "This case has not been adjudicated yet. You will see the outcome and its full derivation here once it has."}
            />
          ) : (
            <Async state={decision} label="decision">
              {(d) => <DecisionSummary decision={d} />}
            </Async>
          )}
        </CardBody>
      </Card>

      {/* --- The chargeback-right gate ------------------------------------
          Runs before everything else and answers a different question from
          the referee's: not "who is right?" but "may Amex charge this back
          at all?". Shown first when it closed, because in that case it IS
          the decision and there is no proof tree beneath it. */}
      {isEligibility(decision.data?.eligibility) && (
        <Card className="mb-5">
          <CardHeader
            title="Chargeback right"
            subtitle="Amex's published guide gives every reason code a filing window and an 'Excluded Transactions' list. Both remove the right to charge a merchant back, before any evidence is weighed."
          />
          <CardBody>
            <ChargebackRightPanel eligibility={decision.data!.eligibility as EligibilityRecord} />
          </CardBody>
        </Card>
      )}

      {/* --- The decision in words ---------------------------------------
          Grounded narration: every sentence's citations were checked against
          the evidence graph before this text was stored, and one that did
          not resolve would have discarded the whole thing. */}
      <Card className="mb-5">
        <CardHeader
          title="In plain terms"
          subtitle="Every sentence is checked against the evidence graph before it is stored. Citations jump to the node that established the claim."
        />
        <CardBody>
          {decision.error instanceof ApiError && decision.error.isNotFound ? (
            <EmptyState
              title="Nothing to explain yet"
              description="The written explanation is produced with the decision."
            />
          ) : (
            <Async state={decision} label="narration">
              {(d) => (
                <NarrationPanel
                  narration={d.narration}
                  onSelectEvidence={selectEvidence}
                  selectedNodeId={selectedNodeId}
                />
              )}
            </Async>
          )}
        </CardBody>
      </Card>

      <div className="grid gap-5 xl:grid-cols-2">
        {/* --- Why ------------------------------------------------------- */}
        <Card>
          <CardHeader
            title="Why — the proof tree"
            subtitle="Every rule that fired, every literal that satisfied it, and the evidence node that established each one. Click a node id to jump to it."
          />
          <CardBody>
            <Async state={decision} label="proof tree">
              {(d) => (
                <ProofTree
                  proofTree={d.proof_tree}
                  onSelectEvidence={selectEvidence}
                  selectedNodeId={selectedNodeId}
                />
              )}
            </Async>
          </CardBody>
        </Card>

        {/* --- Evidence -------------------------------------------------- */}
        <Card>
          <CardHeader
            title="Evidence"
            subtitle="Ordered by provenance tier. Committed evidence is cryptographically proven to predate the dispute."
          />
          <CardBody>
            <Async state={graph} label="evidence graph">
              {(g) => (
                <EvidenceViewer
                  nodes={g.nodes}
                  selectedNodeId={selectedNodeId}
                  onSelect={setSelectedNodeId}
                />
              )}
            </Async>
          </CardBody>
        </Card>
      </div>

      {/* --- Derived facts ----------------------------------------------- */}
      <Card className="mt-5">
        <CardHeader
          title="Derived facts"
          subtitle="Everything the referee knew. The proof tree shows what carried the decision; this shows the complete objective fact set it was evaluated over."
        />
        <CardBody>
          <Async state={decision} label="derived facts">
            {(d) => <DerivedFacts predicates={d.predicates} />}
          </Async>
        </CardBody>
      </Card>

      {/* --- Counterfactuals -------------------------------------------- */}
      <Card className="mt-5">
        <CardHeader
          title="What would have changed this"
          subtitle="The exact minimal set of facts that flips the outcome — a set-difference against enumerated prime implicants, not an approximation."
        />
        <CardBody>
          <Async state={decision} label="counterfactuals">
            {(d) => <Counterfactuals decision={d} />}
          </Async>
        </CardBody>
      </Card>

      {/* --- What has happened on this case ------------------------------
          The party-facing counterpart to the reviewer-only audit chain. The
          endpoint behind it always returned these events; the console
          fetched them for the contradictions array and dropped them, so a
          card member had no surface at all for "what is happening to my
          dispute". */}
      <Card className="mt-5">
        <CardHeader
          title="Case history"
          subtitle={privileged
            ? "The party-facing timeline. The audit trail is the same story with hashes and signatures re-verified on read."
            : "Everything that has happened on your case, in order — including every regulatory deadline the clock fired."}
          actions={privileged
            ? <Link to={`/cases/${caseId}/audit`}><Badge>Verified audit chain →</Badge></Link>
            : undefined}
        />
        <CardBody>
          <Async state={timeline} label="case history">
            {(t) => <CaseTimeline events={t.events} />}
          </Async>
        </CardBody>
      </Card>

      {/* --- Contradictions & documents --------------------------------- */}
      <div className="mt-5 grid gap-5 xl:grid-cols-2">
        <Card>
          <CardHeader
            title="Contradiction analysis"
            subtitle="Four mandatory deterministic layers: temporal (Allen interval algebra), numeric reconciliation, identity coherence, and semantic (DeBERTa-NLI). No generative model participates."
          />
          <CardBody className="space-y-4">
            {decision.data?.contradiction_analysis && (
              <ContradictionStatus analysis={decision.data.contradiction_analysis} />
            )}
            <Async
              state={timeline}
              label="contradictions"
              isEmpty={(t) => t.contradictions.length === 0}
              empty={
                <EmptyState
                  title="No contradictions found"
                  description="All four layers ran and the evidence is internally consistent."
                />
              }
            >
              {(t) => (
                <ul className="space-y-2">
                  {t.contradictions.map((contradiction) => (
                    <li
                      key={contradiction.contradiction_id}
                      className="rounded-lg border border-neutral-200 p-3 dark:border-neutral-800"
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge className={SEVERITY_STYLE[contradiction.severity]}>
                          {contradiction.severity}
                        </Badge>
                        <Badge>{contradiction.detail?.layer ?? "unknown"} layer</Badge>
                        <Mono>{humanize(contradiction.kind)}</Mono>
                      </div>
                      <p className="mt-2 text-xs leading-relaxed text-neutral-700 dark:text-neutral-300">
                        {contradiction.detail?.description}
                      </p>
                      {(contradiction.severity === "HIGH" || contradiction.severity === "CRITICAL") && (
                        <p className="mt-1.5 text-2xs font-medium text-orange-700 dark:text-orange-400">
                          Blocks auto-resolution regardless of confidence — conformal coverage is
                          a population guarantee, not a per-case safety property.
                        </p>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </Async>
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="Documents relied on"
            subtitle="Reg E 12 CFR 1005.11(d)(1). Bytes are retained and re-hashed against the digest recorded at upload before they are served."
          />
          <CardBody className="space-y-5">
            {/* Either party to the case may submit evidence. A reviewer
                uploading on someone's behalf is recorded as NEUTRAL rather
                than silently attributed to a party. */}
            <EvidenceUpload caseId={caseId} onUploaded={reloadAll} />

            <Async
              state={artifacts}
              label="artifacts"
              isEmpty={(a) => a.artifacts.length === 0}
              empty={<EmptyState title="No documents submitted" description="This case is being adjudicated on Amex-held network data alone." />}
            >
              {(a) => (
                <TableWrap>
                  <Table ariaLabel="Artifacts on this case">
                    <thead>
                      <tr><Th>Artifact</Th><Th>Type</Th><Th>Size</Th><Th>By</Th><Th>Scan</Th><Th /></tr>
                    </thead>
                    <tbody>
                      {a.artifacts.map((artifact) => (
                        <tr key={artifact.artifact_id}>
                          <Td><Mono title={artifact.artifact_id}>{artifact.artifact_id.slice(0, 8)}</Mono></Td>
                          <Td className="text-xs">{artifact.mime_type}</Td>
                          <Td className="whitespace-nowrap text-xs tabular-nums">{bytes(artifact.byte_size)}</Td>
                          <Td><Badge>{artifact.uploaded_by}</Badge></Td>
                          <Td>
                            <Badge className={artifact.scan_status === "ACCEPTED"
                              ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"
                              : "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300"}>
                              {artifact.scan_status}
                            </Badge>
                          </Td>
                          <Td>
                            <a
                              href={api.artifactUrl(artifact.artifact_id)}
                              className="text-xs text-brand-600 hover:underline dark:text-brand-400"
                              target="_blank" rel="noreferrer"
                            >
                              download
                            </a>
                          </Td>
                        </tr>
                      ))}
                    </tbody>
                  </Table>
                  {a.artifacts.some((x) => (x.forensics?.findings?.length ?? 0) > 0) && (
                    <div className="mt-3">
                      <Disclosure summary="Forensic findings">
                        <ul className="space-y-2">
                          {a.artifacts.filter((x) => x.forensics?.findings?.length).map((x) => (
                            <li key={x.artifact_id} className="text-2xs">
                              <Mono>{x.artifact_id.slice(0, 8)}</Mono>
                              <ul className="ml-3 mt-0.5 list-disc space-y-0.5 text-neutral-600 dark:text-neutral-400">
                                {x.forensics.findings!.map((finding, i) => <li key={i}>{finding}</li>)}
                              </ul>
                              {!!x.forensics.confidence_penalty && (
                                <p className="ml-3 mt-0.5 text-amber-700 dark:text-amber-400">
                                  reduces evidentiary weight by {percent(x.forensics.confidence_penalty, 0)} —
                                  a signal for a human, never a verdict
                                </p>
                              )}
                            </li>
                          ))}
                        </ul>
                      </Disclosure>
                    </div>
                  )}
                </TableWrap>
              )}
            </Async>
          </CardBody>
        </Card>
      </div>
    </>
  );
}

/** A decision written before the chargeback-right gate existed carries `{}`
 *  here. Narrowing on a required field rather than on emptiness keeps that
 *  case distinguishable from "the gate ran and found nothing", which is a
 *  different fact about the world. */
function isEligibility(
  value: DecisionResponse["eligibility"] | undefined,
): value is EligibilityRecord {
  return Boolean(value && "available" in value);
}

function DecisionSummary({ decision }: { decision: DecisionResponse }) {
  const ineligible = decision.outcome === "CHARGEBACK_INELIGIBLE";
  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <Badge
          className={cx(OUTCOME_STYLE[decision.outcome], "px-2.5 py-1 text-xs")}
          title={OUTCOME_MEANING[decision.outcome]}
        >
          {OUTCOME_LABEL[decision.outcome]}
        </Badge>
        {decision.abstained && (
          <Badge className="bg-orange-100 text-orange-900 dark:bg-orange-950 dark:text-orange-300">
            abstained — routed to a human
          </Badge>
        )}
        {decision.merchant_silent && <Badge>merchant silent</Badge>}
        {decision.provisional_credit_due && (
          <Badge
            className="bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-300"
            title="12 CFR 1005.11(c): provisional credit is due while the investigation continues, independent of who ultimately prevails."
          >
            Reg E provisional credit due
          </Badge>
        )}
        {decision.selected_for_audit && (
          <Badge
            className="bg-indigo-100 text-indigo-800 dark:bg-indigo-950 dark:text-indigo-300"
            title="This case auto-resolved and was ALSO sampled for human review. Its review enters the calibration pool with an inverse-probability weight, which is what keeps the pool exchangeable with the deployment distribution."
          >
            sampled for audit review
          </Badge>
        )}
        {decision.llm_rejections > 0 && (
          <Badge
            className="bg-violet-100 text-violet-800 dark:bg-violet-950 dark:text-violet-300"
            title="Advocate assertions that failed independent re-derivation from the evidence graph. They never reached the referee."
          >
            {decision.llm_rejections} LLM assertion{decision.llm_rejections === 1 ? "" : "s"} rejected
          </Badge>
        )}
      </div>

      {/* A case the gate ended never reached the conformal gate, so its
          confidence (1.0, deterministic by construction) and its empty
          conformal set describe a comparison that did not happen. Rendering
          them as "100% confident, abstained" would be actively misleading,
          so the stat row says what actually applies instead. */}
      <dl className="mt-5 grid grid-cols-2 gap-4 sm:grid-cols-4">
        {ineligible ? (
          <>
            <Stat
              label="Evidence weighed"
              value="none"
              hint="the gate closed before the referee ran"
            />
            <Stat label="Decided" value={dateTime(decision.decided_at)} />
            <Stat
              label="Conformal gate"
              value="did not run"
              hint="its coverage guarantee is calibrated over adjudicated outcomes only"
            />
            <Stat label="Rulepack" value={<Mono>{decision.rulepack_hash.slice(7, 19)}</Mono>} hint="pinned, replayable" />
          </>
        ) : (
          <>
            <Stat
              label="Confidence"
              value={percent(decision.confidence)}
              hint="deterministic features only"
              tone={decision.confidence >= 0.7 ? "good" : decision.confidence >= 0.4 ? "warn" : "bad"}
            />
            <Stat label="Decided" value={dateTime(decision.decided_at)} />
            <Stat
              label="Conformal set"
              value={decision.conformal_set.length === 1 ? "singleton" : "not singleton"}
              hint={decision.conformal_set.join(", ") || "empty — abstain"}
            />
            <Stat label="Rulepack" value={<Mono>{decision.rulepack_hash.slice(7, 19)}</Mono>} hint="pinned, replayable" />
          </>
        )}
      </dl>

      {ineligible && (
        <p className="mt-4 rounded-lg border border-slate-300 bg-slate-50 p-3 text-xs leading-relaxed text-slate-800 dark:border-slate-700 dark:bg-slate-900/60 dark:text-slate-200">
          {OUTCOME_MEANING.CHARGEBACK_INELIGIBLE}
        </p>
      )}

      {decision.escalation_reason && (
        <div className="mt-4 rounded-lg border border-orange-300 bg-orange-50 p-3 dark:border-orange-900 dark:bg-orange-950/40">
          <p className="text-xs font-semibold text-orange-900 dark:text-orange-200">
            Why a human is deciding this
          </p>
          <p className="mt-1 text-xs leading-relaxed text-orange-800 dark:text-orange-300">
            {decision.escalation_reason}
          </p>
        </div>
      )}
    </>
  );
}

function Counterfactuals({ decision }: { decision: DecisionResponse }) {
  const entries = Object.entries(decision.counterfactuals);
  if (entries.length === 0) return <EmptyState title="No counterfactuals recorded" />;

  return (
    <div className="grid gap-3 md:grid-cols-2">
      {entries.map(([outcome, cf]) => {
        const isCurrent = OUTCOME_LABEL[decision.outcome] === outcomeLabel(outcome);
        return (
          <div
            key={outcome}
            className={cx(
              "rounded-lg border p-3",
              isCurrent
                ? "border-emerald-300 bg-emerald-50/60 dark:border-emerald-900 dark:bg-emerald-950/30"
                : "border-neutral-200 dark:border-neutral-800",
            )}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs font-semibold text-neutral-800 dark:text-neutral-200">
                {outcomeLabel(outcome)}
              </span>
              {isCurrent && <Badge className="bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300">current</Badge>}
            </div>

            {cf.already_satisfied ? (
              <p className="mt-2 text-xs text-neutral-600 dark:text-neutral-400">
                Already satisfied by the current evidence.
              </p>
            ) : cf.delta.length === 0 ? (
              <p className="mt-2 text-xs text-neutral-600 dark:text-neutral-400">
                No reachable path under this rulepack.
              </p>
            ) : (
              <ul className="mt-2 space-y-1">
                {cf.delta.map((item) => (
                  <li key={item.predicate} className="flex flex-wrap items-center gap-1.5 text-xs">
                    <Badge className={item.action === "OBTAIN"
                      ? "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300"
                      : "bg-orange-100 text-orange-900 dark:bg-orange-950 dark:text-orange-300"}>
                      {item.action.toLowerCase()}
                    </Badge>
                    <span className="text-neutral-700 dark:text-neutral-300">
                      {humanize(item.predicate)}
                    </span>
                    {!item.obtainable && (
                      <span
                        className="text-2xs italic text-neutral-500"
                        title="A fixed historical fact — it either already holds or it does not. No submission can change it."
                      >
                        not obtainable
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
        );
      })}
    </div>
  );
}

function DerivedFacts({ predicates }: { predicates: Record<string, string> }) {
  const entries = Object.entries(predicates ?? {});
  if (entries.length === 0) {
    return <EmptyState title="No predicates derived" />;
  }
  const rank: Record<string, number> = { TRUE: 0, FALSE: 1, UNKNOWN: 2 };
  return (
    <ul className="flex flex-wrap gap-1.5">
      {entries
        .sort((a, b) => (rank[a[1]] ?? 3) - (rank[b[1]] ?? 3) || a[0].localeCompare(b[0]))
        .map(([predicate, status]) => (
          <li key={predicate}>
            <Badge
              className={
                status === "TRUE"
                  ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"
                  : status === "FALSE"
                  ? "bg-rose-100 text-rose-800 dark:bg-rose-950 dark:text-rose-300"
                  : "bg-neutral-100 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-400"
              }
              title={
                status === "UNKNOWN"
                  ? "No evidence addresses this predicate, or the evidence that does failed its provenance-tier gate."
                  : undefined
              }
            >
              <Mono className="text-inherit">{predicate}</Mono> {status.toLowerCase()}
            </Badge>
          </li>
        ))}
    </ul>
  );
}

function outcomeLabel(raw: string): string {
  const mapped: Record<string, string> = {
    MERCHANT_WINS: "Merchant prevails",
    CARD_MEMBER_WINS: "Card member prevails",
    SPLIT: "Split",
  };
  return mapped[raw] ?? OUTCOME_LABEL[raw as Outcome] ?? humanize(raw);
}

