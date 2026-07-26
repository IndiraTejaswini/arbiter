const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export type ProofNode = {
  rule_id: string | null;
  head: string;
  holds: boolean;
  description?: string;
  literals: LiteralWitness[];
};

export type LiteralWitness = {
  predicate: string;
  negated: boolean;
  satisfied: boolean;
  evidence_node_ids: string[];
  confidence: number;
  child?: ProofNode | null;
};

export type CounterfactualItem = {
  predicate: string;
  negated: boolean;
  action: "OBTAIN" | "RETRACT";
  currently: string;
  obtainable: boolean;
};

export type Counterfactual = {
  outcome: string;
  head: string;
  already_satisfied: boolean;
  delta: CounterfactualItem[];
};

export type DecisionResponse = {
  case_id: string;
  outcome: string;
  abstained: boolean;
  confidence: number;
  conformal_set: string[];
  rulepack_hash: string;
  merchant_silent: boolean;
  llm_rejections: number;
  proof_tree: ProofNode | null;
  counterfactuals: Record<string, Counterfactual>;
  escalation_reason: string | null;
  decided_at: string;
};

export type DisputeCase = {
  case_id: string;
  transaction_id: string;
  card_member_id: string;
  merchant_id: string;
  reason_code: string;
  state: string;
  amount_minor: number;
  currency: string;
  filed_at: string;
  ack_deadline: string;
  resolve_deadline: string;
  merchant_responded: boolean;
};

export type EvidenceNodeOut = {
  node_id: string;
  node_type: string;
  attrs: Record<string, unknown>;
  provenance: "COMMITTED" | "NETWORK" | "SUBMITTED" | "ASSERTED";
  extract_conf: number | null;
  source_ref?: { artifact_id: string; page: number | null; bbox: number[] | null } | null;
};

export type GraphResponse = {
  nodes: EvidenceNodeOut[];
  edges: { src: string; dst: string; rel: string }[];
};

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`${res.status} ${path}: ${text}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  getCase: (caseId: string) => apiFetch<DisputeCase>(`/v1/cases/${caseId}`),
  getDecision: (caseId: string) => apiFetch<DecisionResponse>(`/v1/cases/${caseId}/decision`),
  getGraph: (caseId: string) => apiFetch<GraphResponse>(`/v1/cases/${caseId}/graph`),
  getTimeline: (caseId: string) => apiFetch<{ events: unknown[] }>(`/v1/cases/${caseId}/timeline`),
  adjudicate: (caseId: string) =>
    apiFetch<DisputeCase>(`/v1/cases/${caseId}/adjudicate`, { method: "POST" }),
  streamUrl: (caseId: string) => `${API_BASE}/v1/cases/${caseId}/stream`,
  getRuleFairness: (ruleId: string) => apiFetch<unknown>(`/v1/fairness/rules/${ruleId}`),
};

export { API_BASE };
