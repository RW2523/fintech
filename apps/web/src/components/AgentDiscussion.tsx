import { useState } from "react";

import { Icon, type IconName } from "./icons";
import { Card, Chip, Empty, Glyph, type Tone } from "./primitives";
import type { Opinion } from "../types";
import { means, say } from "../vocabulary";

const STANCE_TONE: Record<string, Tone> = {
  SUPPORT: "pass",
  LEAN_SUPPORT: "pass",
  REVIEW: "warn",
  NEED_MORE_EVIDENCE: "warn",
  LEAN_OPPOSE: "fail",
  OPPOSE: "fail",
  BLOCK: "fail",
};

/** What each seat on the council is for, in one line and one icon.
 *
 *  An officer reading "cross_data_investigator disagrees" needs to know what
 *  that agent is entitled to have an opinion about before the disagreement
 *  means anything. */
const SEATS: Record<string, { label: string; icon: IconName; about: string }> = {
  document_evidence: {
    label: "Document Agent",
    icon: "documents",
    about: "Whether the paper is readable, complete and authentic.",
  },
  policy_affordability: {
    label: "Policy Agent",
    icon: "shield",
    about: "Whether the case meets the product and credit policy.",
  },
  credit_risk: {
    label: "Risk Agent",
    icon: "chart",
    about: "What the calibrated models say about repayment.",
  },
  fraud_integrity: {
    label: "Fraud Agent",
    icon: "alert",
    about: "Identity, duplication and the shape of the network around it.",
  },
  member_relationship: {
    label: "Member Relationship Agent",
    icon: "members",
    about: "The history the member has with the cooperative.",
  },
  challenger: {
    label: "Challenger",
    icon: "scale",
    about: "Argues the recommendation is wrong. Its job is to disagree.",
  },
  behaviour_trend: {
    label: "Behaviour Trend Agent",
    icon: "collections",
    about: "How this member's behaviour has moved against their own baseline.",
  },
  cross_data_investigator: {
    label: "Cross-Data Investigator",
    icon: "search",
    about: "Whether other records corroborate the signal.",
  },
  forecast_scenario: {
    label: "Forecast Agent",
    icon: "chart",
    about: "What is likely next, with the interval around it.",
  },
  intervention_planner: {
    label: "Intervention Planner",
    icon: "chat",
    about: "What could be offered, and what it would take.",
  },
};

function seat(agentId: string) {
  return (
    SEATS[agentId] ?? {
      label: agentId.replaceAll("_", " "),
      icon: "spark" as IconName,
      about: "",
    }
  );
}

/** docs/09 §3.6 — the council discussion.
 *
 *  One row per agent, always visible: its seat, what it concluded, and how it
 *  stood. It used to be collapsed behind a "show 6", on the reasoning that the
 *  decision is the record and the discussion is only how it was reached —
 *  which is true and was still wrong. The single question an officer has when
 *  a case reaches them is "what did they disagree about", and answering it
 *  required a click that gave no hint of what was behind it.
 *
 *  A row opens to its claims and the evidence under each. No raw model prose:
 *  a claim is a structured field with citations, and anything the model said
 *  outside that never reaches this screen.
 *
 *  The Challenger is first. Its job is to argue the recommendation is wrong,
 *  and burying that under five agreements would defeat the point of having
 *  it. */
export function AgentDiscussion({
  opinions,
  onEvidence,
}: {
  opinions: Opinion[];
  onEvidence: (evidenceId: string) => void;
}) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const ordered = [
    ...opinions.filter((o) => o.agent_id === "challenger"),
    ...opinions.filter((o) => o.agent_id !== "challenger"),
  ];

  const dissenting = ordered.filter((o) =>
    ["LEAN_OPPOSE", "OPPOSE", "BLOCK", "REVIEW", "NEED_MORE_EVIDENCE"].includes(o.stance),
  ).length;

  function toggle(id: string) {
    setExpanded((was) => {
      const next = new Set(was);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <Card
      title="Council discussion"
      icon="chat"
      tone="note"
      testId="agent-discussion"
      right={
        ordered.length > 0 ? (
          <span className="flex items-center gap-2">
            <Chip tone={dissenting ? "warn" : "pass"}>
              {dissenting === 0
                ? "all agreed"
                : `${dissenting} of ${ordered.length} not in support`}
            </Chip>
            <button
              type="button"
              data-testid="toggle-discussion"
              className="text-xs underline decoration-dotted hover:text-accent"
              onClick={() =>
                setExpanded((was) =>
                  was.size === ordered.length
                    ? new Set()
                    : new Set(ordered.map((o) => o.opinion_id)),
                )
              }
            >
              {expanded.size === ordered.length ? "collapse all" : `expand all ${ordered.length}`}
            </button>
          </span>
        ) : null
      }
    >
      {ordered.length === 0 ? (
        <Empty>
          No agent gave an opinion on this case. That is not a gap: a case
          settled by a hard gate or a routing rule never reaches the council,
          and the deciding step on the record says which.
        </Empty>
      ) : (
        <ul className="divide-line divide-y overflow-hidden rounded-xl border border-line">
          {ordered.map((opinion) => {
            const who = seat(opinion.agent_id);
            const open = expanded.has(opinion.opinion_id);
            const headline = opinion.claims?.[0]?.text ?? who.about;
            return (
              <li key={opinion.opinion_id} data-testid={`opinion-${opinion.agent_id}`}>
                <button
                  type="button"
                  onClick={() => toggle(opinion.opinion_id)}
                  aria-expanded={open}
                  className="flex w-full items-center gap-3 px-3 py-3 text-left transition hover:bg-raised"
                >
                  <Glyph
                    icon={who.icon}
                    tone={STANCE_TONE[opinion.stance] ?? "neutral"}
                    size="sm"
                  />
                  <span className="w-44 shrink-0 text-sm font-semibold">{who.label}</span>
                  <span className="min-w-0 flex-1 truncate text-xs text-muted">{headline}</span>
                  <Chip
                    tone={STANCE_TONE[opinion.stance] ?? "neutral"}
                    title={means("stance", opinion.stance)}
                  >
                    {say("stance", opinion.stance)}
                  </Chip>
                  <Icon.down
                    className={`h-4 w-4 shrink-0 text-faint transition-transform ${
                      open ? "rotate-180" : ""
                    }`}
                  />
                </button>

                {open ? (
                  <div className="rise border-t border-line bg-raised px-3 py-3">
                    <p className="text-xs text-muted">{who.about}</p>

                    <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-muted">
                      <span>
                        confidence{" "}
                        <span className="font-semibold tabular-nums text-ink">
                          {opinion.confidence.toFixed(2)}
                        </span>
                      </span>
                      <span className="font-mono">
                        {opinion.round} · {opinion.agent_version}
                      </span>
                    </div>

                    {Object.entries(opinion.factor_scores ?? {}).map(([family, score]) => (
                      <p key={family} className="mt-1.5 text-xs text-muted">
                        owns {family.toLowerCase()}:{" "}
                        <span className="font-semibold text-ink tabular-nums">{score.score}</span>{" "}
                        (<span className="font-mono">{score.calc_id}</span>)
                      </p>
                    ))}

                    {(opinion.claims ?? []).map((claim, index) => (
                      <div key={index} className="mt-3">
                        <p className="text-sm">{claim.text}</p>
                        <div className="mt-1 flex flex-wrap gap-1">
                          {(claim.evidence_refs ?? []).map((reference) => (
                            <button
                              key={reference}
                              type="button"
                              data-testid={`claim-evidence-${reference}`}
                              onClick={() => onEvidence(reference)}
                              className="rounded-md border border-line bg-surface px-1.5 py-0.5 font-mono text-[10px] text-accent transition hover:border-accent-line hover:bg-accent-soft"
                            >
                              {reference.slice(0, 18)}
                            </button>
                          ))}
                        </div>
                      </div>
                    ))}

                    {(opinion.unresolved ?? []).length > 0 ? (
                      <ul className="mt-3 flex flex-col gap-1">
                        {(opinion.unresolved ?? []).map((item, index) => (
                          <li key={index} className="flex items-start gap-1.5 text-xs text-warn-deep">
                            <Icon.alert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                            {item.question}
                            {item.blocking ? " (blocking)" : ""}
                          </li>
                        ))}
                      </ul>
                    ) : null}
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}
