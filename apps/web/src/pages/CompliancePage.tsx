import type React from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { useApi } from "../api";
import { Card, Chip, Empty, Problem, type Tone } from "../components/primitives";

const WHY_TONE: Record<string, Tone> = {
  OVERRIDE: "warn",
  HIGH_DISAGREEMENT: "warn",
  DECIDED_ALONE: "accent",
  SAMPLE_OVERDUE: "fail",
};

type Attention = {
  window_days: number;
  count: number;
  unavailable: string[];
  cases: { case_id: string | null; decision_record_id: string; why: string; detail: string; at: string | null }[];
};

type Overrides = {
  window_days: number;
  decisions: number;
  overrides: number;
  override_rate: number | null;
  series: { day: string; decisions: number; overrides: number }[];
  by_reason: Record<string, number>;
  by_action: Record<string, number>;
};

/** docs/09 §6 — the compliance view.
 *
 *  Two questions. Which cases should somebody read, and where is the platform
 *  being told it is wrong. An override rate is not a fault count: a rate that
 *  falls to zero usually means people stopped reading, which is worse than a
 *  rate that stays where it is. */
export function CompliancePage() {
  const request = useApi();

  const attention = useQuery({
    queryKey: ["attention"],
    queryFn: () => request<Attention>("/api/governance/governance/attention"),
  });
  const overrides = useQuery({
    queryKey: ["overrides"],
    queryFn: () => request<Overrides>("/api/governance/governance/overrides"),
  });

  return (
    <div className="flex flex-col gap-5">
      <header>
        <h1 className="text-[34px] leading-tight font-extrabold tracking-tight">Compliance</h1>
        <p className="text-sm text-muted">
          Where a person departed from the platform, and what is worth reading
          because of it.
        </p>
      </header>
      <Card title="Overrides" icon="scale" tone="warn" testId="overrides">
        {overrides.error ? <Problem error={overrides.error} /> : null}
        {overrides.data ? (
          <div className="flex flex-col gap-3">
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-4 text-sm">
              <Figure label="Decisions" value={overrides.data.decisions} testId="override-decisions" />
              <Figure label="Overrides" value={overrides.data.overrides} testId="override-count" />
              <Figure
                label="Rate"
                testId="override-rate"
                value={
                  overrides.data.override_rate === null
                    ? "no decisions yet"
                    : `${(overrides.data.override_rate * 100).toFixed(1)}%`
                }
              />
              <Figure label="Window" value={`${overrides.data.window_days} days`} />
            </div>

            <div>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">
                By reason
              </h3>
              <ul className="mt-1 flex flex-wrap gap-2 text-xs" data-testid="override-reasons">
                {Object.entries(overrides.data.by_reason)
                  .filter(([, count]) => count > 0)
                  .map(([code, count]) => (
                    <li key={code}>
                      <Chip tone="warn">
                        {code} · {count}
                      </Chip>
                    </li>
                  ))}
                {Object.values(overrides.data.by_reason).every((count) => count === 0) ? (
                  <li className="text-muted">
                    {/* Stated rather than shown as an empty chart: nobody has
                        overridden yet is a different fact from no data. */}
                    Nobody has overridden a recommendation in this window.
                  </li>
                ) : null}
              </ul>
            </div>
          </div>
        ) : null}
      </Card>

      <Card title="Worth reading" icon="alert" tone="fail" testId="attention">
        {attention.error ? <Problem error={attention.error} /> : null}
        {attention.data ? (
          attention.data.cases.length === 0 ? (
            <Empty>
              Nothing in the last {attention.data.window_days} days was overridden,
              sharply disputed, decided alone or left past its review deadline.
            </Empty>
          ) : (
            <ul className="flex flex-col gap-2" data-testid="attention-rows">
              {attention.data.cases.map((row, index) => (
                <li
                  key={`${row.decision_record_id}-${row.why}-${index}`}
                  data-testid={`attention-${row.why}`}
                  className="flex flex-wrap items-center gap-2 rounded border border-line px-3 py-2 text-xs"
                >
                  <Chip tone={WHY_TONE[row.why] ?? "neutral"}>
                    {row.why.replaceAll("_", " ").toLowerCase()}
                  </Chip>
                  {row.case_id ? (
                    <Link
                      to={`/ledger/${encodeURIComponent(row.case_id)}`}
                      className="font-mono text-accent underline decoration-dotted"
                    >
                      {row.case_id.slice(0, 20)}
                    </Link>
                  ) : (
                    <span className="font-mono">{row.decision_record_id.slice(0, 20)}</span>
                  )}
                  {/* The reason is on the row, not inferred from a column: a
                      list of case ids tells a compliance officer nothing about
                      which one to open first. */}
                  <span className="text-muted">{row.detail}</span>
                  <span className="ml-auto text-muted">{row.at ?? ""}</span>
                </li>
              ))}
            </ul>
          )
        ) : null}
        {attention.data && attention.data.unavailable.length > 0 ? (
          <p className="mt-2 text-xs text-warn" data-testid="attention-unavailable">
            could not be read: {attention.data.unavailable.join(", ")}
          </p>
        ) : null}
      </Card>
    </div>
  );
}

function Figure({
  label,
  value,
  testId,
}: {
  label: string;
  value: React.ReactNode;
  testId?: string;
}) {
  return (
    <div>
      <div className="text-xs uppercase tracking-wide text-muted">{label}</div>
      <div className="text-lg font-semibold tabular-nums" data-testid={testId}>
        {value}
      </div>
    </div>
  );
}
