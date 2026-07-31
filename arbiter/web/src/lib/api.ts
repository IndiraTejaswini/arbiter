import { authHeader, clearSession, getSession, setSession, type Session } from "./session";
import type {
  ArtifactListResponse, AtRiskResponse, AuditResponse, CaseListResponse,
  AdjudicationJob, CommitResponse, ConsistencyProof, DecisionResponse, DisputeCase,
  EnqueueResponse, QueueHealth,
  FairnessRule, FairnessRuleReport, GraphResponse, HealthResponse,
  InclusionProof, IntentNotResolved, ReadyResponse, RevealResponse,
  ReviewDecisionResponse, Role,
  RulepackCatalogue, RulepackResponse, SignedTreeHead, SweepReport,
  TimelineResponse as Timeline, UploadResponse,
} from "./types";

/**
 * The single HTTP surface of this application.
 *
 * Every endpoint the backend exposes has a typed method here, and no
 * component fetches anything on its own — so authorization, error shaping,
 * and 401 handling exist in exactly one place. Nothing in this file
 * fabricates a value: there are no defaults, no sample rows, and no
 * client-computed statistics. If it renders, the server sent it.
 */

export const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly path: string,
    readonly detail: string,
  ) {
    super(detail || `${status} ${path}`);
    this.name = "ApiError";
  }

  /** 401/403 mean "sign in" or "not your case" — rendered differently from
   *  a genuine failure, because they are not failures. */
  get isAuthError(): boolean {
    return this.status === 401 || this.status === 403;
  }

  get isNotFound(): boolean {
    return this.status === 404;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: {
        ...(init?.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
        ...authHeader(),
        ...(init?.headers ?? {}),
      },
      cache: "no-store",
    });
  } catch (cause) {
    // A network failure is not an HTTP status; surfacing it as one would
    // make "the API is down" indistinguishable from "the API said no".
    throw new ApiError(0, path, `Cannot reach the ARBITER API at ${API_BASE || window.location.origin}.`);
  }

  if (!response.ok) {
    if (response.status === 401) clearSession();
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = typeof body?.detail === "string" ? body.detail : JSON.stringify(body?.detail ?? body);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(response.status, path, detail);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function qs(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") search.set(key, String(value));
  }
  const rendered = search.toString();
  return rendered ? `?${rendered}` : "";
}

// -- Auth ------------------------------------------------------------------

export async function login(role: Role, boundId: string | null): Promise<Session> {
  const body = await request<{ token: string; expires_in: number }>("/v1/auth/dev-token", {
    method: "POST",
    body: JSON.stringify({ role, bound_id: boundId }),
  }).catch((error: ApiError) => {
    if (error.status === 404) {
      throw new ApiError(404, "/v1/auth/dev-token",
        "Development sign-in is disabled on this server (ARBITER_ENABLE_DEV_AUTH is off). " +
        "That is the correct setting outside development — obtain a token from the real IdP.");
    }
    throw error;
  });

  const session: Session = {
    token: body.token,
    role,
    boundId,
    actorId: `${role.toLowerCase()}-session`,
    expiresAt: Date.now() + body.expires_in * 1000,
  };
  setSession(session);
  return session;
}

/** Short-lived token for the SSE query string. `EventSource` cannot set an
 *  Authorization header, so the stream route accepts `?access_token=`; this
 *  mints a 60-second token rather than putting the session token in a URL
 *  that lands in access logs. */
async function streamToken(): Promise<string | null> {
  if (!getSession()) return null;
  try {
    const body = await request<{ access_token: string }>("/v1/auth/stream-token", { method: "POST" });
    return body.access_token;
  } catch {
    return null;
  }
}

// -- The API ---------------------------------------------------------------

export const api = {
  // Service
  health: () => request<HealthResponse>("/health"),
  ready: () => request<ReadyResponse>("/ready"),

  // Cases
  listCases: (params: { state?: string; reason_code?: string; limit?: number; offset?: number } = {}) =>
    request<CaseListResponse>(`/v1/cases${qs(params)}`),
  getCase: (caseId: string) => request<DisputeCase>(`/v1/cases/${caseId}`),
  createDispute: (
    body: { transaction_id: string; reason_code?: string; complaint_text?: string },
    idempotencyKey: string,
  ) =>
    request<DisputeCase | IntentNotResolved>("/v1/disputes", {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: JSON.stringify(body),
    }),
  // 202 + a job. Adjudication runs out of the request path now: it used to
  // hold a threadpool worker for ~10 seconds, saturating the service at
  // roughly 4 QPS with no backpressure.
  adjudicate: (caseId: string) =>
    request<EnqueueResponse>(`/v1/cases/${caseId}/adjudicate`, { method: "POST" }),
  getJob: (jobId: string) => request<AdjudicationJob>(`/v1/jobs/${jobId}`),
  getLatestJob: (caseId: string) =>
    request<{ case_id: string; job: AdjudicationJob | null }>(`/v1/cases/${caseId}/job`),
  queueHealth: () => request<QueueHealth>("/v1/admin/queue"),
  reviewDecision: (caseId: string, body: { outcome: string; reviewer_id: string; notes?: string }) =>
    request<ReviewDecisionResponse>(`/v1/cases/${caseId}/review-decision`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  // Decision & evidence
  getDecision: (caseId: string) => request<DecisionResponse>(`/v1/cases/${caseId}/decision`),
  getGraph: (caseId: string) => request<GraphResponse>(`/v1/cases/${caseId}/graph`),
  getTimeline: (caseId: string) => request<Timeline>(`/v1/cases/${caseId}/timeline`),

  // Artifacts (Reg E 1005.11(d): the documents relied on)
  listArtifacts: (caseId: string) => request<ArtifactListResponse>(`/v1/cases/${caseId}/artifacts`),
  artifactUrl: (artifactId: string) => `${API_BASE}/v1/artifacts/${artifactId}/content`,
  uploadEvidence: (caseId: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<UploadResponse>(`/v1/cases/${caseId}/evidence`, { method: "POST", body: form });
  },

  // Audit
  getAudit: (caseId: string) => request<AuditResponse>(`/v1/audit/${caseId}`),

  // Fairness (A7)
  listFairnessRules: () => request<FairnessRule[]>("/v1/fairness/rules"),
  getRuleFairness: (ruleId: string, params: { stratum_dimension?: string; limit?: number } = {}) =>
    request<FairnessRuleReport>(`/v1/fairness/rules/${ruleId}${qs(params)}`),

  // Rulepacks
  /** The catalogue — reason codes, titles, and descriptions. Any
   *  authenticated caller; carries no rule bodies. This is what drives every
   *  reason-code list in the console, so a rulepack added to the server
   *  appears in the UI without a frontend change. */
  listRulepacks: () => request<RulepackCatalogue>("/v1/rulepacks"),
  /** The full decision function. Reviewer/admin only. */
  getRulepack: (contentHash: string) => request<RulepackResponse>(`/v1/rulepacks/${contentHash}`),

  // Provenance / ADEC
  createCommitment: (body: {
    merchant_id?: string; artifact_type: string; commitment_hash: string; event_time: string;
  }) => request<CommitResponse>("/v1/commitments", { method: "POST", body: JSON.stringify(body) }),
  revealCommitment: (commitmentId: string, body: {
    artifact_hex: string; salt_hex: string; dispute_filed_at?: string | null;
  }) => request<RevealResponse>(`/v1/commitments/${commitmentId}/reveal`, {
    method: "POST", body: JSON.stringify(body),
  }),
  getInclusionProof: (commitmentId: string) =>
    request<InclusionProof>(`/v1/commitments/${commitmentId}/proof`),
  getLatestSth: () => request<SignedTreeHead>("/v1/log/sth"),
  getConsistencyProof: (fromSize: number, toSize?: number) =>
    request<ConsistencyProof>(`/v1/log/consistency${qs({ from_size: fromSize, to_size: toSize })}`),

  // Operations
  sweepDeadlines: () => request<SweepReport>("/v1/admin/sweep-deadlines", { method: "POST" }),
  casesAtRisk: (days = 7) => request<AtRiskResponse>(`/v1/cases-at-risk${qs({ days })}`),
  eraseSubject: (subjectId: string) =>
    request<{ subject_id: string; erased: boolean; cases_annotated: number }>(
      `/v1/subjects/${subjectId}/erase`, { method: "POST" },
    ),

  // Realtime
  async streamUrl(caseId: string): Promise<string | null> {
    const token = await streamToken();
    if (!token) return null;
    return `${API_BASE}/v1/cases/${caseId}/stream?access_token=${encodeURIComponent(token)}`;
  },
};

export type { Session };
export { ApiError as default };
