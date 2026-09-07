import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useApi } from "../api";
import { useAuth } from "../auth";
import {
  Card,
  Cell,
  Chip,
  Empty,
  Path,
  Problem,
  Row,
  Stat,
  Table,
  type Tone,
} from "../components/primitives";
import type { HorizonScore, InboxMessage, MemberScore, MemberWatch, WatchAlert } from "../types";

const STATE_TONE: Record<string, Tone> = {
  WATCH: "neutral",
  ELEVATED: "warn",
  CRITICAL: "fail",
  RECOVERY: "pass",
};

/** docs/09 §4 — the collections workbench.
 *
 *  A queue of members the platform is worried about, in the order acting on
 *  them is worth most. The rule the whole screen follows: nobody here has
 *  applied for anything. They are being looked at because their behaviour
 *  changed, and the only things this screen can do are ask a question and
 *  start a conversation. */
export function CollectionsPage() {
  const request = useApi();
  const [selected, setSelected] = useState<string | null>(null);

  const queue = useQuery({
    queryKey: ["watch-queue"],
    queryFn: () => request<{ count: number; alerts: WatchAlert[] }>("/api/lmi/lmi/alerts?limit=50"),
  });

  const alerts = queue.data?.alerts ?? [];
  const byState = (name: string) => alerts.filter((alert) => alert.state === name).length;
  // Exposure the platform is worried about, summed by the caller only for
  // display. Every figure under it is the service's own.
  const scored = alerts.filter((alert) => alert.p90 !== null);

  return (
    <div className="flex flex-col gap-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-[26px] font-bold tracking-tight">Collections intelligence</h1>
          <p className="text-sm text-[--color-muted]">
            Members whose behaviour changed, in the order acting on them is
            worth most.
          </p>
        </div>
      </header>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat
          label="Open alerts"
          icon="alert"
          tone={byState("CRITICAL") ? "fail" : "warn"}
          value={queue.data?.count ?? "—"}
          hint="members the engine wants somebody to call"
          testId="watch-open"
        />
        <Stat
          label="Critical"
          icon="alert"
          tone="fail"
          value={byState("CRITICAL")}
          hint="the state that will not wait"
        />
        <Stat
          label="Elevated"
          icon="clock"
          tone="warn"
          value={byState("ELEVATED")}
          hint="drifting, still recoverable"
        />
        <Stat
          label="Scored"
          icon="chart"
          tone="accent"
          value={`${scored.length} of ${alerts.length}`}
          hint="carry a ninety-day probability"
        />
      </div>

      <Card
        title="Members worth a call"
        icon="collections"
        tone="warn"
        testId="watch-queue"
        padded={false}
        right={
          queue.data ? (
            <span className="text-xs text-[--color-muted]">{queue.data.count} open</span>
          ) : null
        }
      >
        {queue.error ? (
          <div className="p-4">
            <Problem error={queue.error} />
          </div>
        ) : null}
        {queue.data && queue.data.alerts.length === 0 ? (
          <div className="p-4">
            <Empty>
              Nothing is open. The engine is watching; it has not found anybody
              it thinks somebody should call.
            </Empty>
          </div>
        ) : null}
        {queue.data && queue.data.alerts.length > 0 ? (
          <Table
            bodyTestId="watch-rows"
            head={["Member", "State", "Exposure", "p90", "Why now"]}
          >
            {queue.data.alerts.map((alert) => (
              <Row
                key={alert.alert_id}
                testId={`watch-row-${alert.member_id}`}
                selected={selected === alert.member_id}
                onClick={() => setSelected(alert.member_id)}
              >
                <Cell className="font-mono text-xs font-medium">{alert.member_id}</Cell>
                <Cell>
                  <Chip dot tone={STATE_TONE[alert.state] ?? "neutral"}>
                    {alert.state}
                  </Chip>
                </Cell>
                <Cell className="tabular-nums">{alert.exposure.toLocaleString()}</Cell>
                <Cell className="tabular-nums">
                  {alert.p90 === null ? "not scored" : alert.p90.toFixed(2)}
                </Cell>
                {/* The line, not a signal list. An officer deciding which of
                    twenty-five members to call first reads this and nothing
                    else. */}
                <Cell className="max-w-lg text-xs text-[--color-muted]">{alert.why_now}</Cell>
              </Row>
            ))}
          </Table>
        ) : null}
      </Card>

      {selected ? <MemberDrawer memberId={selected} /> : null}
    </div>
  );
}

function MemberDrawer({ memberId }: { memberId: string }) {
  const request = useApi();

  const watch = useQuery({
    queryKey: ["watch", memberId],
    queryFn: () => request<MemberWatch>(`/api/lmi/lmi/state/${memberId}`),
  });
  const score = useQuery({
    queryKey: ["watch-score", memberId],
    queryFn: () =>
      request<MemberScore>("/api/lmi/lmi/score", {
        method: "POST",
        body: { member_id: memberId },
      }),
  });

  const LADDER = ["STABLE", "WATCH", "ELEVATED", "CRITICAL", "RECOVERY"];

  return (
    <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
      {/* Where this member sits on the ladder, and where they could go. The
          transitions below say how they got here; this says what "here" is. */}
      {watch.data ? (
        <div className="lg:col-span-2">
          <Card title="Early-warning path" icon="route" tone="warn" testId="member-path">
            <Path
              testId="member-ladder"
              steps={LADDER.map((state) => ({
                label: state,
                tone: STATE_TONE[state] ?? "neutral",
                current: state === watch.data!.state,
                caption: state === watch.data!.state ? (watch.data!.since ?? "now") : undefined,
              }))}
            />
          </Card>
        </div>
      ) : null}

      <Card title="How they got here" icon="clock" tone="accent" testId="member-timeline">
        {watch.error ? <Problem error={watch.error} /> : null}
        {watch.data ? (
          <div className="flex flex-col gap-2">
            <div className="flex items-center gap-2 text-sm">
              <Chip tone={STATE_TONE[watch.data.state] ?? "neutral"} testId="member-state">
                {watch.data.state}
              </Chip>
              <span className="text-xs text-[--color-muted]">
                since {watch.data.since ?? "never changed"}
              </span>
            </div>
            {watch.data.transitions.length === 0 ? (
              <Empty>No state change is recorded for this member.</Empty>
            ) : (
              <ol className="flex flex-col gap-1 text-xs" data-testid="member-transitions">
                {watch.data.transitions.map((move) => (
                  <li key={`${move.at}-${move.to_state}`} className="flex flex-wrap gap-2">
                    <span className="text-[--color-muted]">{move.at}</span>
                    <span>
                      {move.from_state} to {move.to_state}
                    </span>
                    {/* The reason travels with the move: a label on its own is
                        not an answer to "why is this member ELEVATED". */}
                    <span className="text-[--color-muted]">{move.reason}</span>
                  </li>
                ))}
              </ol>
            )}
          </div>
        ) : null}
      </Card>

      <Card title="What the models expect" icon="chart" tone="note" testId="member-forecast">
        {score.error ? <Problem error={score.error} /> : null}
        {score.data ? (
          <div className="flex flex-col gap-3">
            {score.data.stale_days > 0 ? (
              <p className="text-xs text-[--color-warn]" data-testid="score-stale">
                {/* A score computed from a fortnight-old feature set is a score
                    about a fortnight ago. */}
                Computed from features {score.data.stale_days} days old.
              </p>
            ) : null}
            {score.data.scores.map((horizon) => (
              <HorizonBar key={horizon.horizon_days} score={horizon} />
            ))}
            <Drivers scores={score.data.scores} />
          </div>
        ) : null}
      </Card>

      <Outreach memberId={memberId} />
      <Inbox memberId={memberId} />
    </div>
  );
}

function HorizonBar({ score }: { score: HorizonScore }) {
  return (
    <div data-testid={`horizon-${score.horizon_days}`}>
      <div className="flex items-baseline justify-between text-xs">
        <span className="font-medium">{score.horizon_days} days</span>
        <span className="tabular-nums">
          {(score.probability * 100).toFixed(1)}%
          {/* The interval is on the rate among members scored alike. Quoting the
              point estimate alone claims a precision the model does not have. */}
          <span className="ml-2 text-[--color-muted]">
            {(score.interval.lower * 100).toFixed(0)}–{(score.interval.upper * 100).toFixed(0)}%
          </span>
        </span>
      </div>
      <div className="relative mt-1 h-2 w-full rounded bg-[--color-line]">
        <div
          className="absolute h-2 rounded bg-[--color-muted] opacity-40"
          style={{
            left: `${score.interval.lower * 100}%`,
            width: `${(score.interval.upper - score.interval.lower) * 100}%`,
          }}
        />
        <div
          className="absolute h-2 w-0.5 bg-[--color-warn]"
          style={{ left: `${score.probability * 100}%` }}
        />
      </div>
      {score.calibration_warning ? (
        <p className="mt-1 text-[11px] text-[--color-warn]" data-testid="calibration-warning">
          {score.calibration_warning}
        </p>
      ) : null}
    </div>
  );
}

function Drivers({ scores }: { scores: HorizonScore[] }) {
  const drivers = scores.find((score) => score.drivers.length > 0)?.drivers ?? [];
  if (drivers.length === 0) return null;
  return (
    <div>
      <h3 className="text-xs font-semibold uppercase tracking-wide text-[--color-muted]">
        What moved it
      </h3>
      <ul className="mt-1 flex flex-col gap-1 text-xs" data-testid="score-drivers">
        {drivers.map((driver) => (
          <li key={driver.feature} className="flex items-center gap-2">
            <Chip tone={driver.direction === "raises" ? "warn" : "pass"}>{driver.direction}</Chip>
            <span className="font-mono">{driver.feature}</span>
            <span className="text-[--color-muted]">{driver.value}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** docs/09 §4 — the outreach draft editor.
 *
 *  The officer writes the message, reads it, and sends it. Nothing here
 *  composes prose on their behalf: a message to a member about money is a
 *  thing the cooperative said, and it has to be a thing a person chose. */
function Outreach({ memberId }: { memberId: string }) {
  const request = useApi();
  const queryClient = useQueryClient();
  const { session } = useAuth();

  const [message, setMessage] = useState(
    "We noticed your instalments have been arriving a little later than usual and wanted to check everything is alright.",
  );

  const send = useMutation({
    mutationFn: async () => {
      const drafted = await request<{ message_id: string }>("/api/notification/notifications", {
        method: "POST",
        body: {
          member_id: memberId,
          template_id: "OFFICER_OUTREACH",
          channel: "SMS",
          variables: {
            member_name: "the member",
            message,
            officer_name: session?.role ?? "an officer",
            cooperative_name: "the cooperative",
          },
        },
      });
      return request<{ state: string }>(
        `/api/notification/notifications/${drafted.message_id}/approve`,
        { method: "POST", body: { actor_id: `${session?.role ?? "officer"}-demo` } },
      );
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["inbox", memberId] });
    },
  });

  return (
    <Card title="Say something" testId="outreach">
      <div className="flex flex-col gap-2">
        <textarea
          data-testid="outreach-body"
          rows={4}
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          className="rounded border border-[--color-line] bg-[--color-surface] p-2 text-xs"
        />
        {send.error ? <Problem error={send.error} /> : null}
        {send.data ? (
          <p className="text-xs text-[--color-pass]" data-testid="outreach-sent">
            Sent, and it is in the member's inbox below.
          </p>
        ) : null}
        <div className="flex items-center gap-3">
          <button
            type="button"
            data-testid="outreach-send"
            disabled={send.isPending || message.trim().length < 10}
            onClick={() => send.mutate()}
            className="rounded border border-[--color-accent] px-3 py-1.5 text-sm disabled:cursor-not-allowed disabled:opacity-50"
          >
            {send.isPending ? "Sending" : "Approve and send"}
          </button>
          <p className="text-xs text-[--color-muted]">
            {/* Said on the screen because it is the constraint that makes this
                page safe: this member has not applied for anything. */}
            This member has not applied for anything. Nothing here changes their
            facility.
          </p>
        </div>
      </div>
    </Card>
  );
}

function Inbox({ memberId }: { memberId: string }) {
  const request = useApi();
  const inbox = useQuery({
    queryKey: ["inbox", memberId],
    queryFn: () =>
      request<{ count: number; messages: InboxMessage[] }>(
        `/api/notification/members/${memberId}/inbox`,
      ),
  });

  return (
    <Card title="What they have been sent" testId="member-inbox">
      {inbox.error ? <Problem error={inbox.error} /> : null}
      {inbox.data && inbox.data.messages.length === 0 ? (
        <Empty>Nothing has been sent to this member.</Empty>
      ) : null}
      {inbox.data ? (
        <ul className="flex flex-col gap-2 text-xs" data-testid="inbox-messages">
          {inbox.data.messages.map((entry) => (
            <li
              key={entry.message_id}
              className="rounded border border-[--color-line] px-2 py-1"
              data-testid={`inbox-${entry.state}`}
            >
              <div className="flex flex-wrap items-center gap-2">
                <Chip tone={entry.state === "SENT" ? "pass" : "neutral"}>{entry.state}</Chip>
                <span className="font-mono">{entry.template_id}</span>
                <span className="text-[--color-muted]">{entry.language}</span>
                <span className="ml-auto text-[--color-muted]">
                  {entry.sent_at ?? entry.scheduled_at ?? ""}
                </span>
              </div>
              <p className="mt-1 whitespace-pre-line">{entry.body}</p>
              {entry.cancel_reason ? (
                <p className="mt-1 text-[--color-muted]">
                  Not sent: {entry.cancel_reason}
                </p>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
    </Card>
  );
}
