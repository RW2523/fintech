import { Card, Chip, Empty, Figure, Glyph, Meter, type Tone } from "./primitives";
import type { DecisionRecord, FactorScore } from "../types";
import { gateFamily, means, say } from "../vocabulary";

/** The banner's own surface. Softer than a chip, because it fills a panel. */
const TONE_PANEL: Record<Tone, string> = {
  neutral: "border-line bg-raised",
  pass: "border-pass-line bg-pass-soft",
  warn: "border-warn-line bg-warn-soft",
  fail: "border-fail-line bg-fail-soft",
  accent: "border-accent-line bg-accent-soft",
  note: "border-note-line bg-note-soft",
};

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
          {say("recommendation", record.recommendation)}
        </Chip>
      }
    >
      <div className="flex flex-col gap-4">
        {/* The recommendation, said once and loudly. It was a chip in the
            corner of the header, which is where a reader looks last. */}
        <div
          data-testid="recommendation-banner"
          className={`flex flex-wrap items-center justify-between gap-4 rounded-xl border p-4 ${
            TONE_PANEL[RECOMMENDATION_TONE[record.recommendation ?? ""] ?? "neutral"]
          }`}
        >
          <div className="flex items-center gap-3.5">
            <Glyph
              icon={
                record.recommendation === "APPROVE"
                  ? "check"
                  : record.recommendation === "DECLINE"
                    ? "alert"
                    : "scale"
              }
              tone={RECOMMENDATION_TONE[record.recommendation ?? ""] ?? "neutral"}
              size="lg"
            />
            <div>
              <p className="text-xs text-muted">The platform recommends</p>
              <p className="text-2xl font-bold tracking-tight">
                {say("recommendation", record.recommendation)}
              </p>
              <p className="mt-0.5 max-w-md text-xs text-muted">
                {means("recommendation", record.recommendation)}
              </p>
            </div>
          </div>
          <dl className="flex items-center gap-7 text-right">
            <div>
              <dt className="text-xs text-muted">Confidence</dt>
              <dd className="text-lg font-bold tabular-nums">
                {record.confidence == null ? "—" : record.confidence.toFixed(2)}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-muted">Disagreement</dt>
              <dd className="text-lg font-bold tabular-nums">
                {record.disagreement == null ? "—" : record.disagreement.toFixed(2)}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-muted">Settled at</dt>
              <dd className="text-sm font-semibold" title={means("step", record.deciding_step)}>
                {say("step", record.deciding_step)}
              </dd>
            </div>
          </dl>
        </div>

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
          <Figure
            label="Deliberation"
            testId="tier"
            value={record.tier ?? "unknown"}
            hint={means("tier", record.tier)}
          />
          <Figure
            label="Who signs it off"
            testId="required-authority"
            value={record.required_authority ?? "not set"}
            hint={say("authority", record.required_authority)}
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
          <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">
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
            <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">
              Hard gates
            </h3>
            {gates.length === 0 ? (
              <Empty>No gate was evaluated.</Empty>
            ) : (
              <ul className="flex flex-wrap gap-1.5" data-testid="hard-gates">
                {gates.map((gate) => {
                  const failed = ["FAIL", "BLOCK"].includes(gate.result);
                  return (
                    <li key={gate.rule_id}>
                      {/* One chip per gate rather than a row each. Twelve rows
                          made this card taller than the viewport and pushed
                          the officer's own actions off the bottom of it. */}
                      <Chip
                        dot
                        tone={failed ? "fail" : "pass"}
                        title={`${gateFamily(gate.rule_id)}: ${gate.result}${gate.clause_id ? ` · ${gate.clause_id}` : ""}`}
                      >
                        {gate.rule_id}
                      </Chip>
                    </li>
                  );
                })}
              </ul>
            )}
            {failing.length > 0 ? (
              <p className="text-xs text-fail">
                {failing.length} gate{failing.length === 1 ? "" : "s"} failed, so the
                case was not weighed.
              </p>
            ) : null}
          </div>

          <div className="flex flex-col gap-2">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">
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

        <div className="flex flex-wrap items-center gap-2 border-t border-line pt-3">
          <span className="text-xs text-muted">Routed to</span>
          <Chip testId="route-chip" tone="accent" title={means("route", record.route)}>
            {say("route", record.route)}
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
        decisive ? "border-accent bg-sky-50" : "border-line"
      }`}
    >
      <div className="flex items-baseline justify-between text-xs">
        <span className="font-medium">
          {family.toLowerCase()}
          {decisive ? (
            <span className="ml-2 text-accent">decided this case</span>
          ) : null}
        </span>
        <span className="tabular-nums text-muted">
          <span data-testid={`factor-${family}-score`}>{score.score}</span>
          {" × "}
          {score.weight} = <span className="font-semibold">{score.weighted}</span>
        </span>
      </div>
      <div className="mt-1 h-1.5 w-full rounded bg-line">
        <div
          className={`h-1.5 rounded ${decisive ? "bg-accent" : "bg-muted"}`}
          style={{ width: `${share * 100}%` }}
        />
      </div>
    </div>
  );
}
