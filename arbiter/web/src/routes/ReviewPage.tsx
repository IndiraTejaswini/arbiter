import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import EvidenceViewer from "@/components/EvidenceViewer";
import ProofTree from "@/components/ProofTree";
import {
  Async, Badge, Button, Card, CardBody, CardHeader, ErrorState, Field,
  PageHeader, Select, TextArea, TextInput,
} from "@/components/ui";
import { api } from "@/lib/api";
import { OUTCOME_LABEL, OUTCOME_MEANING, OUTCOME_STYLE, cx, humanize, percent } from "@/lib/format";
import { getSession } from "@/lib/session";
import type { DecisionResponse, GraphResponse, Outcome } from "@/lib/types";
import { useAction, useApi } from "@/lib/useApi";

/**
 * Escalated-case review.
 *
 * The analyst's decision is the most consequential human action in the
 * system, and it is written to the append-only audit chain as
 * `ANALYST_OVERRODE_SYSTEM` or `ANALYST_DECISION` depending on whether it
 * differs from what the referee produced. That distinction is the system's
 * true error signal, so the form makes the divergence visible *before* the
 * analyst commits rather than burying it in a log.
 */

/**
 * CHARGEBACK_INELIGIBLE is deliberately absent. It is a finding of the
 * deterministic chargeback-right gate over network-held attributes, not a
 * verdict a reviewer reaches by weighing evidence — a reviewer who believes
 * a dispute was never chargeable is reporting that the ledger's attributes
 * are wrong, which is a data correction upstream. The API rejects it with a
 * 422; not offering it is the same rule stated where the analyst can act on
 * it rather than after they have committed.
 */
const OUTCOMES: Outcome[] = ["CARD_MEMBER_PREVAILS", "MERCHANT_PREVAILS", "SPLIT", "INSUFFICIENT_EVIDENCE"];

export default function ReviewPage() {
  const { caseId = "" } = useParams();
  const navigate = useNavigate();
  const session = getSession();

  const decision = useApi<DecisionResponse>(() => api.getDecision(caseId), [caseId]);
  const graph = useApi<GraphResponse>(() => api.getGraph(caseId), [caseId]);

  const [outcome, setOutcome] = useState<Outcome>("CARD_MEMBER_PREVAILS");
  const [reviewerId, setReviewerId] = useState(session?.actorId ?? "");
  const [notes, setNotes] = useState("");
  const [submitted, setSubmitted] = useState(false);

  const submit = useAction(async () => {
    const result = await api.reviewDecision(caseId, {
      outcome,
      reviewer_id: reviewerId.trim() || "analyst",
      notes: notes.trim() || undefined,
    });
    setSubmitted(true);
    return result;
  });

  const systemOutcome = decision.data?.outcome;
  const willOverride = Boolean(systemOutcome && systemOutcome !== outcome);

  if (submitted) {
    return (
      <Card className="mx-auto max-w-lg">
        <CardBody className="space-y-4 text-center">
          <p className="text-sm font-semibold text-neutral-900 dark:text-neutral-50">Review recorded</p>
          <p className="text-xs leading-relaxed text-neutral-600 dark:text-neutral-400">
            {submit.result?.overrode_system
              ? "Your decision differs from the system's and was written to the audit chain as ANALYST_OVERRODE_SYSTEM — the system's true error signal."
              : "Your decision matched the system's and was written to the audit chain as ANALYST_DECISION."}
          </p>

          {/* Whether the review entered the calibration pool is the
              difference between a judgment that improves the abstention gate
              and one that is only recorded. The API has always said which
              happened and why; the console used to claim the calibration
              pool was joined unconditionally, which is false for any case
              that ended at the chargeback-right gate — the conformal gate
              never scored it, so it has no nonconformity score to give. */}
          {submit.result && (
            <div className={cx(
              "rounded-lg border p-3 text-left",
              submit.result.calibration_sample_recorded
                ? "border-emerald-300 bg-emerald-50 dark:border-emerald-900 dark:bg-emerald-950/40"
                : "border-neutral-200 bg-neutral-50 dark:border-neutral-800 dark:bg-neutral-900",
            )}>
              <p className={cx(
                "text-2xs font-semibold",
                submit.result.calibration_sample_recorded
                  ? "text-emerald-900 dark:text-emerald-200"
                  : "text-neutral-700 dark:text-neutral-300",
              )}>
                {submit.result.calibration_sample_recorded
                  ? "Added to the conformal calibration pool"
                  : "Not added to the calibration pool"}
              </p>
              <p className="mt-1 text-2xs leading-relaxed text-neutral-600 dark:text-neutral-400">
                {submit.result.reason}
              </p>
            </div>
          )}

          <div className="flex justify-center gap-2">
            <Button onClick={() => navigate(`/cases/${caseId}`)}>Back to case</Button>
            <Link to={`/cases/${caseId}/audit`}><Button variant="primary">View audit trail</Button></Link>
          </div>
        </CardBody>
      </Card>
    );
  }

  return (
    <>
      <PageHeader
        eyebrow="Analyst workbench"
        title={`Review case ${caseId.slice(0, 8)}`}
        description="The system abstained. Your decision is the answer, and it is recorded in the append-only audit chain alongside what the system would have done."
        actions={<Link to={`/cases/${caseId}`}><Button>Back to case</Button></Link>}
      />

      <div className="grid gap-5 xl:grid-cols-3">
        <div className="space-y-5 xl:col-span-2">
          <Card>
            <CardHeader
              title="Why this escalated"
              subtitle="The abstention gate's own reasoning, verbatim."
            />
            <CardBody>
              <Async state={decision} label="decision">
                {(d) => (
                  <div className="space-y-4">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge className={OUTCOME_STYLE[d.outcome]} title={OUTCOME_MEANING[d.outcome]}>
                        system: {OUTCOME_LABEL[d.outcome]}
                      </Badge>
                      {d.outcome !== "CHARGEBACK_INELIGIBLE" && (
                        <Badge>confidence {percent(d.confidence)}</Badge>
                      )}
                      {d.provisional_credit_due && (
                        <Badge className="bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-300">
                          Reg E provisional credit due
                        </Badge>
                      )}
                    </div>

                    {/* A case that ended at the gate never reached the
                        conformal comparison, so there is no closeness to
                        probe and no calibration value in reviewing it. The
                        analyst should know that before they spend the time,
                        not after the API tells them their sample was
                        discarded. */}
                    {d.outcome === "CHARGEBACK_INELIGIBLE" ? (
                      <div className="rounded-lg border border-slate-300 bg-slate-50 p-3 dark:border-slate-700 dark:bg-slate-900/60">
                        <p className="text-xs font-semibold text-slate-900 dark:text-slate-100">
                          This case ended at the chargeback-right gate
                        </p>
                        <p className="mt-1 text-2xs leading-relaxed text-slate-700 dark:text-slate-300">
                          No evidence was weighed and the conformal gate never scored it, so a
                          review here is recorded in the audit chain but contributes no
                          calibration sample. If you believe this dispute <em>was</em> chargeable,
                          the correction belongs upstream in the transaction attributes the
                          exclusion turned on — not in this form.
                        </p>
                      </div>
                    ) : (
                      <>
                        {d.escalation_reason && (
                          <p className="rounded-lg border border-orange-300 bg-orange-50 p-3 text-xs leading-relaxed text-orange-900 dark:border-orange-900 dark:bg-orange-950/40 dark:text-orange-200">
                            {d.escalation_reason}
                          </p>
                        )}
                        <ClosenessProbe decision={d} />
                      </>
                    )}
                  </div>
                )}
              </Async>
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Derivation" subtitle="What the referee did establish, before it stopped." />
            <CardBody>
              <Async state={decision} label="proof tree">
                {(d) => <ProofTree proofTree={d.proof_tree} />}
              </Async>
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Evidence" subtitle="Ordered by provenance tier." />
            <CardBody>
              <Async state={graph} label="evidence">
                {(g) => <EvidenceViewer nodes={g.nodes} />}
              </Async>
            </CardBody>
          </Card>
        </div>

        <div className="xl:sticky xl:top-20 xl:self-start">
          <Card>
            <CardHeader title="Record your decision" />
            <CardBody className="space-y-4">
              <Field label="Outcome" htmlFor="outcome" required>
                <Select
                  id="outcome"
                  value={outcome}
                  onChange={setOutcome}
                  options={OUTCOMES.map((o) => ({ value: o, label: OUTCOME_LABEL[o] }))}
                />
              </Field>

              {willOverride && (
                <div className="rounded-lg border border-amber-300 bg-amber-50 p-3 dark:border-amber-900 dark:bg-amber-950/40">
                  <p className="text-xs font-semibold text-amber-900 dark:text-amber-200">
                    This overrides the system
                  </p>
                  <p className="mt-1 text-2xs leading-relaxed text-amber-800 dark:text-amber-300">
                    The referee produced {OUTCOME_LABEL[systemOutcome!]}. Your decision will be
                    recorded as <code className="font-mono">ANALYST_OVERRODE_SYSTEM</code> — the
                    system's true error signal, and the input its calibration learns from.
                  </p>
                </div>
              )}

              <Field label="Reviewer id" htmlFor="reviewer" required>
                <TextInput id="reviewer" value={reviewerId} onChange={setReviewerId} placeholder="analyst id" />
              </Field>

              <Field
                label="Notes"
                htmlFor="notes"
                hint="Recorded in the audit event payload. A reviewer reading this case in a year sees exactly what you saw and why you decided."
              >
                <TextArea id="notes" value={notes} onChange={setNotes} rows={5}
                          placeholder="What decided it for you?" />
              </Field>

              {submit.error && <ErrorState error={submit.error} />}

              <Button
                variant="primary"
                className="w-full"
                pending={submit.pending}
                onClick={() => submit.run()}
              >
                Record decision
              </Button>
            </CardBody>
          </Card>
        </div>
      </div>
    </>
  );
}

/**
 * How far each side is from prevailing, straight from the counterfactual
 * ledger. When both sides are one predicate away, that closeness IS the
 * reason the system abstained, and saying so is more useful to an analyst
 * than a confidence number alone.
 */
function ClosenessProbe({ decision }: { decision: DecisionResponse }) {
  const distances = Object.entries(decision.counterfactuals)
    .map(([outcome, cf]) => ({ outcome, distance: cf.delta.length, cf }))
    .sort((a, b) => a.distance - b.distance);

  if (distances.length < 2) return null;
  const closeCall = Math.abs((distances[0]?.distance ?? 0) - (distances[1]?.distance ?? 0)) <= 1;

  return (
    <div>
      {closeCall && (
        <p className="mb-2 rounded-lg border border-blue-300 bg-blue-50 p-2.5 text-xs leading-relaxed text-blue-900 dark:border-blue-900 dark:bg-blue-950/40 dark:text-blue-200">
          Both sides are within one predicate of prevailing. This is a genuinely close case,
          which is what the abstention gate exists to surface.
        </p>
      )}
      <ul className="space-y-1.5">
        {distances.map(({ outcome, distance, cf }) => (
          <li key={outcome} className="text-xs">
            <span className="font-medium text-neutral-800 dark:text-neutral-200">
              {humanize(outcome)}
            </span>
            <span className={cx(
              "ml-2 tabular-nums",
              distance === 0 ? "text-emerald-700 dark:text-emerald-400" : "text-neutral-500",
            )}>
              {distance === 0 ? "already satisfied" : `${distance} fact${distance === 1 ? "" : "s"} away`}
            </span>
            {cf.delta.length > 0 && (
              <span className="ml-2 text-2xs text-neutral-500">
                — {cf.delta.map((d) => humanize(d.predicate)).join(", ")}
              </span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
