"use client";

import { use, useCallback, useEffect, useState } from "react";
import { api, DecisionResponse, DisputeCase, EvidenceNodeOut } from "@/lib/api";
import ProofTree from "@/components/ProofTree";
import EvidenceViewer from "@/components/EvidenceViewer";
import StatusStream from "@/components/StatusStream";

function formatMoney(minor: number, currency: string) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency }).format(minor / 100);
}

export default function MerchantConsolePage({ params }: { params: Promise<{ caseId: string }> }) {
  const { caseId } = use(params);
  const [dispute, setDispute] = useState<DisputeCase | null>(null);
  const [decision, setDecision] = useState<DecisionResponse | null>(null);
  const [nodes, setNodes] = useState<EvidenceNodeOut[]>([]);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [d, g] = await Promise.all([api.getCase(caseId), api.getGraph(caseId)]);
      setDispute(d);
      setNodes(g.nodes);
      try {
        const dec = await api.getDecision(caseId);
        setDecision(dec);
      } catch {
        setDecision(null); // not adjudicated yet
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [caseId]);

  useEffect(() => {
    load();
  }, [load]);

  if (error) {
    return (
      <div className="mx-auto max-w-4xl p-6">
        <div className="rounded border border-red-300 bg-red-50 p-4 text-red-800 dark:bg-red-950 dark:text-red-200">
          Could not load case {caseId}: {error}
        </div>
      </div>
    );
  }

  if (!dispute) {
    return <div className="p-6 text-sm text-neutral-500">Loading case {caseId}…</div>;
  }

  const won = decision?.outcome === "MERCHANT_PREVAILS";

  return (
    <div className="mx-auto max-w-6xl space-y-4 p-6">
      <header className="rounded border border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <div className="text-lg font-bold">
              Case #{caseId.slice(0, 8)} · {dispute.reason_code} · {formatMoney(dispute.amount_minor, dispute.currency)}
            </div>
            <div className="text-sm text-neutral-500">
              status: <span className="font-medium">{dispute.state}</span>
              {decision && (
                <>
                  {" "}·{" "}
                  <span className={won ? "text-emerald-700" : "text-red-700"}>
                    {won ? "you prevailed" : "card member prevailed"}
                  </span>
                </>
              )}
            </div>
          </div>
          {!decision && (
            <button
              onClick={async () => {
                await api.adjudicate(caseId);
                await load();
              }}
              className="rounded bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700"
            >
              Run adjudication
            </button>
          )}
        </div>

        {decision?.merchant_silent && (
          <div className="mt-3 rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:bg-amber-950 dark:text-amber-100">
            ⚡ You did not submit evidence. We adjudicated on Amex records alone. Under the standard flow this
            would have been an automatic concession.
          </div>
        )}
        {decision && decision.llm_rejections > 0 && (
          <div className="mt-3 rounded border border-neutral-300 bg-neutral-50 p-3 text-sm text-neutral-600 dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-400">
            🛡 {decision.llm_rejections} advocate assertion{decision.llm_rejections > 1 ? "s" : ""} could not be
            independently verified against the evidence graph and{" "}
            {decision.llm_rejections > 1 ? "were" : "was"} discarded before adjudication — this did not change
            the outcome, only what the referee was allowed to see.
          </div>
        )}
      </header>

      <StatusStream caseId={caseId} onTerminal={load} />

      {decision && (
        <>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <section>
              <h2 className="mb-2 text-sm font-bold uppercase tracking-wide text-neutral-500">Why</h2>
              <ProofTree
                proofTree={decision.proof_tree}
                outcome={decision.outcome}
                onSelectEvidence={(id) => {
                  setSelectedNodeId(id);
                  document.getElementById(`evidence-${id}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
                }}
              />
              {decision.abstained && decision.escalation_reason && (
                <div className="mt-2 rounded border border-neutral-300 bg-neutral-50 p-3 text-sm dark:border-neutral-700 dark:bg-neutral-900">
                  <span className="font-semibold">Escalated to human review.</span> {decision.escalation_reason}
                </div>
              )}
            </section>
            <section>
              <h2 className="mb-2 text-sm font-bold uppercase tracking-wide text-neutral-500">Evidence</h2>
              <EvidenceViewer nodes={nodes} selectedNodeId={selectedNodeId} />
            </section>
          </div>

          <section className="rounded border border-neutral-200 bg-white p-3 dark:border-neutral-800 dark:bg-neutral-900">
            <h2 className="mb-2 text-sm font-bold uppercase tracking-wide text-neutral-500">
              What would have changed this
            </h2>
            {Object.entries(decision.counterfactuals).map(([outcome, cf]) => {
              if (outcome === decision.outcome) return null;
              if (cf.already_satisfied) {
                return (
                  <div key={outcome} className="text-sm text-neutral-500">
                    {outcome}: already satisfied by current evidence
                  </div>
                );
              }
              if (cf.delta.length === 0) {
                return (
                  <div key={outcome} className="text-sm text-neutral-500">
                    {outcome}: no reachable path given the current rulepack
                  </div>
                );
              }
              return (
                <div key={outcome} className="text-sm">
                  → {outcome} would have prevailed if:{" "}
                  {cf.delta.map((d, i) => (
                    <span key={d.predicate}>
                      {i > 0 && ", "}
                      {d.action === "RETRACT" ? "retract " : "obtain "}
                      {d.predicate.replace(/_/g, " ")}
                      {!d.obtainable && " [not obtainable]"}
                    </span>
                  ))}{" "}
                  ({cf.delta.length} change{cf.delta.length > 1 ? "s" : ""})
                </div>
              );
            })}
          </section>
        </>
      )}
    </div>
  );
}
