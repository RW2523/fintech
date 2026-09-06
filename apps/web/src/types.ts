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
