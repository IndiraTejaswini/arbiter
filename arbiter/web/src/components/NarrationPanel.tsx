import { cx } from "@/lib/format";
import type { Narration } from "@/lib/types";
import { Badge, EmptyState } from "./ui";

/**
 * The decision, in prose.
 *
 * Stated as the gap it closes: `arbiter.narrate` ran on every single
 * adjudication and its output was thrown away — computed by the pipeline,
 * returned to the worker, discarded there, with no column to store it in and
 * no field on the decision response to carry it. An entire guarded LLM
 * boundary (CLAUDE.md invariant #5: one ungrounded sentence discards the
 * whole narration) produced text no card member, merchant, analyst, or
 * auditor could ever read.
 *
 * Two things here are not decoration:
 *
 *   - **Citations are clickable.** Every sentence that rests on evidence
 *     carries the node ids that established it, and those jump to the node.
 *     Prose that cannot be traced back to the evidence graph is exactly the
 *     unfalsifiable explanation this system exists to replace.
 *   - **The renderer is named.** `template_fallback` means a generated
 *     narration was produced and then DISCARDED for citing a node that does
 *     not exist. A reader is entitled to know they are looking at the
 *     deterministic backstop rather than the fuller explanation — hiding
 *     that would make a caught hallucination indistinguishable from a case
 *     that simply had little to say.
 */

const SOURCE_LABEL: Record<string, { label: string; hint: string; tone: "neutral" | "good" | "warn" }> = {
  template: {
    label: "deterministic template",
    hint: "Rendered directly from the proof tree. Faithful by construction — no model wrote this.",
    tone: "neutral",
  },
  llm_exception_path: {
    label: "generated, citation-verified",
    hint: "A model wrote this and every sentence's citation resolved to a real evidence node. One that did not would have discarded the whole thing.",
    tone: "good",
  },
  template_fallback: {
    label: "fell back — grounding failed",
    hint: "A generated narration cited an evidence node that does not exist on this case, so the ENTIRE narration was discarded and this deterministic text rendered instead. Grounding is all-or-nothing; there is no partial acceptance.",
    tone: "warn",
  },
  eligibility_gate: {
    label: "chargeback-right gate",
    hint: "This case ended before the referee ran, so there is no proof tree to narrate and no evidence to cite.",
    tone: "neutral",
  },
};

export default function NarrationPanel({
  narration, onSelectEvidence, selectedNodeId,
}: {
  narration: Narration | Record<string, never>;
  onSelectEvidence?: (nodeId: string) => void;
  selectedNodeId?: string | null;
}) {
  // Decisions written before narration was persisted return `{}`. That is a
  // real state with a real explanation, not an error, and saying so beats
  // rendering an empty box.
  if (!("text" in narration) || !narration.text) {
    return (
      <EmptyState
        title="No narration recorded"
        description="This decision predates the narration record. The proof tree above is the machine-checked derivation and remains the authoritative explanation."
      />
    );
  }

  const meta = SOURCE_LABEL[narration.source] ?? {
    label: narration.source,
    hint: "Unrecognised renderer.",
    tone: "neutral" as const,
  };

  // Sentences come FROM THE BACKEND, because `sentence_idx` indexes the
  // backend's split. Re-deriving it here was wrong twice over: it broke
  // mid-citation on "12 CFR 1005.11)" — which these sentences routinely
  // contain — and, since a different split has a different length, it
  // attached citations to the wrong sentence or dropped them entirely.
  //
  // The fallback is for decisions persisted before `sentences` was part of
  // the payload. It preserves the text's own line breaks rather than
  // guessing at punctuation, so an unsplittable paragraph renders whole
  // instead of mangled. Citations are suppressed in that case: an index into
  // a split we do not have is not something to approximate.
  const backendSentences = Array.isArray(narration.sentences) ? narration.sentences : null;
  const sentences = backendSentences ?? narration.text.split("\n").filter((s) => s.trim());
  const canPlaceCitations = backendSentences !== null;
  const byIndex = new Map<number, string[]>();
  for (const citation of narration.citations) {
    const list = byIndex.get(citation.sentence_idx) ?? [];
    if (!list.includes(citation.node_id)) list.push(citation.node_id);
    byIndex.set(citation.sentence_idx, list);
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge
          title={meta.hint}
          className={cx(
            meta.tone === "good"
              ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"
              : meta.tone === "warn"
              ? "bg-orange-100 text-orange-900 dark:bg-orange-950 dark:text-orange-300"
              : "",
          )}
        >
          {meta.label}
        </Badge>
        {narration.citations.length > 0 && (
          <Badge title="Every one of these resolved to a real evidence node on this case before the narration was accepted.">
            {narration.citations.length} citation{narration.citations.length === 1 ? "" : "s"}
          </Badge>
        )}
      </div>

      {narration.source === "template_fallback" && (
        <div
          role="alert"
          className="rounded-lg border border-orange-300 bg-orange-50 p-3 dark:border-orange-900 dark:bg-orange-950/40"
        >
          <p className="text-xs font-semibold text-orange-900 dark:text-orange-200">
            A generated explanation was discarded
          </p>
          <p className="mt-1 text-2xs leading-relaxed text-orange-800 dark:text-orange-300">
            It cited an evidence node that does not exist on this case. One ungrounded
            sentence voids the entire narration — so what you are reading is the
            deterministic backstop. The verdict itself is unaffected: no narration has ever
            been an input to it.
          </p>
        </div>
      )}

      <div className="space-y-2">
        {sentences.map((sentence, i) => {
          const cited = canPlaceCitations ? byIndex.get(i) ?? [] : [];
          return (
            <p key={i} className="text-sm leading-relaxed text-neutral-700 dark:text-neutral-300">
              {sentence.trim()}
              {cited.length > 0 && (
                <span className="ml-1.5 inline-flex flex-wrap gap-1 align-baseline">
                  {cited.map((nodeId) => (
                    <button
                      key={nodeId}
                      type="button"
                      onClick={() => onSelectEvidence?.(nodeId)}
                      title="Show the evidence node this sentence cites"
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
                </span>
              )}
            </p>
          );
        })}
      </div>

      <p className="border-t border-neutral-200 pt-2.5 text-2xs leading-relaxed text-neutral-500 dark:border-neutral-800 dark:text-neutral-400">
        Narration explains the decision; it never participates in it. Every sentence's
        citations are checked against the evidence graph before the text is stored, and a
        single citation that does not resolve discards the whole output.
      </p>
    </div>
  );
}
