import { Card, Chip, Empty, Figure, Meter, type Tone } from "./primitives";
import type { DecisionRecord, FactorScore } from "../types";

const RECOMMENDATION_TONE: Record<string, Tone> = {
  APPROVE: "pass",
  APPROVE_WITH_CONDITIONS: "accent",
  REVIEW: "warn",
  DECLINE: "fail",
  COMPLIANCE_REVIEW: "fail",
  MORE_INFORMATION_REQUIRED: "warn",
};

/** docs/09 §3.3 — the decision card.
 *
 *  Everything here comes from the DecisionRecord and is shown as the record
 *  states it. Where a figure is absent it says so: a blank reads as zero, and
 *  a weighted score of zero means something very different from a case that
 *  was never scored because a gate stopped it. */
export function DecisionCard({ record }: { record: DecisionRecord }) {
  const factors = Object.entries(record.factor_scores ?? {});
  const decisive = factors.find(([, score]) => score.decisive)?.[0];
  const gates = record.hard_gates ?? [];
  const failing = gates.filter((gate) => ["FAIL", "BLOCK"].includes(gate.result));

  return (
    <Card
      title="Decision"
      testId="decision-card"
      right={
        <Chip
          testId="recommendation-chip"
          tone={RECOMMENDATION_TONE[record.recommendation ?? ""] ?? "neutral"}
        >
          {(record.recommendation ?? "no recommendation").replaceAll("_", " ").toLowerCase()}
        </Chip>
      }
    >
      <div className="flex flex-col gap-4">
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <Figure
            label="Weighted score"
            testId="weighted-score"
            value={
              record.weighted_score == null
                ? "not scored"
                : record.weighted_score.toFixed(1)
            }
            hint={
              record.weighted_score == null
                ? "a gate stopped the case before it was weighed"
                : undefined
            }
          />
          <Figure label="Tier" testId="tier" value={record.tier ?? "unknown"} />
          <Figure
            label="Required authority"
            testId="required-authority"
            value={record.required_authority ?? "not set"}
          />
          <Figure
            label="Evidence coverage"
            testId="evidence-coverage"
            value={
              record.evidence_coverage == null
                ? "not recorded"
                : `${(record.evidence_coverage * 100).toFixed(0)}%`
            }
          />
        </div>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Meter
            label="Confidence"
            testId="confidence"
            value={record.confidence}
            tone="accent"
          />
          <Meter
            label="Disagreement between agents"
            testId="disagreement"
            value={record.disagreement}
            tone="warn"
          />
        </div>

        {/* Factor bars. The decisive one is marked because "what decided it"
            is the first question an officer asks, and the framework computes
            it rather than leaving it to be inferred from the longest bar. */}
        <div className="flex flex-col gap-2" data-testid="factor-bars">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-[--color-muted]">
            Decision factors
          </h3>
          {factors.length === 0 ? (
            <Empty>No factor was scored. The case did not reach the weighting.</Empty>
          ) : (
            factors.map(([family, score]) => (
              <FactorBar
                key={family}
                family={family}
                score={score}
                decisive={family === decisive}
              />
            ))
          )}
        </div>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div className="flex flex-col gap-2">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-[--color-muted]">
              Hard gates
            </h3>
            {gates.length === 0 ? (
              <Empty>No gate was evaluated.</Empty>
            ) : (
              <ul className="flex flex-col gap-1" data-testid="hard-gates">
                {gates.map((gate) => (
                  <li key={gate.rule_id} className="flex items-center gap-2 text-sm">
                    <Chip tone={["FAIL", "BLOCK"].includes(gate.result) ? "fail" : "pass"}>
                      {gate.result.toLowerCase()}
                    </Chip>
                    <span className="font-mono text-xs">{gate.rule_id}</span>
                    {gate.clause_id ? (
                      <span className="text-xs text-[--color-muted]">{gate.clause_id}</span>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
            {failing.length > 0 ? (
              <p className="text-xs text-[--color-fail]">
                {failing.length} gate{failing.length === 1 ? "" : "s"} failed, so the
                case was not weighed.
              </p>
            ) : null}
          </div>

          <div className="flex flex-col gap-2">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-[--color-muted]">
              Would change the outcome
            </h3>
            {(record.would_change_outcome ?? []).length === 0 ? (
              <Empty>Nothing recorded that would have changed it.</Empty>
            ) : (
              <ul className="flex flex-col gap-1 text-sm" data-testid="counterfactuals">
                {(record.would_change_outcome ?? []).map((entry) => (
                  <li key={entry.condition}>
                    {entry.condition} →{" "}
                    <span className="font-medium">
                      {entry.new_recommendation.replaceAll("_", " ").toLowerCase()}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2 border-t border-[--color-line] pt-3">
          <span className="text-xs text-[--color-muted]">Routed to</span>
          <Chip testId="route-chip" tone="accent">
            {(record.route ?? "unrouted").replaceAll("_", " ").toLowerCase()}
          </Chip>
          {(record.route_reasons ?? []).map((reason) => (
            <Chip key={reason}>{reason.replaceAll("_", " ").toLowerCase()}</Chip>
          ))}
          {record.challenger_open ? (
            <Chip tone="warn" testId="challenger-open">
              Challenger reservation open
            </Chip>
          ) : null}
        </div>
      </div>
    </Card>
  );
}

function FactorBar({
  family,
  score,
  decisive,
}: {
  family: string;
  score: FactorScore;
  decisive: boolean;
}) {
  const share = Math.max(0, Math.min(1, (score.score ?? 0) / 100));
  return (
    <div
      data-testid={`factor-${family}`}
      className={`rounded border px-3 py-2 ${
        decisive ? "border-[--color-accent] bg-sky-50" : "border-[--color-line]"
      }`}
    >
      <div className="flex items-baseline justify-between text-xs">
        <span className="font-medium">
          {family.toLowerCase()}
          {decisive ? (
            <span className="ml-2 text-[--color-accent]">decided this case</span>
          ) : null}
        </span>
        <span className="tabular-nums text-[--color-muted]">
          <span data-testid={`factor-${family}-score`}>{score.score}</span>
          {" × "}
          {score.weight} = <span className="font-semibold">{score.weighted}</span>
        </span>
      </div>
      <div className="mt-1 h-1.5 w-full rounded bg-[--color-line]">
        <div
          className={`h-1.5 rounded ${decisive ? "bg-[--color-accent]" : "bg-[--color-muted]"}`}
          style={{ width: `${share * 100}%` }}
        />
      </div>
    </div>
  );
}
