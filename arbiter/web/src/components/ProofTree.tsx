import { useState } from "react";
import { cx, humanize } from "@/lib/format";
import type { LiteralWitness, ProofNode } from "@/lib/types";
import { Badge, Disclosure, EmptyState } from "./ui";

/**
 * The proof tree — the artifact everything else in the system renders from.
 *
 * This is the single most important view in the console: it is what turns
 * "the system decided X" into "the system decided X because these rules
 * fired on these specific pieces of evidence, under this legal provision."
 * GDPR Art. 22(3)'s "meaningful information about the logic involved" is
 * satisfied here or not at all.
 *
 * Implemented as an ARIA tree so it is navigable by keyboard and announced
 * correctly — a compliance reviewer using a screen reader must be able to
 * read a decision's derivation, not just see it.
 */

export default function ProofTree({
  proofTree, onSelectEvidence, selectedNodeId,
}: {
  proofTree: ProofNode | null;
  onSelectEvidence?: (nodeId: string) => void;
  selectedNodeId?: string | null;
}) {
  // `!proofTree` alone was not enough: a decision with no derivation carries
  // `{}`, which is truthy, so this fell straight through to `<RuleNode>` and
  // died on `node.literals.length`. Every case ended by the chargeback-right
  // gate has exactly that shape — the whole page went to the error boundary,
  // and only for the outcome where the explanation matters most.
  //
  // Checked structurally rather than by `Object.keys(...).length`: what this
  // component needs is a node with literals, so that is what it asks for. A
  // future payload that is non-empty but still not a proof tree lands here
  // too, instead of crashing one level down.
  if (!proofTree || !Array.isArray(proofTree.literals)) {
    return (
      <EmptyState
        title="No proof tree"
        description={
          "No rule fired, so there is no derivation to show. Either the referee reached " +
          "no decision — in which case the case escalates to a human rather than " +
          "resolving on a guess — or the case ended before the referee ran, at the " +
          "chargeback-right gate."
        }
      />
    );
  }
  return (
    <ul role="tree" aria-label="Decision proof tree" className="space-y-2">
      <RuleNode
        node={proofTree}
        depth={0}
        onSelectEvidence={onSelectEvidence}
        selectedNodeId={selectedNodeId ?? null}
      />
    </ul>
  );
}

function RuleNode({
  node, depth, onSelectEvidence, selectedNodeId,
}: {
  node: ProofNode;
  depth: number;
  onSelectEvidence?: (nodeId: string) => void;
  selectedNodeId: string | null;
}) {
  const [expanded, setExpanded] = useState(true);

  return (
    <li role="treeitem" aria-expanded={expanded} aria-label={`Rule ${node.rule_id}`}>
      <div
        className={cx(
          "rounded-lg border p-3",
          node.holds
            ? "border-emerald-300 bg-emerald-50/60 dark:border-emerald-900 dark:bg-emerald-950/30"
            : "border-neutral-200 bg-neutral-50 dark:border-neutral-800 dark:bg-neutral-900",
        )}
      >
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => setExpanded((e) => !e)}
            aria-label={expanded ? `Collapse ${node.rule_id}` : `Expand ${node.rule_id}`}
            className="rounded text-neutral-400 transition-transform hover:text-neutral-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 dark:hover:text-neutral-200"
          >
            <span className={cx("inline-block", expanded && "rotate-90")} aria-hidden="true">›</span>
          </button>
          <code className="rounded bg-white px-1.5 py-0.5 font-mono text-xs font-semibold text-neutral-800 ring-1 ring-neutral-200 dark:bg-neutral-950 dark:text-neutral-200 dark:ring-neutral-700">
            {node.rule_id}
          </code>
          <span className="text-xs text-neutral-400">→</span>
          <span className="font-mono text-xs text-neutral-700 dark:text-neutral-300">{node.head}</span>
          <Badge className={node.holds
            ? "bg-emerald-100 text-emerald-800 ring-emerald-600/20 dark:bg-emerald-950 dark:text-emerald-300"
            : ""}>
            {node.holds ? "fired" : "did not fire"}
          </Badge>
        </div>

        {node.description && (
          <p className="mt-2 text-xs leading-relaxed text-neutral-600 dark:text-neutral-400">
            {node.description}
          </p>
        )}

        {/* Rule-to-regulation provenance. A compliance reviewer needs to
            read WHY a rule exists, not only what it checks. */}
        {node.legal_basis && (
          <div className="mt-2">
            <Disclosure summary="Legal basis">
              <p className="text-2xs leading-relaxed text-neutral-600 dark:text-neutral-400">
                {node.legal_basis}
              </p>
            </Disclosure>
          </div>
        )}

        {expanded && node.literals.length > 0 && (
          <ul role="group" className="mt-3 space-y-1.5">
            {node.literals.map((witness, i) => (
              <Literal
                key={`${witness.predicate}-${i}`}
                witness={witness}
                depth={depth + 1}
                onSelectEvidence={onSelectEvidence}
                selectedNodeId={selectedNodeId}
              />
            ))}
          </ul>
        )}
      </div>
    </li>
  );
}

function Literal({
  witness, depth, onSelectEvidence, selectedNodeId,
}: {
  witness: LiteralWitness;
  depth: number;
  onSelectEvidence?: (nodeId: string) => void;
  selectedNodeId: string | null;
}) {
  return (
    <li role="treeitem" className="ml-4 border-l border-neutral-200 pl-3 dark:border-neutral-800">
      <div className="flex flex-wrap items-center gap-2 py-0.5">
        <span
          aria-hidden="true"
          className={cx(
            "inline-block h-1.5 w-1.5 shrink-0 rounded-full",
            witness.satisfied ? "bg-emerald-500" : "bg-neutral-400",
          )}
        />
        <span className={cx(
          "font-mono text-xs",
          witness.satisfied
            ? "text-neutral-800 dark:text-neutral-200"
            : "text-neutral-500 line-through dark:text-neutral-500",
        )}>
          {witness.negated && <span className="text-orange-600 dark:text-orange-400">not </span>}
          {witness.predicate}
        </span>
        <span className="sr-only">{witness.satisfied ? "satisfied" : "not satisfied"}</span>

        {witness.confidence < 1 && (
          <Badge title="Evidentiary weight: provenance tier × extraction confidence (including the forensic tamper penalty)">
            w {witness.confidence.toFixed(2)}
          </Badge>
        )}

        {/* source_ref click-through: this is what makes a claim traceable
            back to the bounding box on the page it came from. */}
        {witness.evidence_node_ids.map((nodeId) => (
          <button
            key={nodeId}
            type="button"
            onClick={() => onSelectEvidence?.(nodeId)}
            title="Show the evidence node that established this"
            className={cx(
              "rounded px-1 font-mono text-2xs underline decoration-dotted underline-offset-2 transition-colors",
              "focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500",
              selectedNodeId === nodeId
                ? "bg-brand-100 text-brand-800 dark:bg-brand-950 dark:text-brand-300"
                : "text-brand-600 hover:bg-brand-50 dark:text-brand-400 dark:hover:bg-brand-950/50",
            )}
          >
            {nodeId.slice(0, 8)}
          </button>
        ))}

        {witness.negated && witness.satisfied && witness.evidence_node_ids.length === 0 && (
          <span className="text-2xs italic text-neutral-500 dark:text-neutral-500">
            satisfied by absence
          </span>
        )}
      </div>

      {witness.child && (
        <ul role="group" className="mt-1.5">
          <RuleNode
            node={witness.child}
            depth={depth + 1}
            onSelectEvidence={onSelectEvidence}
            selectedNodeId={selectedNodeId}
          />
        </ul>
      )}
    </li>
  );
}

/** Flatten every predicate the winning derivation rests on. */
export function loadBearingPredicates(node: ProofNode | null): string[] {
  if (!node) return [];
  const out = new Set<string>();
  const walk = (n: ProofNode) => {
    for (const w of n.literals) {
      out.add(humanize(w.predicate));
      if (w.child) walk(w.child);
    }
  };
  walk(node);
  return [...out].sort();
}
