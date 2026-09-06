/** The shapes the workbench reads. Deliberately partial: the UI shows what a
 *  record carries and says "not recorded" for what it does not, rather than
 *  insisting a record be complete before it can be displayed. A half-finished
 *  case is exactly when someone needs to look at it. */

export type QueueEntry = {
  decision_record_id: string;
  case_id: string | null;
  snapshot_id: string | null;
  member_id: string | null;
  tier: string | null;
  route: string | null;
  route_reasons: string[];
  recommendation: string | null;
  confidence: number | null;
  disagreement: number | null;
  weighted_score: number | null;
  required_authority: string | null;
  challenger_open: boolean | null;
  created_at: string | null;
  decided: boolean;
};

export type Queue = { count: number; routes: string[]; decisions: QueueEntry[] };

export type FactorScore = {
  score: number;
  weight: number;
  weighted: number;
  decisive: boolean;
  calc_id: string;
  evidence_refs?: string[];
  level?: string | null;
};

export type HardGate = {
  rule_id: string;
  result: string;
  reason_code?: string | null;
  clause_id?: string | null;
  evidence_refs?: string[];
};

export type Claim = { text: string; evidence_refs?: string[] };

export type Opinion = {
  opinion_id: string;
  agent_id: string;
  agent_version: string;
  round: string;
  stance: string;
  confidence: number;
  claims?: Claim[];
  contradictions?: unknown[];
  unresolved?: { question: string; blocking?: boolean }[];
  factor_scores?: Record<string, FactorScore>;
  changed_from_prior?: unknown;
};

export type DecisionRecord = {
  decision_record_id: string;
  snapshot_id: string;
  committee_run_id?: string | null;
  case_type?: string;
  tier?: string;
  hard_gates?: HardGate[];
  evidence_coverage?: number;
  factor_scores?: Record<string, FactorScore>;
  weighted_score?: number | null;
  recommendation?: string;
  confidence?: number;
  disagreement?: number;
  challenger_open?: boolean;
  route?: string;
  route_reasons?: string[];
  required_authority?: string;
  /** One passage per audience, each carrying its own status: a narrative
   *  written from the record without the language model is still shown, but
   *  the reader is told which one they are looking at (docs/06 §7). */
  narrative?: Partial<Record<NarrativeAudience, { text: string; status: string }>>;
  would_change_outcome?: { condition: string; new_recommendation: string }[];
  proposed_actions?: unknown[];
  opinions?: string[];
  policy_version?: string;
  dff_version?: string;
  autonomy_version?: string;
  model_versions?: Record<string, string>;
  budgets?: Record<string, unknown>;
  created_at?: string;
};

/** What the decision service returns for one record: the record itself, plus
 *  the two things the ledger knows about it that are not fields of it. */
export type HeldRecord = {
  record: DecisionRecord;
  case_id: string | null;
  superseded_by: string | null;
};

export const NARRATIVE_AUDIENCES = ["officer", "member", "auditor"] as const;

export type NarrativeAudience = (typeof NARRATIVE_AUDIENCES)[number];

export type ExplanationLevels = {
  policy: {
    rule_id: string;
    result: string;
    reason_code: string | null;
    clause_id: string | null;
    evidence_refs: string[];
    blocking: boolean;
  }[];
  factors: ({ family: string } & Partial<FactorScore>)[];
  drivers: {
    feature: string;
    direction: string;
    contribution: number;
    share: number;
    reason_code: string | null;
    model_run_id: string;
  }[];
  provenance: {
    evidence_id?: string;
    document_id?: string;
    finding_id?: string;
    type?: string;
    source_system?: string;
    source_record_id?: string;
    locator?: Record<string, unknown>;
    captured_at?: string;
    code?: string;
    severity?: string;
    advisory?: boolean;
    status?: string;
  }[];
  human: Record<string, unknown> | null;
};

export type Explanation = {
  decision_record_id: string;
  levels: ExplanationLevels;
  counterfactuals: { condition: string; new_recommendation: string }[];
  unavailable: string[];
  sources: Record<string, string>;
};


/** docs/09 §6 — a case reconstructed end to end. */
export type TimelineEvent = {
  source: "ledger" | "committee" | "audit";
  kind: string;
  at: string | null;
  seq?: number;
  entry_id?: string;
  hash?: string;
  prev_hash?: string;
  agent_id?: string;
  stance?: string;
  payload?: unknown;
};

export type ChainVerification = {
  verified: boolean | null;
  entries_checked?: number;
  breaks?: { seq: number; entry_id: string; reason: string; expected: string; found: string }[];
};

export type Reconstruction = {
  case_id: string;
  timeline: TimelineEvent[];
  documents: { document_id: string; type: string; status?: string }[];
  findings: { code?: string; severity?: string; detail?: unknown }[];
  actions: { action_id: string; state: string; type?: string; core_refs?: unknown[] }[];
  chain: ChainVerification;
  unavailable: string[];
};


/** docs/09 §4 — the collections workbench. */
export type WatchAlert = {
  alert_id: string;
  member_id: string;
  account_id: string | null;
  state: "WATCH" | "ELEVATED" | "CRITICAL" | "RECOVERY" | string;
  signals: string[];
  rank_value: number;
  why_now: string;
  p90: number | null;
  exposure: number;
  change_point: string | null;
  corroboration: { confirms?: string[]; explains?: string[]; silent?: string[] };
  opened_at: string | null;
};

export type MemberWatch = {
  member_id: string;
  state: string;
  since: string | null;
  rule?: string;
  reason?: string;
  evaluated: boolean;
  transitions: {
    at: string;
    from_state: string;
    to_state: string;
    rule: string;
    reason: string;
  }[];
};

export type HorizonScore = {
  horizon_days: number;
  probability: number;
  interval: { lower: number; upper: number; nominal: number; band: string; band_n: number };
  drivers: { feature: string; value: number; direction: string; contribution: number; kind: string }[];
  calibration_warning?: string;
};

export type MemberScore = {
  member_id: string;
  model_version: string;
  stale_days: number;
  features_missing: string[];
  scores: HorizonScore[];
};

export type InboxMessage = {
  message_id: string;
  template_id: string;
  language: string;
  channel: string;
  state: string;
  subject: string | null;
  body: string;
  sent_at: string | null;
  scheduled_at: string | null;
  cancelled_at: string | null;
  cancel_reason: string | null;
};


/** docs/09 §3.5 — a grounded answer about the case in front of an officer. */
export type CopilotAnswer = {
  answer: string;
  citations: { ref: string; what: string; kind?: string }[];
  refusal?: { reason: string; code?: string };
  actions?: { label: string; kind?: string; ref?: string }[];
  grounded: boolean;
  attempts: number;
  tools_read: string[];
  tools_unavailable: string[];
  agent_version: string;
};
