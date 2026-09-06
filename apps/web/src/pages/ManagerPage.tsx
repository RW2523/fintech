import { useState } from "react";
import { useMutation, useQueries } from "@tanstack/react-query";

import { useApi } from "../api";
import { Card, Chip, Empty, Problem } from "../components/primitives";

/** docs/09 §7 — the management cockpit.
 *
 *  Every tile reads the governed metrics endpoint, and so does the copilot
 *  beside them. That is the whole design: a tile and the answer under it are
 *  one number rather than two calculations that agree today. Nothing here
 *  computes anything from what it fetched. */

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

/** A number as the API gave it. No rounding, no percentages: the tile shows
 *  what the endpoint returned so a manager comparing the screen with the API
 *  sees the same characters. */
function show(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") return String(value);
  return String(value);
}

function MetricTile({ metric, title, days }: { metric: string; title: string; days: number }) {
  const request = useApi();
  const [query] = useQueries({
    queries: [
      {
        queryKey: ["metric", metric, days],
        queryFn: () =>
          request<MetricResult>(`/api/governance/governance/metrics/${metric}?days=${days}`),
      },
    ],
  });

  return (
    <Card title={title} testId={`tile-${metric}`}>
      {query.error ? <Problem error={query.error} /> : null}
      {query.isPending ? <Empty>Reading…</Empty> : null}
      {query.data ? (
        <div className="flex flex-col gap-2">
          {query.data.totals ? (
            <dl className="flex flex-wrap gap-x-4 gap-y-1 text-sm" data-testid={`totals-${metric}`}>
              {Object.entries(query.data.totals).map(([name, value]) => (
                <div key={name} className="flex items-baseline gap-1">
                  <dt className="text-xs text-[--color-muted]">{name.replace(/_/g, " ")}</dt>
                  <dd className="font-mono" data-testid={`total-${metric}-${name}`}>
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
                  <tr>
                    {Object.keys(query.data.rows[0]).map((key) => (
                      <th key={key} className="py-1 pr-3 font-normal">
                        {key.replace(/_/g, " ")}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="font-mono">
                  {query.data.rows.slice(0, 12).map((row, index) => (
                    <tr key={index} className="border-t border-[--color-line]">
                      {Object.entries(row).map(([key, value]) => (
                        <td key={key} className="py-1 pr-3" data-testid={`cell-${metric}-${index}-${key}`}>
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

export function ManagerPage() {
  const request = useApi();
  const [days, setDays] = useState<number>(90);
  const [question, setQuestion] = useState("");
  const [asked, setAsked] = useState<string | null>(null);

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

  return (
    <div className="flex flex-col gap-4" data-testid="manager-page">
      <div className="flex items-center gap-2">
        <span className="text-xs text-[--color-muted]">Window</span>
        {WINDOWS.map((option) => (
          <button
            key={option}
            type="button"
            data-testid={`window-${option}`}
            onClick={() => setDays(option)}
            className={`rounded border px-2 py-0.5 text-xs ${
              days === option ? "border-[--color-accent]" : "border-[--color-line] text-[--color-muted]"
            }`}
          >
            {option} days
          </button>
        ))}
        <Chip tone="warn">SYNTHETIC DATA</Chip>
      </div>

      <Card title="Ask the portfolio" testId="ask-portfolio">
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
              className="flex-1 rounded border border-[--color-line] bg-[--color-surface] px-2 py-1 text-sm"
            />
            <button
              type="submit"
              data-testid="portfolio-submit"
              disabled={ask.isPending || question.trim().length < 3}
              className="rounded border border-[--color-accent] px-3 py-1 text-sm disabled:cursor-not-allowed disabled:opacity-50"
            >
              {ask.isPending ? "Reading" : "Ask"}
            </button>
          </form>

          {ask.error ? <Problem error={ask.error} /> : null}

          {ask.data ? (
            <div className="rounded border border-[--color-line] p-3" data-testid="portfolio-answer">
              <p className="text-xs text-[--color-muted]">{asked}</p>

              {ask.data.refusal ? (
                <div className="mt-2" data-testid="portfolio-refusal">
                  <Chip tone="warn">{ask.data.refusal.code ?? "refused"}</Chip>
                  <p className="mt-1 text-sm">{ask.data.refusal.reason}</p>
                </div>
              ) : (
                <p className="mt-2 whitespace-pre-line text-sm" data-testid="portfolio-text">
                  {ask.data.answer}
                </p>
              )}

              {/* The tables the narrative rests on. A paragraph about a book
                  with no table under it is a claim, not a report. */}
              {ask.data.metrics.length > 0 ? (
                <ul className="mt-2 flex flex-wrap gap-1 text-xs" data-testid="portfolio-metrics">
                  {ask.data.metrics.map((entry) => (
                    <li
                      key={entry.metric}
                      className="rounded border border-[--color-line] px-2 py-0.5 font-mono"
                    >
                      {entry.metric}
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
            aggregates and cannot see a member, a case or an account. It reports
            what happened and does not forecast.
          </p>
        </div>
      </Card>

      <div className="grid gap-4 md:grid-cols-2">
        {TILES.map((tile) => (
          <MetricTile key={tile.metric} metric={tile.metric} title={tile.title} days={days} />
        ))}
      </div>
    </div>
  );
}
