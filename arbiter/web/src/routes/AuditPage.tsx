import { Link, useParams } from "react-router-dom";
import {
  Async, Badge, Button, Card, CardBody, CardHeader, Disclosure, Mono,
  PageHeader, Stat, Table, TableWrap, Td, Th,
} from "@/components/ui";
import { api } from "@/lib/api";
import { cx, dateTime, humanize } from "@/lib/format";
import type { AuditResponse } from "@/lib/types";
import { useApi } from "@/lib/useApi";

/**
 * The append-only audit chain.
 *
 * Three independent checks are reported separately, because a broken link
 * and a tampered payload are different attacks with different remediations:
 *
 *   link_valid          this event chains to the previous one
 *   payload_hash_valid  event_hash actually covers the stored payload
 *   signature_valid     Ed25519, verified against the key epoch the event
 *                       was signed under, so a past rotation never breaks it
 *
 * The middle one was previously missing: the endpoint verified the chain
 * and the signature over `event_hash` but never that `event_hash` matched
 * the payload it claimed to commit to — so an altered payload still
 * reported a clean bill of health.
 */
export default function AuditPage() {
  const { caseId = "" } = useParams();
  const audit = useApi<AuditResponse>(() => api.getAudit(caseId), [caseId]);

  return (
    <>
      <PageHeader
        eyebrow="Compliance"
        title={`Audit trail · ${caseId.slice(0, 8)}`}
        description="Every event is hash-chained to its predecessor and Ed25519-signed. The chain is recomputed here on read — you do not have to trust the API's own claim that it is intact."
        actions={<Link to={`/cases/${caseId}`}><Button>Back to case</Button></Link>}
      />

      <Async state={audit} label="audit trail">
        {(data) => (
          <>
            <Card className="mb-5">
              <CardHeader
                title="Chain verification"
                subtitle="Recomputed from the stored rows on every request."
                actions={
                  <Badge className={data.verified
                    ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"
                    : "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300"}>
                    {data.verified ? "verified" : "VERIFICATION FAILED"}
                  </Badge>
                }
              />
              <CardBody>
                <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                  <Stat label="Events" value={data.events.length} />
                  <Stat
                    label="Hash chain"
                    value={data.chain_valid ? "intact" : `broken at ${data.broken_at_seq}`}
                    tone={data.chain_valid ? "good" : "bad"}
                    hint="prev_hash linkage"
                  />
                  <Stat
                    label="Payload integrity"
                    value={data.payloads_valid ? "intact" : "TAMPERED"}
                    tone={data.payloads_valid ? "good" : "bad"}
                    hint="event_hash re-derived from payload"
                  />
                  <Stat
                    label="Signatures"
                    value={data.signatures_valid ? "valid" : "INVALID"}
                    tone={data.signatures_valid ? "good" : "bad"}
                    hint="Ed25519, per key epoch"
                  />
                </dl>

                {!data.verified && (
                  <div role="alert" className="mt-4 rounded-lg border border-red-300 bg-red-50 p-3 dark:border-red-900 dark:bg-red-950/40">
                    <p className="text-xs font-semibold text-red-900 dark:text-red-200">
                      This case's audit record cannot be verified.
                    </p>
                    <p className="mt-1 text-2xs leading-relaxed text-red-800 dark:text-red-300">
                      {!data.chain_valid && `The hash chain breaks at sequence ${data.broken_at_seq}. `}
                      {!data.payloads_valid && "At least one event's stored payload does not match the hash that was signed over it. "}
                      {!data.signatures_valid && "At least one signature does not verify under the key epoch it claims. "}
                      Escalate to compliance — the append-only guarantee has been violated.
                    </p>
                  </div>
                )}
              </CardBody>
            </Card>

            <Card>
              <CardHeader
                title="Event log"
                subtitle="Append-only. Corrections are new rows; nothing is ever updated or deleted."
              />
              <CardBody>
                <TableWrap>
                  <Table ariaLabel="Case event log">
                    <thead>
                      <tr>
                        <Th className="w-12">Seq</Th><Th>Event</Th><Th>Actor</Th>
                        <Th>When</Th><Th>Integrity</Th><Th>Payload</Th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.events.map((event) => {
                        const ok = event.link_valid && event.payload_hash_valid && event.signature_valid;
                        const human = event.actor_type === "human";
                        return (
                          <tr key={event.seq} className={cx(!ok && "bg-red-50 dark:bg-red-950/30")}>
                            <Td className="font-mono text-xs tabular-nums">{event.seq}</Td>
                            <Td>
                              <span className={cx(
                                "text-xs font-medium",
                                event.event_type.startsWith("ANALYST_OVERRODE")
                                  ? "text-amber-700 dark:text-amber-400"
                                  : "text-neutral-800 dark:text-neutral-200",
                              )}>
                                {humanize(event.event_type)}
                              </span>
                            </Td>
                            <Td>
                              <Badge className={human
                                ? "bg-violet-100 text-violet-800 dark:bg-violet-950 dark:text-violet-300"
                                : ""} title={event.actor_id}>
                                {event.actor_type}
                              </Badge>
                            </Td>
                            <Td className="whitespace-nowrap text-xs text-neutral-600 dark:text-neutral-400">
                              {dateTime(event.occurred_at)}
                            </Td>
                            <Td>
                              <div className="flex gap-1">
                                <IntegrityDot ok={event.link_valid} label="chain link" />
                                <IntegrityDot ok={event.payload_hash_valid} label="payload hash" />
                                <IntegrityDot ok={event.signature_valid} label={`signature (epoch ${event.key_epoch})`} />
                              </div>
                            </Td>
                            <Td>
                              <Disclosure summary="inspect">
                                <pre className="max-h-56 overflow-auto rounded-lg bg-neutral-100 p-2 text-2xs leading-relaxed text-neutral-700 dark:bg-neutral-950 dark:text-neutral-300">
                                  {JSON.stringify(event.payload, null, 2)}
                                </pre>
                                <p className="mt-1.5 text-2xs text-neutral-500">
                                  event_hash <Mono>{event.event_hash.slice(0, 24)}…</Mono>
                                  {event.rulepack_hash && (
                                    <> · rulepack <Mono>{event.rulepack_hash.slice(0, 12)}…</Mono></>
                                  )}
                                </p>
                              </Disclosure>
                            </Td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </Table>
                </TableWrap>
              </CardBody>
            </Card>
          </>
        )}
      </Async>
    </>
  );
}

function IntegrityDot({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span
      title={`${label}: ${ok ? "valid" : "INVALID"}`}
      className={cx(
        "inline-block h-2 w-2 rounded-full",
        ok ? "bg-emerald-500" : "bg-red-500",
      )}
    >
      <span className="sr-only">{`${label} ${ok ? "valid" : "invalid"}`}</span>
    </span>
  );
}
