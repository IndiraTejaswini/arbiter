"use client";

import { use, useCallback, useEffect, useState } from "react";
import { api, DecisionResponse, DisputeCase } from "@/lib/api";
import StatusStream from "@/components/StatusStream";

function formatMoney(minor: number, currency: string) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency }).format(minor / 100);
}

export default function CardMemberPortalPage({ params }: { params: Promise<{ caseId: string }> }) {
  const { caseId } = use(params);
  const [dispute, setDispute] = useState<DisputeCase | null>(null);
  const [decision, setDecision] = useState<DecisionResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const d = await api.getCase(caseId);
      setDispute(d);
      try {
        setDecision(await api.getDecision(caseId));
      } catch {
        setDecision(null);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [caseId]);

  useEffect(() => {
    load();
  }, [load]);

  if (error) return <div className="p-6 text-red-700">{error}</div>;
  if (!dispute) return <div className="p-6 text-sm text-neutral-500">Loading…</div>;

  const youWon = decision?.outcome === "CARD_MEMBER_PREVAILS";

  return (
    <div className="mx-auto max-w-2xl space-y-4 p-6">
      <header className="rounded border border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900">
        <div className="text-lg font-bold">
          Dispute #{caseId.slice(0, 8)} · {formatMoney(dispute.amount_minor, dispute.currency)}
        </div>
        <div className="text-sm text-neutral-500">
          filed {new Date(dispute.filed_at).toLocaleDateString()} · status {dispute.state}
        </div>
      </header>

      <StatusStream caseId={caseId} onTerminal={load} />

      {decision && (
        <>
          <div
            className={`rounded border p-4 ${
              youWon
                ? "border-emerald-300 bg-emerald-50 dark:bg-emerald-950"
                : "border-red-300 bg-red-50 dark:bg-red-950"
            }`}
          >
            <div className="font-bold">{youWon ? "You prevailed" : "The merchant prevailed"}</div>
            {decision.abstained && (
              <div className="mt-1 text-sm">
                This case has been referred to a specialist for a closer look — no automatic decision has been
                made yet.
              </div>
            )}
          </div>

          {!youWon &&
            decision.counterfactuals["CARD_MEMBER_WINS"] &&
            !decision.counterfactuals["CARD_MEMBER_WINS"].already_satisfied &&
            decision.counterfactuals["CARD_MEMBER_WINS"].delta.length > 0 && (
              <div className="rounded border border-neutral-200 bg-white p-4 text-sm dark:border-neutral-800 dark:bg-neutral-900">
                <div className="mb-1 font-semibold">What would strengthen your claim</div>
                {decision.counterfactuals["CARD_MEMBER_WINS"].delta.map((d) => (
                  <div key={d.predicate}>
                    • {d.action === "RETRACT" ? "Resolve a conflict about" : "Provide"}{" "}
                    {d.predicate.replace(/_/g, " ")}
                    {!d.obtainable && " (may not be obtainable)"}
                  </div>
                ))}
              </div>
            )}
        </>
      )}
    </div>
  );
}
