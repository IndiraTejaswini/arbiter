/**
 * Wire types, mirroring the FastAPI response shapes exactly.
 *
 * Every field here is produced by the backend. There are no client-side
 * defaults, no placeholder values, and no derived "example" data anywhere
 * in this application — if a number is on screen, an authenticated API
 * call returned it.
 */

export type Role = "CARD_MEMBER" | "MERCHANT" | "REVIEWER" | "ADMIN";

export type ProvenanceTier = "COMMITTED" | "NETWORK" | "SUBMITTED" | "ASSERTED";

export type Outcome =
  | "CARD_MEMBER_PREVAILS"
  | "MERCHANT_PREVAILS"
  | "SPLIT"
  | "INSUFFICIENT_EVIDENCE"
  // The network chargeback right did not exist for this dispute: the filing
  // window had closed, or the reason code's "Excluded Transactions" list
  // removes it. NOT a merchant win — no evidence was weighed. Its absence
  // from this union was a live rendering bug: `OUTCOME_LABEL[outcome]` and
  // `OUTCOME_STYLE[outcome]` both resolved to `undefined`, so every case the
  // gate closed rendered an unlabelled, unstyled badge.
  | "CHARGEBACK_INELIGIBLE";

export type CaseState =
  | "INTAKE" | "GATHERING" | "AWAITING_EVIDENCE" | "ANALYSING"
  | "ADJUDICATED" | "ESCALATED" | "SETTLED" | "DEFLECTED" | "REOPENED";

export type Severity = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";

// -- Cases -----------------------------------------------------------------

export interface DisputeCase {
  case_id: string;
  transaction_id: string;
  card_member_id: string;
  merchant_id: string;
  reason_code: string;
  intent_confidence: number | null;
  state: CaseState;
  amount_minor: number;
  currency: string;
  filed_at: string;
  reg_regime: "REG_Z" | "REG_E";
  ack_deadline: string;
  acknowledged_at: string | null;
  resolve_deadline: string;
  merchant_response_deadline: string | null;
  merchant_window_expired_at: string | null;
  provisional_credit_deadline: string | null;
  provisional_credit_issued_at: string | null;
  merchant_responded: boolean;
}

export interface CaseListResponse {
  total: number;
  limit: number;
  offset: number;
  cases: DisputeCase[];
}

export interface IntentNotResolved {
  resolved: false;
  proposed_reason_code: string | null;
  confidence: number | null;
  signals: string[];
  needs_user_confirmation: boolean;
  route_to_human_triage: boolean;
  reason: string;
}

// -- Proof tree ------------------------------------------------------------

export interface LiteralWitness {
  predicate: string;
  negated: boolean;
  satisfied: boolean;
  evidence_node_ids: string[];
  confidence: number;
  child?: ProofNode | null;
}

export interface ProofNode {
  rule_id: string;
  head: string;
  holds: boolean;
  description?: string;
  legal_basis?: string;
  literals: LiteralWitness[];
}

// -- Counterfactuals -------------------------------------------------------

export interface CounterfactualItem {
  predicate: string;
  negated: boolean;
  action: "OBTAIN" | "RETRACT";
  currently: string;
  obtainable: boolean;
}

export interface Counterfactual {
  outcome: string;
  head: string;
  already_satisfied: boolean;
  delta: CounterfactualItem[];
}

// -- Decision --------------------------------------------------------------

/** Per-layer status of the MANDATORY four-layer contradiction pipeline.
 *  An empty findings list is ambiguous between "clean" and "a mandatory
 *  check could not run" -- this is what disambiguates it. */
export interface ContradictionAnalysis {
  complete: boolean;
  must_escalate: boolean;
  layer_status: Record<string, "OK" | "UNAVAILABLE" | "NOT_APPLICABLE">;
  unavailable_layers: string[];
  unavailable_reason: string | null;
  semantic_pairs_evaluated: number;
}

// -- Narration -------------------------------------------------------------

/** One narration sentence's citation into the evidence graph. Grounding is
 *  all-or-nothing: a single citation that does not resolve to a real node
 *  discards the ENTIRE narration and the deterministic template renders
 *  instead. */
export interface NarrationCitation {
  sentence_idx: number;
  node_id: string;
}

export interface Narration {
  text: string;
  /** Which renderer produced this.
   *  `template`           — the deterministic proof-tree renderer (the default path)
   *  `llm_exception_path` — a generated narration that PASSED citation grounding
   *  `template_fallback`  — a generated narration was produced and then DISCARDED
   *                         for citing a node that does not exist; this is the
   *                         backstop text. Worth showing: a reader is entitled to
   *                         know the veto fired.
   *  `eligibility_gate`   — the case ended at the chargeback-right gate, so there
   *                         is no proof tree to narrate and nothing to cite. */
  source: "template" | "llm_exception_path" | "template_fallback" | "eligibility_gate" | string;
  /** The backend's own sentence split — `NarrationCitation.sentence_idx`
   *  indexes THIS array. Never re-derive it from `text`: these sentences
   *  contain legal citations ("12 CFR 1005.11"), so punctuation-based
   *  splitting breaks mid-reference and misaligns every citation after it.
   *  Absent on decisions persisted before it joined the payload. */
  sentences?: string[];
  citations: NarrationCitation[];
}

// -- Chargeback-right gate (arbiter.eligibility) ---------------------------

export interface ExclusionFinding {
  exclusion_id: string;
  fired: boolean;
  description: string;
  legal_basis: string;
  satisfied_conditions: string[];
  undetermined_conditions: string[];
}

export interface FilingWindowBranchFinding {
  branch_id: string;
  anchor_attribute: string | null;
  anchor_at: string | null;
  deadline: string | null;
  /** `null` means the branch could not be evaluated — the ledger did not
   *  supply its anchor date. Distinct from `false`, which means the window
   *  demonstrably closed. */
  timely: boolean | null;
  capped_out: boolean;
}

export interface FilingWindowFinding {
  timely: boolean | null;
  branches: FilingWindowBranchFinding[];
}

/** Recorded on EVERY decision, not only the ineligible ones — "the gate ran,
 *  the window was open, no exclusion applied" is a positive claim, and its
 *  absence has to be distinguishable from a case decided before the gate
 *  existed. */
export interface EligibilityRecord {
  available: boolean;
  network_code: string;
  reason: string;
  filing_window: FilingWindowFinding;
  exclusions_fired: ExclusionFinding[];
  exclusions_evaluated: number;
  /** Attributes the ledger did not supply, which an exclusion turned on.
   *  Unknown fails OPEN here and only here: an exclusion firing removes the
   *  card member's dispute right outright, so the conservative direction
   *  reverses. These are the coverage gaps worth closing. */
  undetermined_attributes: string[];
}

// -- Decision --------------------------------------------------------------

export interface DecisionResponse {
  case_id: string;
  decision_id: string;
  outcome: Outcome;
  abstained: boolean;
  confidence: number;
  conformal_set: string[];
  rulepack_hash: string;
  merchant_silent: boolean;
  provisional_credit_due: boolean;
  llm_rejections: number;
  selected_for_audit: boolean;
  review_selection_probability: number | null;
  contradiction_analysis: ContradictionAnalysis;
  predicates: Record<string, "TRUE" | "FALSE" | "UNKNOWN">;
  proof_tree: ProofNode | null;
  counterfactuals: Record<string, Counterfactual>;
  escalation_reason: string | null;
  /** `{}` on decisions written before narration was persisted. */
  narration: Narration | Record<string, never>;
  /** `{}` on decisions written before the chargeback-right gate existed. */
  eligibility: EligibilityRecord | Record<string, never>;
  decided_at: string;
}

export interface ReviewDecisionResponse {
  status: string;
  case_id: string;
  overrode_system: boolean;
  /** False for a case that ended at the chargeback-right gate: the conformal
   *  gate never scored it, so it has no nonconformity score to contribute. */
  calibration_sample_recorded: boolean;
  reason: string;
}

// -- Evidence --------------------------------------------------------------

export interface SourceRef {
  artifact_id: string;
  page: number | null;
  bbox: number[] | null;
  char_span: number[] | null;
}

export interface EvidenceNodeOut {
  node_id: string;
  node_type: string;
  attrs: Record<string, unknown>;
  provenance: ProvenanceTier;
  extract_conf: number | null;
  source_ref?: SourceRef | null;
}

export interface GraphResponse {
  nodes: EvidenceNodeOut[];
  edges: { src: string; dst: string; rel: string }[];
}

export interface ContradictionOut {
  contradiction_id: string;
  kind: string;
  severity: Severity;
  node_ids: string[];
  detail: { description?: string; layer?: string };
}

export interface CaseEventOut {
  seq: number;
  event_type: string;
  payload: Record<string, unknown>;
  actor_type: string;
  occurred_at: string;
}

export interface TimelineResponse {
  nodes: EvidenceNodeOut[];
  edges: { edge_id: string; src: string; dst: string; rel: string }[];
  contradictions: ContradictionOut[];
  events: CaseEventOut[];
}

// -- Artifacts -------------------------------------------------------------

export interface ArtifactOut {
  artifact_id: string;
  mime_type: string;
  byte_size: number;
  sha256: string;
  uploaded_by: string;
  uploaded_at: string;
  scan_status: string;
  forensics: {
    incremental_update_after_filed?: boolean;
    moddate_after_filed_at?: boolean;
    producer_suspicious?: boolean;
    perceptual_hash?: string | null;
    findings?: string[];
    confidence_penalty?: number;
  };
}

export interface ArtifactListResponse {
  case_id: string;
  artifacts: ArtifactOut[];
}

export interface UploadResponse {
  artifact_id: string;
  /** The SCAN accepted it and the bytes were retained. Not the same as
   *  "evidence came out of it" — see `evidence_extracted`. */
  accepted: boolean;
  /** Whether extraction produced an evidence node. A structurally valid
   *  document with no readable fields is `accepted: true,
   *  evidence_extracted: false` — stored and retained, but contributing
   *  nothing to the graph. Conflating that with rejection told the user
   *  their document had been refused when it had in fact been kept. */
  evidence_extracted?: boolean;
  node_id?: string | null;
  report: Record<string, unknown>;
}

// -- Audit -----------------------------------------------------------------

export interface AuditEvent {
  seq: number;
  event_type: string;
  actor_id: string;
  actor_type: string;
  occurred_at: string;
  event_hash: string;
  rulepack_hash: string | null;
  key_epoch: number;
  link_valid: boolean;
  payload_hash_valid: boolean;
  signature_valid: boolean;
  payload: Record<string, unknown>;
}

export interface AuditResponse {
  case_id: string;
  chain_valid: boolean;
  broken_at_seq: number | null;
  payloads_valid: boolean;
  signatures_valid: boolean;
  verified: boolean;
  events: AuditEvent[];
}

// -- Fairness --------------------------------------------------------------

export interface FairnessRule {
  reason_code: string;
  rule_id: string;
  head: string;
  description: string;
}

export interface ProportionCI {
  rate: number;
  ci_low: number;
  ci_high: number;
  n: number;
}

export interface FairnessFinding {
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
  ci_a: ProportionCI | null;
  ci_b: ProportionCI | null;
  p_value: number;
  q_value: number;
  power: { min_detectable_effect: number; adequately_powered: boolean } | null;
}

export interface FairnessRuleReport {
  rule_id: string;
  n_cases: number;
  findings: FairnessFinding[];
  flagged: FairnessFinding[];
}

// -- Rulepacks -------------------------------------------------------------

export interface RulepackRule {
  rule_id: string;
  head: string;
  body: string[];
  description: string;
  legal_basis: string;
}

/** One "Maximum time a dispute can be raised" clause from the Amex guide.
 *  `from` holds more than one attribute only for the guide's "whichever
 *  occurred first" construction. */
export interface RulepackFilingWindow {
  branch_id: string;
  days: number;
  from: string[];
  absolute_cap_days: number | null;
  cap_from: string | null;
  description: string;
}

/** One "Excluded Transactions" bullet. Conditions are ANDed within a bullet;
 *  bullets are ORed — each is an independent way the chargeback right
 *  disappears. */
export interface RulepackExclusion {
  id: string;
  description: string;
  legal_basis: string;
  when: string[];
}

/** The pre-referee gate, as data. Disclosed to the same audience and under
 *  the same reasoning as the rules themselves: an exclusion can end a
 *  dispute outright, so an auditor shown every rule body but not the
 *  conditions under which no rule runs at all has been shown the less
 *  consequential half of the decision function. */
export interface RulepackChargebackRight {
  source: string;
  merchant_challenge_days: number;
  filing_window: RulepackFilingWindow[];
  exclusions: RulepackExclusion[];
}

export interface RulepackResponse {
  rulepack_id: string;
  reason_code: string;
  /** The four-digit code the Amex chargeback guide and a merchant's own
   *  "Resolve Disputes" screen use for this same dispute (F29→4540,
   *  C08→4554, C02→4513). */
  network_code: string | null;
  version: string;
  content_hash: string;
  decision_predicates: Record<string, string>;
  predicate_schema: string[];
  rules: RulepackRule[];
  chargeback_right: RulepackChargebackRight | null;
}

// -- Provenance / ADEC -----------------------------------------------------

export interface SignedTreeHead {
  tree_size: number;
  root_hash: string;
  timestamp_unix_ns: number;
  log_id: string;
  signature: string;
  tsa_token: {
    message_imprint: string;
    gen_time_unix_ns: number;
    tsa_key_id: string;
    signature: string;
  };
}

export interface InclusionProof {
  leaf_index: number;
  tree_size: number;
  audit_path: string[];
  sth: SignedTreeHead;
}

export interface CommitResponse {
  commitment_id: string;
  merchant_id: string;
  leaf_index: number;
  committed_at: string;
}

export interface RevealResponse {
  ok: boolean;
  inclusion_valid: boolean;
  sth_signature_valid: boolean;
  tsa_signature_valid: boolean;
  predates_deadline: boolean | null;
  committed_at_unix_ns: number;
  tier: ProvenanceTier;
}

export interface ConsistencyProof {
  size1: number;
  size2: number;
  proof: string[];
}

// -- Operations ------------------------------------------------------------

export interface SweepReport {
  acknowledged: string[];
  ack_breached: string[];
  merchant_windows_expired: string[];
  provisional_credit_due: string[];
  resolve_breached: string[];
  total_actions: number;
}

export interface CaseAtRisk {
  case_id: string;
  reason_code: string;
  reg_regime: string;
  state: CaseState;
  resolve_deadline: string;
  days_remaining: number;
}

export interface AtRiskResponse {
  within_days: number;
  cases: CaseAtRisk[];
}

// -- Adjudication jobs -----------------------------------------------------

export type JobState = "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED";

export interface AdjudicationJob {
  job_id: string;
  case_id: string;
  state: JobState;
  terminal: boolean;
  attempts: number;
  max_attempts: number;
  requested_by: string;
  error: string | null;
  decision_id: string | null;
  enqueued_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface EnqueueResponse {
  case_id: string;
  job: AdjudicationJob;
  newly_queued: boolean;
  stream_url: string;
}

export interface QueueHealth {
  depth: {
    queued: number;
    running: number;
    succeeded: number;
    failed: number;
    oldest_queued_at: string | null;
    oldest_queued_age_seconds: number | null;
  };
  recent_failures: AdjudicationJob[];
}

export interface HealthResponse {
  status: string;
  reason_codes: string[];
  /** Request constraints the client must honour. Served rather than
   *  hardcoded: `max_artifact_bytes` is configurable, and a console that
   *  guesses it states the wrong number the moment an operator tunes it. */
  limits: {
    max_artifact_bytes: number;
    max_artifacts_per_case: number;
  };
}

export interface ReadyResponse {
  status: "ready" | "degraded";
  rulepacks_loaded: number;
  calibration: Record<string, { n: number; effective_n: number; calibrated: boolean }>;
  /** The threshold `calibrated` is measured against. Configurable, and the
   *  console used to hardcode it as a statement of fact. */
  min_calibration_n: number;
  conformal_alpha: number;
  note: string | null;
}

// -- Rulepack catalogue ----------------------------------------------------

/**
 * One loaded reason code, as the catalogue describes it.
 *
 * Deliberately carries NO rule bodies and no exclusion conditions — those
 * are the decision function and stay behind the reviewer-only
 * `GET /v1/rulepacks/{hash}`. This answers only "which disputes can I file,
 * and what do they mean", which every authenticated caller may ask.
 */
export interface RulepackSummary {
  reason_code: string;
  network_code: string | null;
  /** Falls back to the reason code server-side, so this is never empty. */
  title: string;
  description: string;
  version: string;
  content_hash: string;
  rule_count: number;
  predicate_count: number;
  exclusion_count: number;
  has_chargeback_right_gate: boolean;
}

export interface RulepackCatalogue {
  rulepacks: RulepackSummary[];
}

export interface StageEvent {
  case_id: string;
  stage: string;
  message: string;
  progress: number;
  timestamp: string;
}
