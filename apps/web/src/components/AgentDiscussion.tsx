import { useState } from "react";

import { Card, Chip, Empty, type Tone } from "./primitives";
import type { Opinion } from "../types";

const STANCE_TONE: Record<string, Tone> = {
  SUPPORT: "pass",
  LEAN_SUPPORT: "pass",
  REVIEW: "warn",
  NEED_MORE_EVIDENCE: "warn",
  LEAN_OPPOSE: "fail",
  OPPOSE: "fail",
  BLOCK: "fail",
};

/** docs/09 §3.6 — the agent discussion.
 *
 *  Collapsed by default, because the decision is the record and the discussion
 *  is how it was reached. Opened, it shows each agent's stance, the factor it
 *  owns, and its claims with the evidence behind them. No raw model prose: a
 *  claim is a structured field with citations, and anything the model said
 *  outside that never reaches this screen.
 *
 *  The Challenger is first. Its job is to argue the recommendation is wrong,
 *  and burying that under five agreements would defeat the point of having it. */
export function AgentDiscussion({
  opinions,
  onEvidence,
}: {
  opinions: Opinion[];
  onEvidence: (evidenceId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const ordered = [
    ...opinions.filter((o) => o.agent_id === "challenger"),
    ...opinions.filter((o) => o.agent_id !== "challenger"),
  ];

  return (
    <Card
      title="Agent discussion"
      testId="agent-discussion"
      right={
        <button
          type="button"
          data-testid="toggle-discussion"
          className="text-xs underline decoration-dotted"
          onClick={() => setOpen((value) => !value)}
        >
          {open ? "collapse" : `show ${ordered.length}`}
        </button>
      }
    >
      {!open ? (
        <p className="text-sm text-[--color-muted]">
          {ordered.length === 0
            ? "No agent gave an opinion on this case."
            : `${ordered.length} agents took part. The decision above is the record; this is how it was reached.`}
        </p>
      ) : ordered.length === 0 ? (
        <Empty>No agent gave an opinion on this case.</Empty>
      ) : (
        <ul className="flex flex-col gap-3">
          {ordered.map((opinion) => (
            <li
              key={opinion.opinion_id}
              data-testid={`opinion-${opinion.agent_id}`}
              className="rounded border border-[--color-line] p-3"
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium">
                  {opinion.agent_id.replaceAll("_", " ")}
                </span>
                <Chip tone={STANCE_TONE[opinion.stance] ?? "neutral"}>
                  {opinion.stance.replaceAll("_", " ").toLowerCase()}
                </Chip>
                <span className="text-xs text-[--color-muted]">
                  confidence {opinion.confidence.toFixed(2)}
                </span>
                <span className="ml-auto font-mono text-xs text-[--color-muted]">
                  {opinion.round} · {opinion.agent_version}
                </span>
              </div>

              {Object.entries(opinion.factor_scores ?? {}).map(([family, score]) => (
                <p key={family} className="mt-1 text-xs text-[--color-muted]">
                  owns {family.toLowerCase()}: {score.score} (
                  <span className="font-mono">{score.calc_id}</span>)
                </p>
              ))}

              {(opinion.claims ?? []).slice(0, 3).map((claim, index) => (
                <div key={index} className="mt-2 text-sm">
                  <p>{claim.text}</p>
                  <div className="mt-1 flex flex-wrap gap-1">
                    {(claim.evidence_refs ?? []).map((reference) => (
                      <button
                        key={reference}
                        type="button"
                        data-testid={`claim-evidence-${reference}`}
                        onClick={() => onEvidence(reference)}
                        className="rounded border border-[--color-line] px-1.5 py-0.5 font-mono text-[10px] text-[--color-accent] hover:border-[--color-accent]"
                      >
                        {reference.slice(0, 16)}
                      </button>
                    ))}
                  </div>
                </div>
              ))}

              {(opinion.unresolved ?? []).length > 0 ? (
                <ul className="mt-2 flex flex-col gap-1">
                  {(opinion.unresolved ?? []).map((item, index) => (
                    <li key={index} className="text-xs text-[--color-warn]">
                      unresolved: {item.question}
                      {item.blocking ? " (blocking)" : ""}
                    </li>
                  ))}
                </ul>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
