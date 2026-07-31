import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Badge, Button, Card, CardBody, CardHeader, ErrorState, Field, PageHeader,
  Select, TextArea, TextInput,
} from "@/components/ui";
import { api } from "@/lib/api";
import type { DisputeCase, IntentNotResolved } from "@/lib/types";
import { useAction } from "@/lib/useApi";
import { useRulepacks } from "@/lib/useRulepacks";

/**
 * File a dispute.
 *
 * Two paths, and the difference between them is the point:
 *
 *   - a known reason code goes straight to the matching rulepack;
 *   - free text goes through LLM boundary 1, where the classifier proposes
 *     a bucket and `verify_intent` decides whether to accept it.
 *
 * The classifier NEVER silently guesses. Below 0.70 confidence, or on a
 * bucket with no loaded rulepack, or when the model is unreachable, the
 * API returns an unresolved response and no case is created — because
 * misrouting is the one intake error that corrupts everything downstream:
 * wrong rulepack means wrong predicates means entirely wrong evidence
 * requirements. This screen surfaces that outcome rather than retrying.
 */
export default function FilePage() {
  const navigate = useNavigate();

  // The filable reason codes come from the server's loaded rulepack
  // directory. They used to be a hardcoded three-entry list here, so a
  // fourth rulepack the backend could adjudicate was one no card member
  // could file under.
  const { rulepacks, loading: loadingCodes } = useRulepacks();

  const [transactionId, setTransactionId] = useState("");
  const [mode, setMode] = useState<"reason_code" | "complaint">("complaint");
  const [reasonCode, setReasonCode] = useState("");
  const [complaint, setComplaint] = useState("");
  const [unresolved, setUnresolved] = useState<IntentNotResolved | null>(null);

  // Default to the first code the server actually offers, once we know what
  // that is. Defaulting to a literal "C08" would have silently filed under a
  // rulepack that a given deployment may not even load.
  useEffect(() => {
    if (!reasonCode && rulepacks.length > 0) setReasonCode(rulepacks[0]!.reason_code);
  }, [reasonCode, rulepacks]);

  const file = useAction(async () => {
    setUnresolved(null);
    const body = mode === "reason_code"
      ? { transaction_id: transactionId.trim(), reason_code: reasonCode }
      : { transaction_id: transactionId.trim(), complaint_text: complaint.trim() };

    // A fresh idempotency key per submission attempt: a retry of the SAME
    // attempt must not create a second case, but a genuinely new filing must.
    const result = await api.createDispute(body, crypto.randomUUID());

    if ("resolved" in result && result.resolved === false) {
      setUnresolved(result);
      return;
    }
    navigate(`/cases/${(result as DisputeCase).case_id}`);
  });

  const canSubmit = transactionId.trim().length > 0 &&
    (mode === "reason_code"
      ? reasonCode.length > 0
      : complaint.trim().length > 0);

  return (
    <>
      <PageHeader
        title="File a dispute"
        description="A dispute can only be filed by the card member the transaction belongs to. The API enforces that from your token, not from what this form sends."
      />

      <div className="grid gap-5 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader title="New dispute" />
          <CardBody className="space-y-4">
            <Field
              label="Transaction id"
              htmlFor="txn"
              required
              hint="A transaction_id from the ledger. Seed one with scripts/seed_demo.py if you are running locally."
            >
              <TextInput id="txn" mono value={transactionId} onChange={setTransactionId}
                         placeholder="00000000-0000-0000-0000-000000000000" />
            </Field>

            <Field label="How do you want to describe the problem?" htmlFor="mode">
              <Select
                id="mode"
                value={mode}
                onChange={setMode}
                options={[
                  { value: "complaint", label: "Describe it in my own words (LLM intent classification)" },
                  { value: "reason_code", label: "I know the reason code" },
                ]}
              />
            </Field>

            {mode === "reason_code" ? (
              <Field
                label="Reason code"
                htmlFor="reason"
                hint={
                  loadingCodes
                    ? "Loading the reason codes this deployment has rulepacks for…"
                    : `${rulepacks.length} reason code${rulepacks.length === 1 ? "" : "s"} loaded. ` +
                      "This list is the server's rulepack directory, not a fixed menu — a rulepack " +
                      "added there appears here."
                }
              >
                <Select
                  id="reason"
                  value={reasonCode}
                  onChange={setReasonCode}
                  options={rulepacks.map((pack) => ({
                    value: pack.reason_code,
                    label: pack.description
                      ? `${pack.reason_code} — ${pack.description}`
                      : `${pack.reason_code} — ${pack.title}`,
                  }))}
                />
              </Field>
            ) : (
              <Field
                label="What happened?"
                htmlFor="complaint"
                required
                hint="Read by a local model to propose a reason code. A deterministic verifier accepts the proposal only above 0.70 confidence and only if it resolves to a loaded rulepack — otherwise this goes to a human, never to a guess."
              >
                <TextArea
                  id="complaint"
                  rows={5}
                  value={complaint}
                  onChange={setComplaint}
                  placeholder="I ordered a pair of shoes three weeks ago, the tracking says delivered, but nothing ever arrived and the merchant has stopped replying."
                />
              </Field>
            )}

            {file.error && <ErrorState error={file.error} />}

            {unresolved && (
              <div className="rounded-lg border border-amber-300 bg-amber-50 p-3 dark:border-amber-900 dark:bg-amber-950/40">
                <p className="text-xs font-semibold text-amber-900 dark:text-amber-200">
                  {unresolved.needs_user_confirmation
                    ? "Please confirm the reason code"
                    : "Routed to human triage"}
                </p>
                <p className="mt-1 text-2xs leading-relaxed text-amber-800 dark:text-amber-300">
                  {unresolved.reason}
                </p>
                {unresolved.signals.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1">
                    {unresolved.signals.map((signal, i) => (
                      <Badge key={i} className="bg-white/60 dark:bg-black/20">{signal}</Badge>
                    ))}
                  </div>
                )}
                {unresolved.proposed_reason_code && (
                  <Button
                    size="sm"
                    className="mt-3"
                    onClick={() => {
                      setMode("reason_code");
                      setReasonCode(unresolved.proposed_reason_code!);
                      setUnresolved(null);
                    }}
                  >
                    Use {unresolved.proposed_reason_code} and continue
                  </Button>
                )}
                <p className="mt-2 text-2xs text-amber-700 dark:text-amber-400">
                  No case was created. Nothing was guessed.
                </p>
              </div>
            )}

            <Button
              variant="primary"
              className="w-full"
              pending={file.pending}
              disabled={!canSubmit}
              onClick={() => file.run()}
            >
              File dispute
            </Button>

            {mode === "reason_code" && !loadingCodes && rulepacks.length === 0 && (
              <p className="text-2xs leading-relaxed text-amber-700 dark:text-amber-400">
                This server has no rulepacks loaded, so there is nothing to file under.
                Describing the problem in your own words will route to human triage.
              </p>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="What happens next" />
          <CardBody>
            <ol className="space-y-3 text-xs leading-relaxed text-neutral-600 dark:text-neutral-400">
              {[
                ["Amex records are gathered", "Authorization, settlement, AVS/CVV, 3-D Secure, device, descriptor, prior transactions — held on one system because Amex is issuer, network, and often acquirer."],
                ["Evidence is checked against itself", "Four mandatory deterministic layers: temporal, numeric, identity, and semantic. A high-severity contradiction blocks automatic resolution outright."],
                ["Both sides get an advocate", "Including the merchant, even if they never respond. Silence is not treated as concession."],
                ["A rule engine decides", "Propositional Horn forward chaining, producing a proof tree. No model participates in the verdict."],
                ["Or it abstains", "If it cannot decide with a calibrated guarantee, a human decides — and the case file is already assembled for them."],
              ].map(([title, body], i) => (
                <li key={i} className="flex gap-2.5">
                  <span
                    aria-hidden="true"
                    className="mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full bg-brand-100 text-2xs font-bold text-brand-700 dark:bg-brand-950 dark:text-brand-300"
                  >
                    {i + 1}
                  </span>
                  <span>
                    <strong className="font-semibold text-neutral-800 dark:text-neutral-200">{title}.</strong>{" "}
                    {body}
                  </span>
                </li>
              ))}
            </ol>
          </CardBody>
        </Card>
      </div>
    </>
  );
}
