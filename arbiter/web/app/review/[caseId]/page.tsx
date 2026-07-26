"use client";

import { use, useCallback, useEffect, useState } from "react";
import { api, DecisionResponse, EvidenceNodeOut } from "@/lib/api";
import ProofTree from "@/components/ProofTree";
import EvidenceViewer from "@/components/EvidenceViewer";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export default function ReviewPage({ params }: { params: Promise<{ caseId: string }> }) {
  const { caseId } = use(params);
  const [decision, setDecision] = useState<DecisionResponse | null>(null);
  const [nodes, setNodes] = useState<EvidenceNodeOut[]>([]);
  const [reviewerId, setReviewerId] = useState("");
  const [notes, setNotes] = useState("");
  const [submitted, setSubmitted] = useState(false);

  const load = useCallback(async () => {
    const [dec, graph] = await Promise.all([api.getDecision(caseId), api.getGraph(caseId)]);
    setDecision(dec);
    setNodes(graph.nodes);
  }, [caseId]);

  useEffect(() => {
    load();
  }, [load]);

  if (!decision) return <div className="p-6 text-sm text-neutral-500">Loading…</div>;

  const outcomes = Object.keys(decision.counterfactuals);
  const distances = outcomes.map((o) => decision.counterfactuals[o].delta.length);
  const closeCall = distances.length === 2 && Math.abs(distances[0] - distances[1]) <= 1;

  async function submitReview(outcome: string) {
    await fetch(`${API_BASE}/v1/cases/${caseId}/review-decision`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ outcome, reviewer_id: reviewerId || "analyst", notes }),
    });
    setSubmitted(true);
  }

  return (
    <div className="mx-auto max-w-5xl space-y-4 p-6">
      <header>
        <h1 className="text-lg font-bold">Escalated review · Case #{caseId.slice(0, 8)}</h1>
        <p className="text-sm text-neutral-500">{decision.escalation_reason}</p>
      </header>

      {closeCall && (
        <div className="rounded border border-blue-300 bg-blue-50 p-3 text-sm text-blue-900 dark:bg-blue-950 dark:text-blue-100">
          Both sides are one predicate from prevailing. This is why the system abstained.
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <section>
          <h2 className="mb-2 text-sm font-bold uppercase tracking-wide text-neutral-500">Proof tree</h2>
          <ProofTree proofTree={decision.proof_tree} outcome={decision.outcome} onSelectEvidence={() => {}} />
          <div className="mt-3 space-y-1">
            {outcomes.map((o) => (
              <div key={o} className="text-sm">
                <span className="font-semibold">{o}:</span>{" "}
                {decision.counterfactuals[o].already_satisfied
                  ? "already satisfied"
                  : decision.counterfactuals[o].delta.length === 0
                  ? "no reachable path"
                  : `${decision.counterfactuals[o].delta.length} predicate(s) away`}
              </div>
            ))}
          </div>
        </section>
        <section>
          <h2 className="mb-2 text-sm font-bold uppercase tracking-wide text-neutral-500">Evidence</h2>
          <EvidenceViewer nodes={nodes} selectedNodeId={null} />
        </section>
      </div>

      <section className="rounded border border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900">
        <h2 className="mb-2 text-sm font-bold uppercase tracking-wide text-neutral-500">Decision</h2>
        {submitted ? (
          <div className="text-emerald-700">Recorded — thank you. This feeds the calibration set.</div>
        ) : (
          <>
            <input
              className="mb-2 w-full rounded border border-neutral-300 px-2 py-1 text-sm dark:border-neutral-700 dark:bg-neutral-800"
              placeholder="reviewer id"
              value={reviewerId}
              onChange={(e) => setReviewerId(e.target.value)}
            />
            <textarea
              className="mb-2 w-full rounded border border-neutral-300 px-2 py-1 text-sm dark:border-neutral-700 dark:bg-neutral-800"
              placeholder="notes (optional)"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
            />
            <div className="flex gap-2">
              <button
                onClick={() => submitReview("MERCHANT_PREVAILS")}
                className="rounded bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-700"
              >
                Merchant prevails
              </button>
              <button
                onClick={() => submitReview("CARD_MEMBER_PREVAILS")}
                className="rounded bg-red-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-700"
              >
                Card member prevails
              </button>
              <button
                onClick={() => submitReview("SPLIT")}
                className="rounded bg-neutral-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-neutral-700"
              >
                Split
              </button>
            </div>
          </>
        )}
      </section>
    </div>
  );
}
