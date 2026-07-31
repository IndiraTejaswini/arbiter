import { useMemo, useState } from "react";
import { TIER_MEANING, TIER_RANK, TIER_STYLE, cx, humanize } from "@/lib/format";
import type { EvidenceNodeOut, ProvenanceTier } from "@/lib/types";
import { Badge, Disclosure, EmptyState, Mono, Select, TextInput } from "./ui";

/**
 * The evidence graph, ordered by evidentiary weight.
 *
 * Sorted by provenance tier descending, because that is the order a
 * reviewer should read it in: ADEC-committed evidence is cryptographically
 * proven to predate the dispute, network evidence is Amex's own record, and
 * submitted evidence is whatever a party chose to send. Presenting them as
 * a flat undifferentiated list would erase the distinction the entire
 * provenance layer exists to draw.
 */

export default function EvidenceViewer({
  nodes, selectedNodeId, onSelect,
}: {
  nodes: EvidenceNodeOut[];
  selectedNodeId?: string | null;
  onSelect?: (nodeId: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [tierFilter, setTierFilter] = useState<ProvenanceTier | "ALL">("ALL");

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return nodes
      .filter((n) => tierFilter === "ALL" || n.provenance === tierFilter)
      .filter((n) => {
        if (!needle) return true;
        return (
          n.node_type.toLowerCase().includes(needle) ||
          n.node_id.toLowerCase().includes(needle) ||
          JSON.stringify(n.attrs).toLowerCase().includes(needle)
        );
      })
      .sort((a, b) => {
        const byTier = TIER_RANK[b.provenance] - TIER_RANK[a.provenance];
        return byTier !== 0 ? byTier : a.node_type.localeCompare(b.node_type);
      });
  }, [nodes, query, tierFilter]);

  const tierCounts = useMemo(() => {
    const counts: Partial<Record<ProvenanceTier, number>> = {};
    for (const n of nodes) counts[n.provenance] = (counts[n.provenance] ?? 0) + 1;
    return counts;
  }, [nodes]);

  if (nodes.length === 0) {
    return (
      <EmptyState
        title="No evidence on this case yet"
        description="Network evidence is loaded when the case is adjudicated; submitted documents appear here as they are uploaded."
      />
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="min-w-[10rem] flex-1">
          <TextInput
            value={query}
            onChange={setQuery}
            placeholder="Filter evidence…"
            ariaLabel="Filter evidence nodes"
          />
        </div>
        <div className="w-40">
          <Select
            value={tierFilter}
            onChange={setTierFilter}
            ariaLabel="Filter by provenance tier"
            options={[
              { value: "ALL" as const, label: `All tiers (${nodes.length})` },
              ...(["COMMITTED", "NETWORK", "SUBMITTED", "ASSERTED"] as ProvenanceTier[])
                .filter((t) => tierCounts[t])
                .map((t) => ({ value: t, label: `${t} (${tierCounts[t]})` })),
            ]}
          />
        </div>
      </div>

      {visible.length === 0 ? (
        <EmptyState title="No evidence matches this filter" />
      ) : (
        <ul className="space-y-2">
          {visible.map((node) => (
            <EvidenceCard
              key={node.node_id}
              node={node}
              selected={selectedNodeId === node.node_id}
              onSelect={onSelect}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

function EvidenceCard({
  node, selected, onSelect,
}: { node: EvidenceNodeOut; selected: boolean; onSelect?: (id: string) => void }) {
  const predicate = node.attrs["asserts_predicate"] as string | undefined;
  const predicateValue = node.attrs["predicate_value"];
  const fields = node.attrs["extracted_fields"] as
    | Array<{ field_name: string; value: unknown; confidence?: number; pan_tokenized?: boolean }>
    | undefined;

  return (
    <li
      id={`evidence-${node.node_id}`}
      onClick={() => onSelect?.(node.node_id)}
      className={cx(
        "scroll-mt-24 rounded-lg border p-3 transition-colors",
        onSelect && "cursor-pointer",
        selected
          ? "border-brand-400 bg-brand-50 ring-2 ring-brand-500/30 dark:border-brand-600 dark:bg-brand-950/40"
          : "border-neutral-200 bg-white hover:border-neutral-300 dark:border-neutral-800 dark:bg-neutral-900 dark:hover:border-neutral-700",
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <Badge className={TIER_STYLE[node.provenance]} title={TIER_MEANING[node.provenance]}>
          {node.provenance}
        </Badge>
        <span className="text-xs font-medium text-neutral-800 dark:text-neutral-200">
          {humanize(node.node_type)}
        </span>
        <Mono className="ml-auto">{node.node_id.slice(0, 8)}</Mono>
      </div>

      {predicate && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <span className="font-mono text-xs text-neutral-700 dark:text-neutral-300">{predicate}</span>
          <Badge className={predicateValue
            ? "bg-emerald-100 text-emerald-800 ring-emerald-600/20 dark:bg-emerald-950 dark:text-emerald-300"
            : "bg-neutral-100 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-400"}>
            {String(predicateValue)}
          </Badge>
        </div>
      )}

      {/* Extraction confidence is not decoration: it carries the forensic
          tamper penalty into the evidentiary weight this node contributes. */}
      {node.extract_conf !== null && node.extract_conf < 1 && (
        <p className="mt-1.5 text-2xs text-amber-700 dark:text-amber-400">
          extraction confidence {node.extract_conf.toFixed(2)} — reduced weight
          {node.extract_conf < 0.7 ? " (forensic signals present)" : ""}
        </p>
      )}

      {fields && fields.length > 0 && (
        <div className="mt-2">
          <Disclosure summary={`${fields.length} extracted field${fields.length === 1 ? "" : "s"}`}>
            <dl className="space-y-1">
              {fields.map((f, i) => (
                <div key={`${f.field_name}-${i}`} className="flex flex-wrap items-baseline gap-2">
                  <dt className="font-mono text-2xs text-neutral-500 dark:text-neutral-500">{f.field_name}</dt>
                  <dd className="font-mono text-2xs text-neutral-800 dark:text-neutral-200">
                    {String(f.value)}
                  </dd>
                  {/* PCI: a card number never reaches the datastore — the
                      surrogate is what was persisted. */}
                  {f.pan_tokenized && (
                    <Badge className="bg-violet-100 text-violet-800 dark:bg-violet-950 dark:text-violet-300"
                           title="A card number was replaced with an irreversible surrogate before this node was persisted">
                      PAN tokenized
                    </Badge>
                  )}
                  {typeof f.confidence === "number" && f.confidence < 1 && (
                    <span className="text-2xs text-neutral-400">{f.confidence.toFixed(2)}</span>
                  )}
                </div>
              ))}
            </dl>
          </Disclosure>
        </div>
      )}

      {node.source_ref?.artifact_id && (
        <p className="mt-1.5 text-2xs text-neutral-500 dark:text-neutral-500">
          from artifact <Mono>{node.source_ref.artifact_id.slice(0, 8)}</Mono>
          {node.source_ref.page !== null && ` · page ${node.source_ref.page + 1}`}
          {node.source_ref.bbox && " · bounding box recorded"}
        </p>
      )}
    </li>
  );
}
