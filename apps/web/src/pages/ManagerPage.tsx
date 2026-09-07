import { useState } from "react";
import { useMutation, useQueries, useQuery } from "@tanstack/react-query";

import { useApi } from "../api";
import {
  BarSeries,
  Button,
  Card,
  Chip,
  Donut,
  Empty,
  FilterChips,
  Finding,
  Problem,
  Stat,
  type Tone,
} from "../components/primitives";

/** docs/09 §7 — the portfolio overview.
 *
 *  Every figure reads the governed metrics endpoint, and so does the copilot
 *  beside them. That is the whole design: a tile and the answer under it are
 *  one number rather than two calculations that agree today.
 *
 *  Nothing here computes. The headline figures are read straight out of a
 *  `totals` block; the sparklines and the bars plot a `series` the service
 *  returned. What this page deliberately does *not* show is a change against
 *  the previous window — "+6pp on last month" is arithmetic, arithmetic is a
 *  number, and a number this app worked out for itself is a number with no
 *  `calc_id` behind it (CLAUDE.md §2.1). When the metrics service publishes a
 *  delta, this will show it. */

type MetricRow = Record<string, unknown>;

type MetricResult = {
  metric: string;
  means: string;
  window_days: number;
  as_of: string;
  rows: MetricRow[];
  totals?: Record<string, number | null>;
  series?: MetricRow[];
  series_means?: string;
  sources: string[];
};

type PortfolioAnswer = {
  answer: string;
  citations: { ref: string; what: string }[];
  refusal?: { reason: string; code?: string };
  metrics: { metric: string; result: MetricResult }[];
  tools_unavailable: string[];
};

const TILES = [
  { metric: "applications", title: "Flow" },
  { metric: "routing", title: "Where work went" },
  { metric: "recommendations", title: "What was recommended" },
  { metric: "tiers", title: "Committee depth" },
  { metric: "authority", title: "Authority required" },
  { metric: "early_warning", title: "Early warning" },
  { metric: "delinquency", title: "The book" },
  { metric: "outreach", title: "Handoffs" },
] as const;

const WINDOWS = [30, 90, 365] as const;

/** How each early-warning state reads. The order is the ladder itself, so the
 *  ring runs from calm to critical rather than alphabetically. */
const STATE_TONE: Record<string, Tone> = {
  STABLE: "pass",
  WATCH: "warn",
  ELEVATED: "warn",
  CRITICAL: "fail",
  RECOVERY: "accent",
};
const STATE_ORDER = ["STABLE", "RECOVERY", "WATCH", "ELEVATED", "CRITICAL"];

/** A number as the API gave it. No rounding, no percentages: the tile shows
 *  what the endpoint returned so a manager comparing the screen with the API
 *  sees the same characters. */
function show(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") return String(value);
  return String(value);
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** A percentage for a headline, from a share the service already worked out.
 *  Formatting is not computing; the tile below still shows the raw share. */
function asPercent(share: number | null): string {
  return share == null ? "—" : `${(share * 100).toFixed(0)}%`;
}

export function ManagerPage() {
  const request = useApi();
  const [days, setDays] = useState<number>(90);
  const [question, setQuestion] = useState("");
  const [asked, setAsked] = useState<string | null>(null);

  const headline = useQueries({
    queries: ["applications", "recommendations", "early_warning", "routing"].map((metric) => ({
      queryKey: ["metric", metric, days],
      queryFn: () =>
        request<MetricResult>(`/api/governance/governance/metrics/${metric}?days=${days}`),
    })),
  });
  const [applications, recommendations, earlyWarning, routing] = headline.map((q) => q.data);

  const ask = useMutation({
    mutationFn: (text: string) =>
      request<PortfolioAnswer>("/api/agent_runtime/copilot/portfolio", {
        method: "POST",
        body: { question: text, days },
      }),
  });

  function submit(text: string) {
    const trimmed = text.trim();
    if (trimmed.length < 3) return;
    setAsked(trimmed);
    ask.mutate(trimmed);
  }

  const flow = applications?.rows?.[0] ?? {};
  const flowSeries = (applications?.series ?? []).map((row) => num(row.total) ?? 0);
  const decidedSeries = (applications?.series ?? []).map((row) => num(row.matched) ?? 0);

  const states = (earlyWarning?.rows ?? [])
    .map((row) => ({
      label: String(row.state ?? "unknown"),
      count: num(row.members) ?? 0,
      share: num(row.share) ?? 0,
      tone: STATE_TONE[String(row.state)] ?? "neutral",
    }))
    .sort((a, b) => STATE_ORDER.indexOf(a.label) - STATE_ORDER.indexOf(b.label));

  return (
    <div className="flex flex-col gap-5" data-testid="manager-page">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Portfolio overview</h1>
          <p className="text-sm text-[--color-muted]">
            What the platform decided, where the work went, and how the book is
            behaving.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <FilterChips
            options={WINDOWS.map((option) => ({ key: String(option), label: `${option} days` }))}
            value={String(days)}
            onChange={(key) => setDays(Number(key))}
            testIdPrefix="window"
          />
        </div>
      </header>

      {/* The four figures a manager opens this screen for. Each is a `totals`
          value the metrics service published; the sparkline beside it plots
          that metric's own monthly series. */}
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat
          label="Cases assessed"
          icon="applications"
          tone="accent"
          value={show(flow.received)}
          series={flowSeries}
          hint={`in the last ${days} days`}
          testId="headline-received"
        />
        <Stat
          label="Waiting for a person"
          icon="clock"
          tone={num(flow.pending) ? "warn" : "pass"}
          value={show(flow.pending)}
          series={decidedSeries}
          hint="not yet decided"
          testId="headline-pending"
        />
        <Stat
          label="Recommended approve"
          icon="check"
          tone="pass"
          value={asPercent(num(recommendations?.totals?.approval_rate))}
          hint="share the platform recommended, not the share a person approved"
          testId="headline-approval"
        />
        <Stat
          label="Members under early warning"
          icon="alert"
          tone={states.some((s) => s.label === "CRITICAL") ? "fail" : "warn"}
          value={show(earlyWarning?.totals?.members)}
          hint="across every open state"
          testId="headline-early-warning"
        />
      </div>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <Card
          title="Cases month by month"
          icon="chart"
          tone="accent"
          testId="flow-chart"
          right={
            applications?.series_means ? (
              <span className="hidden text-xs text-[--color-muted] sm:block">
                {applications.series_means}
              </span>
            ) : null
          }
        >
          {flowSeries.length === 0 ? (
            <Empty>No month in this window has a case in it.</Empty>
          ) : (
            <BarSeries
              testId="flow-bars"
              bars={[
                { label: "Assessed", tone: "accent" },
                { label: "Decided", tone: "pass" },
              ]}
              rows={(applications?.series ?? []).map((row) => ({
                label: String(row.month ?? ""),
                values: [num(row.total) ?? 0, num(row.matched) ?? 0],
              }))}
            />
          )}
        </Card>

        <Card title="Early-warning mix" icon="alert" tone="warn" testId="risk-mix">
          {states.length === 0 ? (
            <Empty>No member is in an early-warning state.</Empty>
          ) : (
            <Donut
              slices={states}
              total={num(earlyWarning?.totals?.members) ?? 0}
              caption="members"
              testId="risk-donut"
            />
          )}
        </Card>
      </div>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <Card title="Ask the portfolio" icon="chat" tone="accent" testId="ask-portfolio">
          <div className="flex flex-col gap-3">
            <form
              className="flex gap-2"
              onSubmit={(event) => {
                event.preventDefault();
                submit(question);
              }}
            >
              <input
                data-testid="portfolio-input"
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                placeholder="Why did approvals fall this quarter?"
                className="flex-1 rounded-lg border border-[--color-line] bg-[--color-surface] px-3 py-2 text-sm"
              />
              <Button
                type="submit"
                variant="primary"
                testId="portfolio-submit"
                disabled={ask.isPending || question.trim().length < 3}
              >
                {ask.isPending ? "Reading" : "Ask"}
              </Button>
            </form>

            {ask.error ? <Problem error={ask.error} /> : null}

            {ask.data ? (
              <div
                className="rounded-lg border border-[--color-line] bg-[--color-raised] p-3"
                data-testid="portfolio-answer"
              >
                <p className="text-xs text-[--color-muted]">{asked}</p>

                {ask.data.refusal ? (
                  <div className="mt-2" data-testid="portfolio-refusal">
                    <Chip tone="warn">{ask.data.refusal.code ?? "refused"}</Chip>
                    <p className="mt-1.5 text-sm">{ask.data.refusal.reason}</p>
                  </div>
                ) : (
                  <p className="mt-2 text-sm whitespace-pre-line" data-testid="portfolio-text">
                    {ask.data.answer}
                  </p>
                )}

                {/* The tables the narrative rests on. A paragraph about a book
                    with no table under it is a claim, not a report. */}
                {ask.data.metrics.length > 0 ? (
                  <ul className="mt-3 flex flex-wrap gap-1.5" data-testid="portfolio-metrics">
                    {ask.data.metrics.map((entry) => (
                      <li key={entry.metric}>
                        <Chip tone="accent">{entry.metric}</Chip>
                      </li>
                    ))}
                  </ul>
                ) : null}

                {ask.data.tools_unavailable.length > 0 ? (
                  <p className="mt-2 text-[11px] text-[--color-muted]" data-testid="portfolio-gaps">
                    could not read {ask.data.tools_unavailable.join(", ")}
                  </p>
                ) : null}
              </div>
            ) : null}

            <p className="text-xs text-[--color-muted]" data-testid="portfolio-limits">
              Answers use only the metrics on this page. The copilot reads
              aggregates and cannot see a member, a case or an account. It
              reports what happened and does not forecast.
            </p>
          </div>
        </Card>

        <Card title="Where the work went" icon="route" tone="accent" testId="routing-summary">
          {routing?.rows?.length ? (
            <div className="flex flex-col gap-2">
              {routing.rows.map((row) => (
                <Finding
                  key={String(row.route)}
                  tone={
                    String(row.route) === "AUTONOMOUS"
                      ? "pass"
                      : String(row.route).startsWith("COMPLIANCE")
                        ? "fail"
                        : "neutral"
                  }
                  icon="route"
                  title={String(row.route ?? "").replaceAll("_", " ").toLowerCase()}
                  detail={`${show(row.decisions)} decisions`}
                  right={<Chip tone="neutral">{asPercent(num(row.share))}</Chip>}
                />
              ))}
              <p className="text-[11px] text-[--color-muted]">{routing.means}</p>
            </div>
          ) : (
            <Empty>Nothing was routed in this window.</Empty>
          )}
        </Card>
      </div>

      <div>
        <h2 className="mb-3 text-sm font-semibold tracking-tight text-[--color-muted]">
          Every metric, exactly as the service returned it
        </h2>
        <div className="grid gap-4 xl:grid-cols-2">
          {TILES.map((tile) => (
            <MetricTile key={tile.metric} metric={tile.metric} title={tile.title} days={days} />
          ))}
        </div>
      </div>
    </div>
  );
}

function MetricTile({ metric, title, days }: { metric: string; title: string; days: number }) {
  const request = useApi();
  const query = useQuery({
    queryKey: ["metric", metric, days],
    queryFn: () =>
      request<MetricResult>(`/api/governance/governance/metrics/${metric}?days=${days}`),
  });

  return (
    <Card title={title} icon="chart" testId={`tile-${metric}`}>
      {query.error ? <Problem error={query.error} /> : null}
      {query.isPending ? <Empty>Reading…</Empty> : null}
      {query.data ? (
        <div className="flex flex-col gap-3">
          {query.data.totals ? (
            <dl className="flex flex-wrap gap-x-5 gap-y-2" data-testid={`totals-${metric}`}>
              {Object.entries(query.data.totals).map(([name, value]) => (
                <div key={name} className="flex flex-col">
                  <dt className="text-xs text-[--color-muted]">{name.replace(/_/g, " ")}</dt>
                  <dd
                    className="font-mono text-sm font-semibold"
                    data-testid={`total-${metric}-${name}`}
                  >
                    {show(value)}
                  </dd>
                </div>
              ))}
            </dl>
          ) : null}

          {query.data.rows.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs" data-testid={`rows-${metric}`}>
                <thead className="text-[--color-muted]">
                  <tr className="border-b border-[--color-line]">
                    {Object.keys(query.data.rows[0]).map((key) => (
                      <th key={key} className="py-1.5 pr-3 font-medium whitespace-nowrap">
                        {key.replace(/_/g, " ")}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="font-mono">
                  {query.data.rows.slice(0, 12).map((row, index) => (
                    <tr key={index} className="border-b border-[--color-line] last:border-0">
                      {Object.entries(row).map(([key, value]) => (
                        <td
                          key={key}
                          className="py-1.5 pr-3 whitespace-nowrap"
                          data-testid={`cell-${metric}-${index}-${key}`}
                        >
                          {show(value)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty>Nothing in this window.</Empty>
          )}

          {/* The definition travels with the number. "Autonomous share 0.25"
              cannot be read without knowing whether it is of all decisions or
              of the ones eligible to be automatic. */}
          <p className="text-[11px] text-[--color-muted]" data-testid={`means-${metric}`}>
            {query.data.means} Read from {query.data.sources.join(", ")}.
          </p>
        </div>
      ) : null}
    </Card>
  );
}
