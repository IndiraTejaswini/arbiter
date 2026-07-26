"use client";

import { ProofNode } from "@/lib/api";

function TierNote() {
  return null;
}

function LiteralRow({
  predicate,
  negated,
  satisfied,
  confidence,
  evidenceNodeIds,
  child,
  depth,
  onSelectEvidence,
}: {
  predicate: string;
  negated: boolean;
  satisfied: boolean;
  confidence: number;
  evidenceNodeIds: string[];
  child?: ProofNode | null;
  depth: number;
  onSelectEvidence: (nodeId: string) => void;
}) {
  const label = predicate.replace(/_/g, " ");
  return (
    <div style={{ marginLeft: depth * 16 }} className="py-1">
      <div className="flex items-center gap-2 text-sm">
        <span className={satisfied ? "text-emerald-600" : "text-red-500"}>{satisfied ? "✓" : "✗"}</span>
        <span className={negated ? "italic text-neutral-500" : ""}>
          {negated ? `not ${label}` : label}
        </span>
        {confidence < 1 && (
          <span className="text-xs text-neutral-400">conf {(confidence * 100).toFixed(0)}%</span>
        )}
        {evidenceNodeIds.map((id) => (
          <button
            key={id}
            onClick={() => onSelectEvidence(id)}
            className="rounded border border-neutral-300 px-1.5 text-xs text-neutral-600 hover:border-blue-400 hover:text-blue-600 dark:border-neutral-700 dark:text-neutral-400"
            title="Jump to supporting evidence"
          >
            {id.slice(0, 8)}
          </button>
        ))}
      </div>
      {child && <ProofTreeNode node={child} depth={depth + 1} onSelectEvidence={onSelectEvidence} />}
    </div>
  );
}

export function ProofTreeNode({
  node,
  depth = 0,
  onSelectEvidence,
}: {
  node: ProofNode;
  depth?: number;
  onSelectEvidence: (nodeId: string) => void;
}) {
  return (
    <div style={{ marginLeft: depth === 0 ? 0 : 8 }}>
      {node.rule_id && (
        <div className="mb-0.5 text-xs font-semibold uppercase tracking-wide text-neutral-500">
          rule {node.rule_id} fired
        </div>
      )}
      {node.description && (
        <div className="mb-1 text-sm text-neutral-600 dark:text-neutral-400">{node.description}</div>
      )}
      {node.literals.map((lit, i) => (
        <LiteralRow
          key={`${lit.predicate}-${i}`}
          predicate={lit.predicate}
          negated={lit.negated}
          satisfied={lit.satisfied}
          confidence={lit.confidence}
          evidenceNodeIds={lit.evidence_node_ids}
          child={lit.child}
          depth={depth}
          onSelectEvidence={onSelectEvidence}
        />
      ))}
    </div>
  );
}

export default function ProofTree({
  proofTree,
  outcome,
  onSelectEvidence,
}: {
  proofTree: ProofNode | null;
  outcome: string;
  onSelectEvidence: (nodeId: string) => void;
}) {
  if (!proofTree) {
    return (
      <div className="rounded border border-dashed border-neutral-300 p-4 text-sm text-neutral-500 dark:border-neutral-700">
        No proof tree available for this case yet.
      </div>
    );
  }
  const won = outcome === "MERCHANT_PREVAILS";
  return (
    <div className="rounded border border-neutral-200 bg-white p-3 dark:border-neutral-800 dark:bg-neutral-900">
      <div className={`mb-2 text-sm font-bold ${won ? "text-emerald-700" : "text-red-700"}`}>
        {outcome} {won ? "✓" : ""}
      </div>
      <ProofTreeNode node={proofTree} onSelectEvidence={onSelectEvidence} />
    </div>
  );
}
