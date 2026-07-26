"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

type StageMessage = {
  case_id: string;
  stage: string;
  message: string;
  progress: number;
  timestamp: string;
};

const STAGE_ORDER = [
  "GATHERING_NETWORK",
  "PARSING_EVIDENCE",
  "VERIFYING_PROVENANCE",
  "BUILDING_GRAPH",
  "CONSTRUCTING_ARGUMENTS",
  "ADJUDICATING",
  "DECIDED",
  "ESCALATED",
];

export default function StatusStream({ caseId, onTerminal }: { caseId: string; onTerminal?: () => void }) {
  const [events, setEvents] = useState<StageMessage[]>([]);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    const source = new EventSource(api.streamUrl(caseId));

    source.addEventListener("connected", () => setConnected(true));
    source.addEventListener("stage", (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data) as StageMessage;
        setEvents((prev) => [...prev, data]);
        if (data.stage === "DECIDED" || data.stage === "ESCALATED") {
          onTerminal?.();
        }
      } catch {
        // ignore malformed/ping frames
      }
    });
    source.onerror = () => setConnected(false);

    return () => source.close();
  }, [caseId, onTerminal]);

  const latest = events[events.length - 1];
  const latestIdx = latest ? STAGE_ORDER.indexOf(latest.stage) : -1;

  return (
    <div className="rounded border border-neutral-200 bg-white p-3 dark:border-neutral-800 dark:bg-neutral-900">
      <div className="mb-2 flex items-center gap-2 text-xs text-neutral-500">
        <span className={`h-2 w-2 rounded-full ${connected ? "bg-emerald-500" : "bg-neutral-300"}`} />
        live status stream
      </div>
      <div className="flex flex-wrap gap-1">
        {STAGE_ORDER.map((stage, i) => {
          const reached = latestIdx >= i;
          const isCurrent = latestIdx === i;
          return (
            <span
              key={stage}
              className={`rounded px-2 py-0.5 text-xs ${
                isCurrent
                  ? "bg-blue-600 text-white"
                  : reached
                  ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-900 dark:text-emerald-200"
                  : "bg-neutral-100 text-neutral-400 dark:bg-neutral-800"
              }`}
            >
              {stage}
            </span>
          );
        })}
      </div>
      {latest && <div className="mt-2 text-sm text-neutral-600 dark:text-neutral-400">{latest.message}</div>}
    </div>
  );
}
