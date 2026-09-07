import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { useApi } from "../api";
import { Icon } from "../components/icons";
import {
  Button,
  Card,
  Cell,
  Chip,
  Empty,
  FilterChips,
  Meter,
  Problem,
  Row,
  Table,
  type Tone,
} from "../components/primitives";
import type { DecisionRecord, Queue, QueueEntry } from "../types";

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

/** The three ways a queue is actually worked, as chips rather than a dropdown.
 *
 *  A select hides its options and, worse, hides how many are behind each one.
 *  An officer opening this screen wants to know whether anything needs them
 *  now before they read a single row. */
type Lane = "all" | "attention" | "waiting" | "decided";

const LANES: { key: Lane; label: string; tone?: Tone }[] = [
  { key: "all", label: "All" },
  { key: "attention", label: "Needs attention", tone: "fail" },
  { key: "waiting", label: "Awaiting a person", tone: "warn" },
  { key: "decided", label: "Decided", tone: "pass" },
];

const LOUD_ROUTES = new Set([
  "COMPLIANCE",
  "COMPLIANCE_REVIEW",
  "ENHANCED_ASSESSMENT",
  "MANUAL_FALLBACK",
]);

function inLane(entry: QueueEntry, lane: Lane): boolean {
  if (lane === "all") return true;
  if (lane === "decided") return entry.decided;
  if (lane === "attention")
    return !entry.decided && LOUD_ROUTES.has(entry.route ?? "");
  return !entry.decided;
}

function caseOf(entry: QueueEntry): string {
  return entry.case_id ?? entry.snapshot_id ?? entry.decision_record_id;
}

/** docs/09 §2 — the applications queue.
 *
 *  Two panes. On the left every case the platform has assessed, with the
 *  route, what it recommended and how sure it was; on the right whichever one
 *  is selected, in enough detail to decide whether to open it. Triage without
 *  losing your place in the list, which is the thing the single-column version
 *  made impossible: opening a case meant navigating away and coming back to
 *  the top of the queue. */
export function OfficerQueuePage() {
  const request = useApi();
  const [lane, setLane] = useState<Lane>("all");
  const [route, setRoute] = useState<string>("");
  const [selected, setSelected] = useState<string | null>(null);

  const { data, isPending, error } = useQuery({
    queryKey: ["queue", route],
    queryFn: () =>
      request<Queue>(`/api/decision/queue${route ? `?route=${route}` : ""}`),
  });

  const all = useMemo(() => data?.decisions ?? [], [data]);
  const rows = useMemo(
    () => all.filter((entry) => inLane(entry, lane)),
    [all, lane],
  );

  const counts = useMemo(
    () =>
      LANES.map((option) => ({
        ...option,
        count: all.filter((entry) => inLane(entry, option.key)).length,
      })),
    [all],
  );

  // The first row, until somebody picks another. A right-hand pane that starts
  // empty asks the reader to click before it will tell them anything.
  useEffect(() => {
    if (rows.length === 0) {
      setSelected(null);
    } else if (!rows.some((entry) => entry.decision_record_id === selected)) {
      setSelected(rows[0].decision_record_id);
    }
  }, [rows, selected]);

  const chosen =
    rows.find((entry) => entry.decision_record_id === selected) ?? null;

  return (
    <div className="flex flex-col gap-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            Applications queue
          </h1>
          <p className="text-sm text-[--color-muted]">
            Every case the platform has assessed, and what it is waiting for.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select
            data-testid="route-filter"
            value={route}
            onChange={(event) => setRoute(event.target.value)}
            className="rounded-lg border border-[--color-line] bg-[--color-surface] px-3 py-2 text-sm"
          >
            <option value="">Every route</option>
            {(data?.routes ?? []).map((name) => (
              <option key={name} value={name}>
                {name.replaceAll("_", " ").toLowerCase()}
              </option>
            ))}
          </select>
        </div>
      </header>

      {error ? <Problem error={error} /> : null}

      <FilterChips
        options={counts}
        value={lane}
        onChange={setLane}
        testIdPrefix="lane"
      />

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_22rem]">
        <Card testId="queue-card" padded={false}>
          {rows.length === 0 ? (
            <div className="p-4">
              <Empty>
                {isPending
                  ? "Loading."
                  : lane === "all"
                    ? "Nothing is waiting. A case appears here once it has been assessed and routed to somebody."
                    : "Nothing in this lane."}
              </Empty>
            </div>
          ) : (
            <Table
              testId="queue-table"
              bodyTestId="queue-rows"
              head={[
                "Case",
                "Member",
                "Route",
                "Recommendation",
                "Confidence",
                "Authority",
                "",
              ]}
            >
              {rows.map((entry) => {
                const caseId = caseOf(entry);
                return (
                  <Row
                    key={entry.decision_record_id}
                    testId={`queue-row-${caseId}`}
                    selected={entry.decision_record_id === selected}
                    onClick={() => setSelected(entry.decision_record_id)}
                  >
                    <Cell>
                      <span className="flex flex-col">
                        <Link
                          to={`/officer/cases/${encodeURIComponent(caseId)}`}
                          className="font-mono text-xs font-medium text-[--color-accent] underline decoration-dotted"
                        >
                          {caseId.slice(0, 20)}
                        </Link>
                        <span className="text-[11px] text-[--color-faint]">
                          {entry.tier ?? "tier unknown"}
                        </span>
                      </span>
                    </Cell>
                    <Cell className="font-mono text-xs">
                      {entry.member_id ?? "not recorded"}
                    </Cell>
                    <Cell>
                      <Chip
                        dot
                        tone={ROUTE_TONE[entry.route ?? ""] ?? "neutral"}
                        title={entry.route_reasons.join(", ")}
                      >
                        {(entry.route ?? "unrouted")
                          .replaceAll("_", " ")
                          .toLowerCase()}
                      </Chip>
                    </Cell>
                    <Cell>
                      <Chip
                        tone={
                          RECOMMENDATION_TONE[entry.recommendation ?? ""] ??
                          "neutral"
                        }
                      >
                        {(entry.recommendation ?? "none")
                          .replaceAll("_", " ")
                          .toLowerCase()}
                      </Chip>
                    </Cell>
                    <Cell className="w-28">
                      <Meter label="" value={entry.confidence} tone="accent" />
                    </Cell>
                    <Cell className="text-xs text-[--color-muted]">
                      {entry.required_authority ?? "not set"}
                    </Cell>
                    <Cell>
                      {entry.decided ? (
                        <Chip tone="pass">decided</Chip>
                      ) : (
                        <Icon.arrowRight className="h-4 w-4 text-[--color-faint]" />
                      )}
                    </Cell>
                  </Row>
                );
              })}
            </Table>
          )}
        </Card>

        <QueueDetail entry={chosen} />
      </div>
    </div>
  );
}

/** The selected case, in enough detail to decide whether to open it.
 *
 *  Every figure here is read from the decision record, not recomputed: this
 *  panel is a second view of the same row the case page shows, and two views
 *  that could disagree would be worse than one. */
function QueueDetail({ entry }: { entry: QueueEntry | null }) {
  const request = useApi();
  const { data } = useQuery({
    queryKey: ["record", entry?.decision_record_id],
    enabled: Boolean(entry),
    queryFn: () =>
      request<{ record: DecisionRecord }>(
        `/api/decision/decision-records/${entry!.decision_record_id}`,
      ),
  });

  if (!entry) {
    return (
      <Card title="Case detail" icon="applications" testId="queue-detail">
        <Empty>Select a case to see what the platform found.</Empty>
      </Card>
    );
  }

  const caseId = caseOf(entry);
  const record = data?.record;
  const gates = record?.hard_gates ?? [];
  const failed = gates.filter((gate) => gate.result === "FAIL");

  return (
    <Card
      title="Case detail"
      icon="applications"
      tone="accent"
      testId="queue-detail"
      right={
        <Chip tone={entry.decided ? "pass" : "warn"}>
          {entry.decided ? "decided" : "open"}
        </Chip>
      }
    >
      <div className="flex flex-col gap-4">
        <div>
          <p className="font-mono text-xs break-all text-[--color-muted]">
            {caseId}
          </p>
          <p className="mt-1 text-sm">
            <span className="font-semibold">
              {(entry.recommendation ?? "no recommendation")
                .replaceAll("_", " ")
                .toLowerCase()}
            </span>
            {entry.required_authority ? (
              <span className="text-[--color-muted]">
                {" "}
                · needs{" "}
                {entry.required_authority.replaceAll("_", " ").toLowerCase()}
              </span>
            ) : null}
          </p>
        </div>

        <dl className="grid grid-cols-2 gap-3">
          <Fact label="Member" value={entry.member_id ?? "not recorded"} mono />
          <Fact label="Tier" value={entry.tier ?? "unknown"} />
          <Fact
            label="Confidence"
            value={
              entry.confidence == null
                ? "not scored"
                : entry.confidence.toFixed(2)
            }
          />
          <Fact
            label="Disagreement"
            value={
              entry.disagreement == null
                ? "not scored"
                : entry.disagreement.toFixed(2)
            }
          />
          <Fact
            label="Weighted score"
            value={
              entry.weighted_score == null
                ? "not scored"
                : entry.weighted_score.toFixed(1)
            }
          />
          <Fact label="Deciding step" value={record?.deciding_step ?? "—"} />
        </dl>

        {entry.route_reasons.length > 0 ? (
          <div>
            <p className="mb-1.5 text-xs font-medium text-[--color-muted]">
              Why it is routed here
            </p>
            <ul className="flex flex-col gap-1">
              {entry.route_reasons.map((reason) => (
                <li
                  key={reason}
                  className="font-mono text-xs text-[--color-ink]"
                >
                  {reason}
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {gates.length > 0 ? (
          <div>
            <p className="mb-1.5 text-xs font-medium text-[--color-muted]">
              Hard gates
              <span className="ml-1.5 text-[--color-faint]">
                {failed.length === 0 ? "all passed" : `${failed.length} failed`}
              </span>
            </p>
            <div className="flex flex-wrap gap-1.5">
              {gates.map((gate) => (
                <Chip
                  key={gate.rule_id}
                  tone={gate.result === "PASS" ? "pass" : "fail"}
                  title={gate.reason_code ?? gate.result}
                >
                  {gate.rule_id}
                </Chip>
              ))}
            </div>
          </div>
        ) : null}

        <Link
          to={`/officer/cases/${encodeURIComponent(caseId)}`}
          className="block"
        >
          <Button variant="primary" icon="arrowRight" testId="open-case">
            Open the case
          </Button>
        </Link>
      </div>
    </Card>
  );
}

function Fact({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div>
      <dt className="text-xs text-[--color-muted]">{label}</dt>
      <dd className={`text-sm font-medium ${mono ? "font-mono text-xs" : ""}`}>
        {value}
      </dd>
    </div>
  );
}
