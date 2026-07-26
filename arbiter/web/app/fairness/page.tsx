"use client";

import { useEffect, useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

type RuleInfo = { reason_code: string; rule_id: string; head: string; description: string };
type Finding = {
  rule_id: string;
  stratum_dimension: string;
  stratum_a: string;
  stratum_b: string;
  evidence_strength_bucket: number;
  firing_rate_a: number;
  firing_rate_b: number;
  delta: number;
  n_a: number;
  n_b: number;
  flagged: boolean;
};

export default function FairnessDashboard() {
  const [rules, setRules] = useState<RuleInfo[]>([]);
  const [findingsByRule, setFindingsByRule] = useState<Record<string, Finding[]>>({});
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      const res = await fetch(`${API_BASE}/v1/fairness/rules`);
      const ruleList: RuleInfo[] = await res.json();
      setRules(ruleList);

      const results: Record<string, Finding[]> = {};
      await Promise.all(
        ruleList.map(async (r) => {
          try {
            const fr = await fetch(`${API_BASE}/v1/fairness/rules/${r.rule_id}`);
            const data = await fr.json();
            results[r.rule_id] = data.findings ?? [];
          } catch {
            results[r.rule_id] = [];
          }
        })
      );
      setFindingsByRule(results);
      setLoading(false);
    })();
  }, []);

  return (
    <div className="mx-auto max-w-5xl space-y-4 p-6">
      <header>
        <h1 className="text-lg font-bold">Rule-level fairness audit</h1>
        <p className="text-sm text-neutral-500">
          Per-rule firing rate by merchant tier, conditioned on evidence strength (A7). Flagged rows exceed a
          15-point firing-rate delta between strata at equal evidence strength.
        </p>
      </header>

      {loading && <div className="text-sm text-neutral-500">Loading…</div>}

      {!loading && rules.length === 0 && (
        <div className="rounded border border-dashed border-neutral-300 p-4 text-sm text-neutral-500">
          No rulepacks loaded, or the API is unreachable.
        </div>
      )}

      <div className="space-y-4">
        {rules.map((r) => {
          const findings = findingsByRule[r.rule_id] ?? [];
          if (findings.length === 0) return null;
          return (
            <div key={r.rule_id} className="rounded border border-neutral-200 bg-white p-3 dark:border-neutral-800 dark:bg-neutral-900">
              <div className="mb-2 text-sm font-semibold">
                {r.reason_code} · {r.rule_id} <span className="font-normal text-neutral-500">— {r.description}</span>
              </div>
              <table className="w-full text-left text-xs">
                <thead className="text-neutral-500">
                  <tr>
                    <th className="pr-2">stratum A</th>
                    <th className="pr-2">rate A</th>
                    <th className="pr-2">stratum B</th>
                    <th className="pr-2">rate B</th>
                    <th className="pr-2">delta</th>
                    <th className="pr-2">bucket</th>
                    <th>n</th>
                  </tr>
                </thead>
                <tbody>
                  {findings.map((f, i) => (
                    <tr key={i} className={f.flagged ? "bg-red-50 text-red-800 dark:bg-red-950 dark:text-red-200" : ""}>
                      <td className="pr-2">{f.stratum_a}</td>
                      <td className="pr-2">{(f.firing_rate_a * 100).toFixed(0)}%</td>
                      <td className="pr-2">{f.stratum_b}</td>
                      <td className="pr-2">{(f.firing_rate_b * 100).toFixed(0)}%</td>
                      <td className="pr-2 font-semibold">{f.delta >= 0 ? "+" : ""}{(f.delta * 100).toFixed(0)}pp</td>
                      <td className="pr-2">{f.evidence_strength_bucket}</td>
                      <td>{f.n_a}/{f.n_b}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          );
        })}
      </div>
    </div>
  );
}
