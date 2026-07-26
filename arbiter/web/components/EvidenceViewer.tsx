"use client";

import { EvidenceNodeOut } from "@/lib/api";

const TIER_LABEL: Record<string, string> = {
  COMMITTED: "COMMITTED (ADEC-verified)",
  NETWORK: "NETWORK (Amex-held)",
  SUBMITTED: "SUBMITTED (party-supplied, unverified)",
  ASSERTED: "ASSERTED (narrative claim)",
};

export default function EvidenceViewer({
  nodes,
  selectedNodeId,
}: {
  nodes: EvidenceNodeOut[];
  selectedNodeId: string | null;
}) {
  if (nodes.length === 0) {
    return (
      <div className="rounded border border-dashed border-neutral-300 p-4 text-sm text-neutral-500 dark:border-neutral-700">
        No evidence gathered yet.
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {nodes.map((n) => {
        const isSelected = n.node_id === selectedNodeId;
        const predicate = n.attrs["asserts_predicate"] as string | undefined;
        const value = n.attrs["predicate_value"];
        return (
          <div
            key={n.node_id}
            id={`evidence-${n.node_id}`}
            className={`rounded border p-2 text-sm transition-colors ${
              isSelected
                ? "border-blue-500 bg-blue-50 dark:bg-blue-950"
                : "border-neutral-200 dark:border-neutral-800"
            }`}
          >
            <div className="flex items-center justify-between">
              <span className="font-mono text-xs text-neutral-500">{n.node_id.slice(0, 8)}</span>
              <span className={`tier-badge tier-${n.provenance}`}>{TIER_LABEL[n.provenance] ?? n.provenance}</span>
            </div>
            <div className="mt-1 font-medium">{n.node_type.replace(/_/g, " ")}</div>
            {predicate && (
              <div className="mt-0.5 text-neutral-600 dark:text-neutral-400">
                {predicate.replace(/_/g, " ")} = <span className="font-semibold">{String(value)}</span>
              </div>
            )}
            {n.extract_conf !== null && n.extract_conf < 0.95 && (
              <div className="mt-0.5 text-xs text-amber-600">extraction confidence {(n.extract_conf * 100).toFixed(0)}%</div>
            )}
            {n.source_ref?.page != null && (
              <div className="mt-0.5 text-xs text-neutral-400">
                artifact {n.source_ref.artifact_id.slice(0, 8)}, page {n.source_ref.page + 1}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
