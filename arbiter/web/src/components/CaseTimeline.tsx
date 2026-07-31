import { cx, dateTime, humanize } from "@/lib/format";
import type { CaseEventOut } from "@/lib/types";
import { Badge, Disclosure, EmptyState, Mono } from "./ui";

/**
 * What has happened on this case, in order.
 *
 * `GET /v1/cases/{id}/timeline` has always returned these events — it is the
 * party-facing counterpart to the reviewer-only audit chain, deliberately
 * scoped so a card member or merchant can follow their own case without
 * being handed the raw internal log. The console fetched the endpoint for
 * its `contradictions` array and dropped `events` on the floor, so the one
 * surface a non-privileged party had for "what is happening to my dispute"
 * rendered nothing at all.
 *
 * The distinction from AuditPage is real and worth keeping: that page
 * re-verifies hashes and signatures and is reviewer/admin only; this one
 * just tells the story, to whoever the case belongs to.
 */

/** Events whose meaning a party should not have to infer from a
 *  SCREAMING_SNAKE identifier. Anything unlisted falls back to a humanised
 *  form of its type — a new event appearing untranslated is better than one
 *  being hidden. */
const EVENT_COPY: Record<string, { label: string; detail: string }> = {
  CASE_FILED: { label: "Dispute filed", detail: "The regulatory clock started here." },
  INTENT_CLASSIFIED: {
    label: "Reason code identified",
    detail: "A model proposed the category; a deterministic verifier accepted it only above the confidence threshold and only because it resolved to a rulepack in force.",
  },
  EVIDENCE_UPLOADED: { label: "Evidence submitted", detail: "Scanned, type-sniffed, and checked for tampering signals on arrival." },
  ADJUDICATION_STARTED: { label: "Adjudication started", detail: "" },
  CHARGEBACK_RIGHT_UNAVAILABLE: {
    label: "Not chargeable under this code",
    detail: "The network gives no right to charge this back. No evidence was weighed — and this does not decide the card member's rights against the issuer.",
  },
  CHARGEBACK_RIGHT_UNDETERMINED: {
    label: "An exclusion could not be checked",
    detail: "The ledger did not supply an attribute it turns on, so the case proceeded to the merits. Unknown never excludes.",
  },
  DECISION_COMPUTED: { label: "Decided", detail: "By propositional Horn forward chaining. No model participates in this step." },
  CASE_ESCALATED: { label: "Escalated to a human", detail: "The system could not decide with a calibrated guarantee, so it abstained. That is an answer, not a failure." },
  LLM_ASSERTIONS_REJECTED: {
    label: "Advocate claims rejected",
    detail: "Assertions that failed independent re-derivation from the evidence graph. They never reached the referee.",
  },
  NARRATION_GROUNDING_FAILED: {
    label: "A generated explanation was discarded",
    detail: "It cited evidence that does not exist on this case, so the whole narration was voided and the deterministic template used instead.",
  },
  SELECTED_FOR_AUDIT_REVIEW: {
    label: "Sampled for human review",
    detail: "This case resolved automatically and was routed to a person anyway, so the calibration pool sees cases the escalation path never visits.",
  },
  PROVISIONAL_CREDIT_DUE: { label: "Provisional credit due", detail: "12 CFR 1005.11(c) — owed while the investigation continues, whoever ultimately prevails." },
  PROVISIONAL_CREDIT_DEADLINE_REACHED: { label: "Provisional credit deadline reached", detail: "" },
  ACK_DEADLINE_MET: { label: "Acknowledged", detail: "" },
  ACK_DEADLINE_BREACHED: { label: "Acknowledgment deadline breached", detail: "" },
  MERCHANT_WINDOW_EXPIRED: {
    label: "Merchant response window expired",
    detail: "NOT conceded. The merchant's case was built from Amex-held records and adjudicated on the merits.",
  },
  RESOLVE_DEADLINE_BREACHED: { label: "Resolution deadline breached", detail: "Escalated to a senior analyst." },
  ANALYST_DECISION: { label: "Analyst decision recorded", detail: "It matched what the system produced." },
  ANALYST_OVERRODE_SYSTEM: {
    label: "Analyst overrode the system",
    detail: "The system's true error signal, and the input its calibration learns from.",
  },
  SUBJECT_ERASURE_EXECUTED: {
    label: "Erasure executed",
    detail: "GDPR Article 17. The subject's key was destroyed; the append-only record itself stays intact and verifiable.",
  },
};

/** Events a party should see highlighted rather than buried. */
const EMPHASISED = new Set([
  "DECISION_COMPUTED", "CASE_ESCALATED", "CHARGEBACK_RIGHT_UNAVAILABLE",
  "ANALYST_OVERRODE_SYSTEM", "MERCHANT_WINDOW_EXPIRED", "PROVISIONAL_CREDIT_DUE",
]);

export default function CaseTimeline({ events }: { events: CaseEventOut[] }) {
  if (events.length === 0) {
    return (
      <EmptyState
        title="Nothing has happened yet"
        description="Events appear here as the case moves — filing, evidence, adjudication, and every regulatory deadline the clock fires."
      />
    );
  }

  return (
    <ol className="space-y-0">
      {events.map((event, i) => {
        const copy = EVENT_COPY[event.event_type];
        const emphasised = EMPHASISED.has(event.event_type);
        const last = i === events.length - 1;
        return (
          <li key={event.seq} className="flex gap-3">
            {/* Rail: a continuous line, not a per-item border, so the
                sequence reads as one thread rather than a stack of cards. */}
            <div className="flex flex-col items-center" aria-hidden="true">
              <span className={cx(
                "mt-1.5 h-2 w-2 shrink-0 rounded-full",
                emphasised ? "bg-brand-500" : "bg-neutral-300 dark:bg-neutral-700",
              )} />
              {!last && <span className="w-px flex-1 bg-neutral-200 dark:bg-neutral-800" />}
            </div>

            <div className={cx("min-w-0 flex-1", last ? "pb-0" : "pb-4")}>
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                <span className={cx(
                  "text-xs",
                  emphasised
                    ? "font-semibold text-neutral-900 dark:text-neutral-100"
                    : "font-medium text-neutral-700 dark:text-neutral-300",
                )}>
                  {copy?.label ?? humanize(event.event_type)}
                </span>
                {event.actor_type === "human" && (
                  <Badge className="bg-violet-100 text-violet-800 dark:bg-violet-950 dark:text-violet-300">
                    person
                  </Badge>
                )}
                <span className="text-2xs tabular-nums text-neutral-500 dark:text-neutral-500">
                  {dateTime(event.occurred_at)}
                </span>
              </div>

              {copy?.detail && (
                <p className="mt-0.5 text-2xs leading-relaxed text-neutral-500 dark:text-neutral-400">
                  {copy.detail}
                </p>
              )}

              {Object.keys(event.payload ?? {}).length > 0 && (
                <div className="mt-1">
                  <Disclosure summary="detail">
                    <pre className="max-h-48 overflow-auto rounded-lg bg-neutral-100 p-2 text-2xs leading-relaxed text-neutral-700 dark:bg-neutral-950 dark:text-neutral-300">
                      {JSON.stringify(event.payload, null, 2)}
                    </pre>
                  </Disclosure>
                </div>
              )}

              {!copy && (
                <Mono className="text-2xs opacity-60">{event.event_type}</Mono>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
