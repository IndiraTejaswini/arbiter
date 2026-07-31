import { useEffect, useState } from "react";
import { api } from "./api";
import { subscribe } from "./session";
import type { RulepackSummary } from "./types";

/**
 * The loaded reason codes, from the server.
 *
 * Stated as the gap it closes: the console hardcoded `F29 / C08 / C02` and
 * their descriptions in three separate files — the filing form, the case
 * filter, and the case-detail header. A fourth rulepack dropped into
 * `rulepacks/amex/` was adjudicable by the backend and completely invisible
 * in the UI: unfilable, unfilterable, and rendered with a bare code where
 * every other case showed a name. "Adding a reason code is a YAML file" was
 * true of the engine and false of the product.
 *
 * **Module-scoped cache, deliberately.** The catalogue is small, changes
 * only when the server reloads its rulepack directory, and is needed by
 * three unrelated routes. Refetching it per mount would put three identical
 * requests on every navigation to buy staleness nobody would notice. It is
 * dropped on sign-out with the rest of the session, because a different
 * actor may be served a different catalogue.
 */

let cache: RulepackSummary[] | null = null;
let inflight: Promise<RulepackSummary[]> | null = null;
const subscribers = new Set<(packs: RulepackSummary[]) => void>();

function load(): Promise<RulepackSummary[]> {
  if (cache) return Promise.resolve(cache);
  if (!inflight) {
    inflight = api.listRulepacks()
      .then(({ rulepacks }) => {
        cache = rulepacks;
        subscribers.forEach((notify) => notify(rulepacks));
        return rulepacks;
      })
      .catch((error) => {
        // Allow a later mount to retry rather than caching the failure. A
        // party token can read this route, so a failure here is a real
        // outage, not an authorization boundary — and the callers below
        // degrade to showing the raw reason code rather than an empty list.
        inflight = null;
        throw error;
      });
  }
  return inflight;
}

/** Clears the catalogue. Wired to sign-out below; exported for tests. */
export function clearRulepackCache(): void {
  cache = null;
  inflight = null;
}

// Server-derived caches die with the session: a different actor may be
// served a different catalogue, and carrying one identity's view of the
// system into the next is how a console starts offering a user something
// they cannot reach.
//
// The dependency runs THIS way — the cache subscribes to the session —
// rather than `clearSession()` reaching in here. The other direction is a
// cycle (session → useRulepacks → api → session) that only a dynamic import
// breaks, and a dynamic import taken to dodge a cycle is a cycle you still
// have, minus the ability to chunk-split it.
subscribe((session) => {
  if (session === null) clearRulepackCache();
});

export function useRulepacks(): {
  rulepacks: RulepackSummary[];
  loading: boolean;
  error: Error | null;
} {
  const [rulepacks, setRulepacks] = useState<RulepackSummary[]>(cache ?? []);
  const [loading, setLoading] = useState(cache === null);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    let cancelled = false;
    if (cache) {
      setRulepacks(cache);
      setLoading(false);
      return;
    }
    setLoading(true);
    load()
      .then((packs) => { if (!cancelled) { setRulepacks(packs); setError(null); } })
      .catch((caught) => {
        if (!cancelled) setError(caught instanceof Error ? caught : new Error(String(caught)));
      })
      .finally(() => { if (!cancelled) setLoading(false); });

    const notify = (packs: RulepackSummary[]) => { if (!cancelled) setRulepacks(packs); };
    subscribers.add(notify);
    return () => { cancelled = true; subscribers.delete(notify); };
  }, []);

  return { rulepacks, loading, error };
}

/**
 * A reason code's human name.
 *
 * Falls back to the code itself rather than to a hardcoded table: an
 * unrecognised code means the catalogue has not loaded or the server knows
 * something this bundle does not, and showing `C14` is honest where showing
 * a guessed label would not be.
 */
export function useReasonCodeLabel(reasonCode: string): string {
  const { rulepacks } = useRulepacks();
  return rulepacks.find((p) => p.reason_code === reasonCode)?.title ?? reasonCode;
}
