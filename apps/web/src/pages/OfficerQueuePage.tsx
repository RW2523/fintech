import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { useApi } from "../api";
import { Card, Chip, Empty, Meter, Problem, type Tone } from "../components/primitives";
import type { Queue, QueueEntry } from "../types";

/** How loudly a route reads. COMPLIANCE and ENHANCED are the two that mean
 *  "somebody must look at this now"; the rest are ordinary work. */
const ROUTE_TONE: Record<string, Tone> = {
  COMPLIANCE_REVIEW: "fail",
  COMPLIANCE: "fail",
  ENHANCED_ASSESSMENT: "warn",
  COMMITTEE: "warn",
  SENIOR_REVIEW: "accent",
  OFFICER_REVIEW: "neutral",
  AUTONOMOUS: "pass",
  MANUAL_FALLBACK: "fail",
};

const RECOMMENDATION_TONE: Record<string, Tone> = {
  APPROVE: "pass",
  APPROVE_WITH_CONDITIONS: "accent",
  REVIEW: "warn",
  DECLINE: "fail",
  COMPLIANCE_REVIEW: "fail",
  MORE_INFORMATION_REQUIRED: "warn",
};

/** docs/09 §2 — the officer queue.
 *
 *  Sorted by how much attention a case needs rather than by when it arrived:
 *  a compliance review left until tomorrow is a worse outcome than an officer
 *  review left until tomorrow. */
export function OfficerQueuePage() {
  const request = useApi();
  const [route, setRoute] = useState<string>("");

  const { data, isPending, error } = useQuery({
    queryKey: ["queue", route],
    queryFn: () => request<Queue>(`/api/decision/queue${route ? `?route=${route}` : ""}`),
  });

  const rows = useMemo(() => data?.decisions ?? [], [data]);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-3">
        <h1 className="text-base font-semibold tracking-tight">Officer queue</h1>
        <select
          data-testid="route-filter"
          value={route}
          onChange={(event) => setRoute(event.target.value)}
          className="rounded border border-[--color-line] bg-[--color-surface] px-2 py-1 text-xs"
        >
          <option value="">every route</option>
          {(data?.routes ?? []).map((name) => (
            <option key={name} value={name}>
              {name.replaceAll("_", " ").toLowerCase()}
            </option>
          ))}
        </select>
        <span className="text-xs text-[--color-muted]">
          {isPending ? "loading" : `${rows.length} waiting`}
        </span>
      </div>

      {error ? <Problem error={error} /> : null}

      <Card title="Decisions awaiting a person" testId="queue-card">
        {rows.length === 0 && !isPending ? (
          <Empty>
            Nothing is waiting. A case appears here once it has been assessed and
            routed to somebody.
          </Empty>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[--color-line] text-left text-xs text-[--color-muted]">
                <th className="py-2 font-medium">Case</th>
                <th className="font-medium">Member</th>
                <th className="font-medium">Tier</th>
                <th className="font-medium">Route</th>
                <th className="font-medium">Recommendation</th>
                <th className="font-medium">Confidence</th>
                <th className="font-medium">Disagreement</th>
                <th className="font-medium">Authority</th>
              </tr>
            </thead>
            <tbody data-testid="queue-rows">
              {rows.map((entry) => (
                <QueueRow key={entry.decision_record_id} entry={entry} />
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

function QueueRow({ entry }: { entry: QueueEntry }) {
  const caseId = entry.case_id ?? entry.snapshot_id ?? entry.decision_record_id;
  return (
    <tr
      data-testid={`queue-row-${caseId}`}
      className="border-b border-[--color-line] last:border-0 hover:bg-[--color-canvas]"
    >
      <td className="py-2">
        <Link
          to={`/officer/cases/${encodeURIComponent(caseId)}`}
          className="font-mono text-xs text-[--color-accent] underline decoration-dotted"
        >
          {caseId.slice(0, 18)}
        </Link>
      </td>
      <td className="font-mono text-xs">{entry.member_id ?? "not recorded"}</td>
      <td>
        <Chip>{entry.tier ?? "unknown"}</Chip>
      </td>
      <td>
        <Chip tone={ROUTE_TONE[entry.route ?? ""] ?? "neutral"} title={entry.route_reasons.join(", ")}>
          {(entry.route ?? "unrouted").replaceAll("_", " ").toLowerCase()}
        </Chip>
      </td>
      <td>
        <Chip tone={RECOMMENDATION_TONE[entry.recommendation ?? ""] ?? "neutral"}>
          {(entry.recommendation ?? "none").replaceAll("_", " ").toLowerCase()}
        </Chip>
      </td>
      <td className="w-32 pr-4">
        <Meter label="" value={entry.confidence} tone="accent" />
      </td>
      <td className="w-32 pr-4">
        <Meter label="" value={entry.disagreement} tone="warn" />
      </td>
      <td className="text-xs text-[--color-muted]">
        {entry.required_authority ?? "not set"}
        {entry.decided ? <> · <Chip tone="pass">decided</Chip></> : null}
      </td>
    </tr>
  );
}
