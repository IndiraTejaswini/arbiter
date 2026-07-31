import { cx, humanize } from "@/lib/format";
import type { ContradictionAnalysis } from "@/lib/types";
import { Badge } from "./ui";

/**
 * Per-layer status of the MANDATORY four-layer contradiction pipeline.
 *
 * This exists because an empty findings list is ambiguous between "this
 * case is clean" and "a mandatory safety check could not run", and
 * collapsing those two is exactly how the semantic layer sat dead for an
 * entire build while reporting success on every case.
 *
 * The layer that most often reports UNAVAILABLE is the semantic one, since
 * it needs DeBERTa weights present. When it does, the case escalates —
 * correct fail-closed behaviour — and a reviewer looking at that case
 * deserves to know that is *why*, rather than inferring it from a
 * confidence number.
 */

const LAYER_DESCRIPTIONS: Record<string, string> = {
  temporal: "Allen interval algebra over dated evidence, plus domain ordering constraints",
  numeric: "Order → authorization → settlement → refund reconciliation",
  identity: "Address, device, IP, and email coherence across sources",
  semantic: "DeBERTa-v3-MNLI cross-encoder over claim pairs about the same fact",
};

const ORDER = ["temporal", "numeric", "identity", "semantic"];

export default function ContradictionStatus({ analysis }: { analysis: ContradictionAnalysis }) {
  // `layer_status` is absent whenever the pipeline never reached the
  // contradiction layers — every case ended by the chargeback-right gate
  // carries `contradiction_analysis: {}`. The caller's `&&` guard does not
  // catch it, because `{}` is truthy, so this threw
  // "Cannot use 'in' operator to search for 'temporal' in undefined" and
  // took the whole case page down with it.
  //
  // Rendering nothing is right here, not an "unknown" placeholder: the four
  // layers did not fail to run, they were never reached, and the panel that
  // explains why the case ended early is the chargeback-right one.
  const layerStatus = analysis?.layer_status;
  if (!layerStatus) return null;

  const entries = ORDER.filter((layer) => layer in layerStatus);
  if (entries.length === 0) return null;

  return (
    <div className="space-y-3">
      {analysis.must_escalate && (
        <div
          role="alert"
          className="rounded-lg border border-orange-300 bg-orange-50 p-3 dark:border-orange-900 dark:bg-orange-950/40"
        >
          <p className="text-xs font-semibold text-orange-900 dark:text-orange-200">
            A mandatory check could not run — this case was escalated for that reason alone
          </p>
          <p className="mt-1 text-2xs leading-relaxed text-orange-800 dark:text-orange-300">
            {analysis.unavailable_layers.map(humanize).join(", ")} did not execute.
            An unrunnable mandatory check is an unknown, not a clean bill of health, so
            auto-resolution is blocked regardless of how the case otherwise scored.
            {analysis.unavailable_reason && (
              <>
                {" "}
                <span className="font-mono">{analysis.unavailable_reason}</span>
              </>
            )}
          </p>
        </div>
      )}

      <ul className="grid gap-2 sm:grid-cols-2">
        {entries.map((layer) => {
          const status = layerStatus[layer]!;
          return (
            <li
              key={layer}
              className={cx(
                "rounded-lg border p-2.5",
                status === "UNAVAILABLE"
                  ? "border-orange-300 bg-orange-50 dark:border-orange-900 dark:bg-orange-950/30"
                  : "border-neutral-200 dark:border-neutral-800",
              )}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs font-medium capitalize text-neutral-800 dark:text-neutral-200">
                  {layer}
                </span>
                <Badge
                  className={
                    status === "OK"
                      ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"
                      : status === "UNAVAILABLE"
                      ? "bg-orange-100 text-orange-900 dark:bg-orange-950 dark:text-orange-300"
                      : "bg-neutral-100 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-400"
                  }
                  title={
                    status === "NOT_APPLICABLE"
                      ? "Nothing on this case for that layer to compare — distinct from a check that could not run."
                      : undefined
                  }
                >
                  {status === "NOT_APPLICABLE" ? "n/a" : status.toLowerCase()}
                </Badge>
              </div>
              <p className="mt-1 text-2xs leading-relaxed text-neutral-500 dark:text-neutral-400">
                {LAYER_DESCRIPTIONS[layer]}
                {layer === "semantic" && analysis.semantic_pairs_evaluated > 0 && (
                  <> · {analysis.semantic_pairs_evaluated} claim pair
                    {analysis.semantic_pairs_evaluated === 1 ? "" : "s"} evaluated</>
                )}
              </p>
            </li>
          );
        })}
      </ul>

      <p className="text-2xs leading-relaxed text-neutral-500 dark:text-neutral-500">
        All four layers are mandatory and fully deterministic. No generative model
        participates in any of them — the semantic layer is a DeBERTa-NLI cross-encoder,
        exclusively.
      </p>
    </div>
  );
}
