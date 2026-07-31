import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { cx } from "@/lib/format";
import type { StageEvent } from "@/lib/types";
import { Badge } from "./ui";

/**
 * Live adjudication progress over Server-Sent Events.
 *
 * `EventSource` cannot set request headers, so the stream route accepts a
 * short-lived token in the query string (`POST /v1/auth/stream-token`).
 * Putting the hour-long session token in a URL that lands in access logs,
 * proxy logs, and `Referer` would be the lazy version of this; a
 * 60-second token bounds the exposure to roughly the time it takes to open
 * the connection.
 */

/**
 * Mirrors `arbiter.realtime.events.STAGES` exactly — this list is the
 * contract, and drift between the two shows up as a step that never lights.
 *
 * Two corrections are baked in here. `CHECKING_CHARGEBACK_RIGHT` is the
 * FIRST stage of every adjudication and was missing, so the bar moved to 5%
 * with nothing highlighted and the pipeline's most decisive gate was
 * invisible while it ran. And there is no "Classify intent" step: intent
 * classification happens in `POST /v1/disputes`, before a case exists, so
 * there is no `case:{id}` channel it could ever arrive on — it was listed
 * here as a step that structurally could not be reached.
 */
const STAGES = [
  { id: "CHECKING_CHARGEBACK_RIGHT", label: "Chargeback right" },
  { id: "GATHERING_NETWORK", label: "Gather Amex records" },
  { id: "PARSING_EVIDENCE", label: "Parse evidence" },
  { id: "VERIFYING_PROVENANCE", label: "Verify ADEC" },
  { id: "BUILDING_GRAPH", label: "Contradiction analysis" },
  { id: "CONSTRUCTING_ARGUMENTS", label: "Dual advocacy" },
  { id: "ADJUDICATING", label: "Referee" },
] as const;

const TERMINAL = new Set(["DECIDED", "ESCALATED"]);

export default function StatusStream({
  caseId, onTerminal,
}: { caseId: string; onTerminal?: () => void }) {
  const [events, setEvents] = useState<StageEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const [terminal, setTerminal] = useState<StageEvent | null>(null);
  const onTerminalRef = useRef(onTerminal);
  useEffect(() => { onTerminalRef.current = onTerminal; }, [onTerminal]);

  useEffect(() => {
    let source: EventSource | null = null;
    let cancelled = false;

    (async () => {
      const url = await api.streamUrl(caseId);
      if (cancelled || !url) return;

      source = new EventSource(url);
      source.addEventListener("connected", () => !cancelled && setConnected(true));
      source.addEventListener("stage", (event) => {
        if (cancelled) return;
        try {
          const data = JSON.parse((event as MessageEvent).data) as StageEvent;
          setEvents((prev) => [...prev, data]);
          if (TERMINAL.has(data.stage)) {
            setTerminal(data);
            onTerminalRef.current?.();
          }
        } catch {
          /* ping frames and malformed payloads are ignored */
        }
      });
      source.onerror = () => !cancelled && setConnected(false);
    })();

    return () => {
      cancelled = true;
      source?.close();
    };
  }, [caseId]);

  const latest = events[events.length - 1];
  // Monotonic, and computed over every event received rather than only the
  // last one. Using `findIndex` on the latest stage alone meant a stage this
  // build does not know about — a newly added pipeline step, or one arriving
  // out of order — returned -1 and darkened every step that had already
  // completed, so the UI appeared to run backwards. Progress that only ever
  // moves forward is the honest rendering of a pipeline that only ever
  // moves forward.
  const reachedIndex = events.reduce((furthest, event) => {
    const index = STAGES.findIndex((s) => s.id === event.stage);
    return index > furthest ? index : furthest;
  }, -1);
  const progress = terminal ? 1 : latest?.progress ?? 0;

  return (
    <div
      className="rounded-xl border border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900"
      role="region"
      aria-label="Live adjudication progress"
    >
      <div className="mb-3 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span
            aria-hidden="true"
            className={cx(
              "h-2 w-2 rounded-full",
              connected ? "animate-pulse bg-emerald-500" : "bg-neutral-300 dark:bg-neutral-700",
            )}
          />
          <span className="text-xs font-medium text-neutral-700 dark:text-neutral-300">
            {connected ? "Live" : "Not connected"}
          </span>
          <span className="sr-only" aria-live="polite">
            {latest ? `${latest.stage}: ${latest.message}` : "Waiting for adjudication to start"}
          </span>
        </div>
        {terminal && (
          <Badge className={terminal.stage === "DECIDED"
            ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"
            : "bg-orange-100 text-orange-900 dark:bg-orange-950 dark:text-orange-300"}>
            {terminal.stage}
          </Badge>
        )}
      </div>

      <div
        className="h-1 w-full overflow-hidden rounded-full bg-neutral-200 dark:bg-neutral-800"
        role="progressbar"
        aria-valuenow={Math.round(progress * 100)}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="Adjudication progress"
      >
        <div
          className="h-full rounded-full bg-brand-500 transition-[width] duration-500 ease-out"
          style={{ width: `${Math.round(progress * 100)}%` }}
        />
      </div>

      <ol className="mt-3 flex flex-wrap gap-1.5">
        {STAGES.map((stage, i) => {
          const reached = terminal ? true : reachedIndex >= i;
          const current = !terminal && reachedIndex === i;
          return (
            <li key={stage.id}>
              <span className={cx(
                "inline-block rounded-md px-2 py-0.5 text-2xs font-medium transition-colors",
                current
                  ? "bg-brand-600 text-white"
                  : reached
                  ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"
                  : "bg-neutral-100 text-neutral-400 dark:bg-neutral-800 dark:text-neutral-600",
              )}>
                {stage.label}
              </span>
            </li>
          );
        })}
      </ol>

      {latest && (
        <p className="mt-2 text-xs text-neutral-600 dark:text-neutral-400">{latest.message}</p>
      )}
      {!latest && (
        <p className="mt-2 text-xs text-neutral-500 dark:text-neutral-500">
          Progress appears here while the case is being adjudicated.
        </p>
      )}
    </div>
  );
}
